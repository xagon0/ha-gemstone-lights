"""Data coordinator for Gemstone Lights.

State and commands prefer the controller's local HTTP API and fall back to
Gemstone's cloud. The cloud is still required for things the controller does
not serve: account discovery, saved designs, zone definitions and the pattern
catalog.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .api import GemstoneApi, GemstoneAuthError, GemstoneError
from .commands import serialized
from .const import (
    CATALOG_REFRESH_INTERVAL,
    LIBRARY_REFRESH_INTERVAL,
    LOCAL_RETRY_BACKOFF,
    LOCAL_WRITE_GAP,
    DATA_DESIGNS,
    DATA_DEVICES,
    DATA_INFO,
    DATA_LOCAL,
    DATA_PATTERNS,
    DATA_SETTINGS,
    DATA_STATE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SPEED,
    DOMAIN,
    EFFECT_SOLID,
)
from .local_api import GemstoneLocalApi, GemstoneLocalError
from .state import same_content, scale_color

_LOGGER = logging.getLogger(__name__)


class GemstoneCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll controller state and cache the design/pattern catalog."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: GemstoneApi,
        *,
        host_override: str | None = None,
        prefer_local: bool = True,
        enable_local: bool = True,
        enable_library: bool = True,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
            config_entry=entry,
        )
        self.api = api
        self._host_override = host_override
        self._prefer_local = prefer_local
        self._enable_local = enable_local
        self._enable_library = enable_library
        self._enable_attempted: set[str] = set()
        self._known_devices: list[dict[str, Any]] = []
        self._discovery_next: datetime | None = None
        self._cloud_available = True
        self._store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self._saved_cache: dict[str, Any] | None = None

        # Gemstone's official pattern library, keyed by folder id.
        self._library_folders: dict[str, dict[str, Any]] = {}
        self._library: dict[str, list[dict[str, Any]]] = {}
        self._library_refreshed: datetime | None = None
        self._selected_folder: dict[str, str] = {}

        self._device_ids: list[str] = []
        self._designs: dict[str, list[dict[str, Any]]] = {}
        self._patterns: list[dict[str, Any]] = []
        self._zones: dict[str, list[dict[str, Any]]] = {}
        self._catalog_refreshed: datetime | None = None
        self._speeds: dict[str, int] = {}

        # The controller drops writes that arrive too close together.
        self._write_locks: dict[str, asyncio.Lock] = {}
        self._write_owners: dict[str, asyncio.Task] = {}
        self._last_write: dict[str, float] = {}
        self._pending_states: dict[str, tuple[float, dict[str, Any]]] = {}
        self._state_aliases: dict[str, dict[str, Any]] = {}
        self._state_versions: dict[str, int] = {}
        self._refresh_cancel = None
        self._local: dict[str, GemstoneLocalApi] = {}
        self._local_ok: dict[str, bool] = {}
        # Don't retry a controller we can't reach on every single poll.
        self._local_retry_after: dict[str, datetime] = {}
        self._settings: dict[str, dict[str, Any]] = {}

    async def _async_setup(self) -> None:
        """Restore discovery before attempting any cloud authentication."""
        cached = await self._store.async_load()
        if not isinstance(cached, dict):
            return
        devices = cached.get("devices", [])
        if not isinstance(devices, list) or any(not isinstance(d, dict) or not d.get("id") for d in devices):
            return
        self._known_devices = devices
        self._device_ids = [d["id"] for d in devices]
        for key in ("zones", "designs", "library", "library_folders", "speeds", "selected_folder", "state_aliases"):
            if isinstance(cached.get(key), dict):
                setattr(self, f"_{key}", cached[key])
        if isinstance(cached.get("patterns"), list):
            self._patterns = cached["patterns"]
        if devices:
            # The first refresh can complete locally, even if Cognito is down.
            self._discovery_next = dt_util.utcnow() + DEFAULT_SCAN_INTERVAL
            self._catalog_refreshed = dt_util.utcnow()
            self._library_refreshed = dt_util.utcnow()

    async def _async_save_cache(self) -> None:
        """Persist discovery and catalogs, never passwords or session tokens."""
        devices = []
        for device in self._known_devices:
            info = {k: device[k] for k in ("id", "name", "online", "firmware") if k in device}
            hub = device.get("hub") or {}
            info["hub"] = {k: hub[k] for k in ("localIp", "tcpEnabled", "pixelCount", "outputNames", "rgbwSequence") if k in hub}
            devices.append(info)
        cached = deepcopy({
            "devices": devices, "zones": self._zones, "designs": self._designs,
            "patterns": self._patterns, "library": self._library,
            "library_folders": self._library_folders, "speeds": self._speeds,
            "selected_folder": self._selected_folder,
            "state_aliases": self._state_aliases,
        })
        if cached != self._saved_cache:
            await self._store.async_save(cached)
            self._saved_cache = cached

    # -- basics -------------------------------------------------------------

    @asynccontextmanager
    async def _device_lock(self, device_id: str):
        """Serialize each device while allowing nested commands in the same task."""
        task = asyncio.current_task()
        if self._write_owners.get(device_id) is task:
            yield
            return
        lock = self._write_locks.setdefault(device_id, asyncio.Lock())
        async with lock:
            self._write_owners[device_id] = task
            try:
                yield
            finally:
                self._write_owners.pop(device_id, None)

    async def async_shutdown(self) -> None:
        """Cancel delayed command refreshes on unload."""
        if self._refresh_cancel:
            self._refresh_cancel()
            self._refresh_cancel = None
        await super().async_shutdown()

    def _publish_command(self, device_id: str, state: dict[str, Any], wire_state: dict[str, Any] | None = None) -> None:
        """Publish a successful command immediately and protect it from stale echoes."""
        self._state_versions[device_id] = self._state_versions.get(device_id, 0) + 1
        self._pending_states[device_id] = (monotonic() + 5, deepcopy(state))
        self._state_aliases[device_id] = {"logical": deepcopy(state), "wire": deepcopy(wire_state or state)}
        data = dict(self.data or {})
        records = dict(data.get(DATA_DEVICES, {}))
        records[device_id] = {**records.get(device_id, {}), DATA_STATE: deepcopy(state), "available": True}
        data[DATA_DEVICES] = records
        self.async_set_updated_data(data)
        if self._refresh_cancel:
            self._refresh_cancel()

        async def refresh(_now):
            self._refresh_cancel = None
            await self.async_request_refresh()

        self._refresh_cancel = async_call_later(self.hass, 2, refresh)

    @property
    def device_ids(self) -> list[str]:
        """Return the known controller ids."""
        return self._device_ids

    def zones(self, device_id: str) -> list[dict[str, Any]]:
        """Return the zones configured on a controller."""
        return self._zones.get(device_id, [])

    def device_info_raw(self, device_id: str) -> dict[str, Any]:
        """Return the raw controller record from the cloud."""
        return (self.data or {}).get(DATA_DEVICES, {}).get(device_id, {}).get(
            DATA_INFO, {}
        )

    def device_state(self, device_id: str) -> dict[str, Any]:
        """Return the controller's currently-playing state."""
        return (self.data or {}).get(DATA_DEVICES, {}).get(device_id, {}).get(
            DATA_STATE, {}
        )

    def designs(self, device_id: str) -> list[dict[str, Any]]:
        """Return saved designs for a controller."""
        return (self.data or {}).get(DATA_DESIGNS, {}).get(device_id, [])

    def patterns(self) -> list[dict[str, Any]]:
        """Return the account's patterns."""
        return (self.data or {}).get(DATA_PATTERNS, [])

    def settings(self, device_id: str) -> dict[str, Any]:
        """Return hub settings read locally (empty when cloud-only)."""
        return self._settings.get(device_id, {})

    def is_local(self, device_id: str) -> bool:
        """Return True when this controller is being driven over the LAN."""
        return bool(self._local_ok.get(device_id))

    def local_host(self, device_id: str) -> str | None:
        """Return the controller's local address, if known."""
        client = self._local.get(device_id)
        return client.host if client else None

    # -- transport ----------------------------------------------------------

    def _local_client(
        self, device_id: str, info: dict[str, Any]
    ) -> GemstoneLocalApi | None:
        """Return the LAN client for a controller, if local control applies.

        The address comes from the controller's own cloud record, so nothing
        has to be configured by hand. The record also says whether "Allow
        Local Commands" is switched on, which saves pointless connections.
        """
        if not self._prefer_local:
            return None

        hub = info.get("hub") or {}
        host = self._host_override or hub.get("localIp")
        if not host:
            return None

        # Respect the app's own switch, unless an address was pinned by hand.
        if not self._host_override and hub.get("tcpEnabled") is False:
            if self._enable_local and device_id not in self._enable_attempted:
                # The switch can be flipped from the cloud, so offer to do it
                # rather than making the user go into the app.
                self._enable_attempted.add(device_id)
                self.hass.async_create_task(self._async_enable_local(device_id))
            elif self._local_ok.get(device_id) is not False:
                _LOGGER.info(
                    "Gemstone %s: 'Allow Local Commands' is off, using cloud",
                    device_id,
                )
            self._local_ok[device_id] = False
            return None

        # Back off after a failure rather than stalling every poll.
        retry_after = self._local_retry_after.get(device_id)
        if retry_after and dt_util.utcnow() < retry_after:
            return None

        existing = self._local.get(device_id)
        if existing is not None and existing.host == host:
            return existing

        if existing is not None:
            _LOGGER.info(
                "Gemstone %s: local address changed from %s to %s",
                device_id,
                existing.host,
                host,
            )
        client = GemstoneLocalApi(async_get_clientsession(self.hass), host)
        self._local[device_id] = client
        return client

    async def _async_enable_local(self, device_id: str) -> None:
        """Switch on the controller's local commands via the cloud."""
        try:
            await self.api.async_set_local_enabled(device_id, True)
        except GemstoneError as err:
            _LOGGER.warning(
                "Gemstone %s: could not enable local control (%s); "
                "turn on 'Allow Local Commands' in the Gemstone app",
                device_id,
                err,
            )
            return
        _LOGGER.info(
            "Gemstone %s: enabled local control on the controller", device_id
        )

    # -- polling ------------------------------------------------------------

    async def _async_discover(self) -> list[dict[str, Any]]:
        """Find every controller across all homegroups on the account."""
        devices: list[dict[str, Any]] = []
        for homegroup in await self.api.async_get_homegroups():
            homegroup_id = homegroup.get("id")
            if not homegroup_id:
                continue
            devices.extend(await self.api.async_get_devices(homegroup_id))
        return devices

    async def _async_refresh_catalog(self, device_ids: list[str]) -> None:
        """Reload saved designs, zones and patterns (cloud only)."""
        for device_id in device_ids:
            try:
                self._designs[device_id] = await self.api.async_get_designs(device_id)
                self._zones[device_id] = await self.api.async_get_zones(device_id)
            except GemstoneError as err:
                _LOGGER.debug("Could not load catalog for %s: %s", device_id, err)

        patterns: list[dict[str, Any]] = []
        try:
            for folder in await self.api.async_get_folders():
                folder_id = folder.get("folderId")
                if not folder_id:
                    continue
                for item in await self.api.async_get_folder_patterns(folder_id):
                    data = item.get("patternData")
                    if not data or not data.get("name"):
                        continue
                    patterns.append(
                        {
                            "folder": folder.get("name") or "",
                            "name": data["name"],
                            "data": data,
                        }
                    )
        except GemstoneError as err:
            _LOGGER.debug("Could not load patterns: %s", err)

        if patterns:
            self._patterns = patterns
        self._catalog_refreshed = dt_util.utcnow()

    async def _async_state_for(
        self, device_id: str, info: dict[str, Any]
    ) -> dict[str, Any]:
        """Read state locally when possible, otherwise from the cloud."""
        client = self._local_client(device_id, info)
        if client:
            try:
                state = await client.async_get_state()
                if not self._local_ok.get(device_id):
                    _LOGGER.info(
                        "Gemstone %s: using local control at %s", device_id, client.host
                    )
                self._local_ok[device_id] = True
                self._local_retry_after.pop(device_id, None)
                try:
                    self._settings[device_id] = await client.async_get_settings()
                except GemstoneLocalError:
                    pass
                return state
            except GemstoneLocalError as err:
                if self._local_ok.get(device_id) is not False:
                    _LOGGER.warning(
                        "Gemstone %s: local control unavailable (%s), using cloud",
                        device_id,
                        err,
                    )
                self._local_ok[device_id] = False
                self._local_retry_after[device_id] = dt_util.utcnow() + LOCAL_RETRY_BACKOFF

        return await self.api.async_get_state(device_id)

    def device_available(self, device_id: str) -> bool:
        """Use successful state reads, not the cloud's stale online flag."""
        record = (self.data or {}).get(DATA_DEVICES, {}).get(device_id, {})
        return record.get("available", bool(record.get(DATA_STATE)))

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch controller state (and the catalog when due)."""
        try:
            now = dt_util.utcnow()
            if self._discovery_next is None or now >= self._discovery_next:
                self._discovery_next = now + timedelta(minutes=1)
                try:
                    discovered = await self._async_discover()
                except GemstoneError:
                    self._cloud_available = False
                    if not self._known_devices:
                        raise
                    _LOGGER.debug("Cloud discovery unavailable; using cached controllers")
                else:
                    self._known_devices = list({d["id"]: d for d in discovered if d.get("id")}.values())
                    self._cloud_available = True
                    self._discovery_next = now + timedelta(minutes=5)
            devices = self._known_devices
            self._device_ids = [d["id"] for d in devices if d.get("id")]

            due = (
                self._catalog_refreshed is None
                or dt_util.utcnow() - self._catalog_refreshed > CATALOG_REFRESH_INTERVAL
            )
            if due and self._cloud_available:
                await self._async_refresh_catalog(self._device_ids)

            library_due = self._enable_library and (
                self._library_refreshed is None
                or dt_util.utcnow() - self._library_refreshed
                > LIBRARY_REFRESH_INTERVAL
            )
            if library_due and self._cloud_available:
                await self._async_refresh_library()

            result: dict[str, Any] = {DATA_DEVICES: {}}
            versions = dict(self._state_versions)
            for device in devices:
                device_id = device.get("id")
                if not device_id:
                    continue
                available = True
                try:
                    async with self._device_lock(device_id):
                        state = await self._async_state_for(device_id, device)
                        pending = self._pending_states.get(device_id)
                        if pending and monotonic() < pending[0]:
                            state = deepcopy(pending[1])
                        else:
                            self._pending_states.pop(device_id, None)
                            alias = self._state_aliases.get(device_id)
                            if alias and same_content(state, alias["wire"]):
                                state = {**deepcopy(alias["logical"]), "onState": state["onState"]}
                            else:
                                self._state_aliases.pop(device_id, None)
                except GemstoneAuthError:
                    raise
                except GemstoneError as err:
                    _LOGGER.debug("State fetch failed for %s: %s", device_id, err)
                    state = self.device_state(device_id)
                    available = False
                result[DATA_DEVICES][device_id] = {
                    DATA_INFO: device,
                    DATA_STATE: state,
                    "available": available,
                    DATA_LOCAL: self.is_local(device_id),
                    DATA_SETTINGS: self._settings.get(device_id, {}),
                }

            result[DATA_DESIGNS] = self._designs
            result[DATA_PATTERNS] = self._patterns
            await self._async_save_cache()
            for device_id in result[DATA_DEVICES]:
                if self._state_versions.get(device_id, 0) != versions.get(device_id, 0):
                    result[DATA_DEVICES][device_id] = self.data[DATA_DEVICES][device_id]
            return result

        except GemstoneAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GemstoneError as err:
            raise UpdateFailed(str(err)) from err

    # -- pattern construction ----------------------------------------------

    def speed(self, device_id: str) -> int:
        """Return the speed to use when building patterns."""
        return self._speeds.get(device_id, DEFAULT_SPEED)

    def set_speed(self, device_id: str, value: int) -> None:
        """Remember the desired animation speed."""
        self._speeds[device_id] = max(0, min(255, int(value)))

    def build_pattern(
        self,
        device_id: str,
        colors: list[int],
        animation: str,
        *,
        name: str | None = None,
        brightness: int = 255,
    ) -> dict[str, Any]:
        """Build a pattern payload the controller accepts.

        The controller plays any well-formed pattern, saved or not, which is
        what makes free-form effect selection possible.
        """
        return {
            "id": str(uuid.uuid4()),
            "name": name or animation.replace("_", " ").title(),
            "colors": colors or [0xFFFFFF],
            "animation": "motionless" if animation == EFFECT_SOLID else animation,
            "speed": self.speed(device_id),
            "brightness": max(1, min(255, brightness)),
            "direction": 0,
            "backgroundColor": 0,
        }

    # -- commands (local first, cloud fallback) ----------------------------

    async def _async_command(self, device_id: str, local_coro, cloud_coro, state: dict[str, Any], local_state: dict[str, Any] | None = None) -> None:
        """Run a command locally when possible, else via the cloud.

        Writes are serialised and spaced: the controller silently ignores
        commands that arrive back to back.
        """
        delay = LOCAL_WRITE_GAP - (monotonic() - self._last_write.get(device_id, 0))
        if delay > 0:
            await asyncio.sleep(delay)
        if local_coro is not None and self.is_local(device_id):
            try:
                await local_coro()
                self._last_write[device_id] = monotonic()
                self._publish_command(device_id, state, local_state)
                await self._async_save_cache()
                return
            except GemstoneLocalError as err:
                _LOGGER.warning(
                    "Gemstone %s: local command failed (%s), retrying via cloud",
                    device_id,
                    err,
                )
                self._local_ok[device_id] = False
                self._local_retry_after[device_id] = (
                    dt_util.utcnow() + LOCAL_RETRY_BACKOFF
                )

        await cloud_coro()
        self._last_write[device_id] = monotonic()
        self._publish_command(device_id, state)
        await self._async_save_cache()

    @serialized
    async def async_set_power(self, device_id: str, on: bool) -> None:
        """Turn a controller on or off."""
        client = self._local.get(device_id)
        await self._async_command(
            device_id,
            (lambda: client.async_set_power(on)) if client else None,
            lambda: self.api.async_set_power(device_id, on),
            {**self.device_state(device_id), "onState": on},
        )

    @serialized
    async def async_play_color(
        self, device_id: str, packed: int, brightness: int
    ) -> None:
        """Show one solid colour.

        Locally the controller takes brightness as its own field. The cloud
        has no equivalent for solid colours, so brightness is folded into the
        colour there.
        """
        client = self._local.get(device_id)

        def _cloud() -> Any:
            return self.api.async_play_color(device_id, scale_color(packed, brightness))

        await self._async_command(
            device_id,
            (
                lambda: client.async_play(
                    {
                        "onState": True,
                        "colorB": {"value": packed, "brightness": brightness},
                    }
                )
            )
            if client
            else None,
            _cloud,
            {"onState": True, "colorB": {"value": packed, "brightness": brightness}},
        )

    @serialized
    async def async_play_pattern(self, device_id: str, pattern: dict[str, Any]) -> None:
        """Play a pattern."""
        client = self._local.get(device_id)
        await self._async_command(
            device_id,
            (lambda: client.async_play({"onState": True, "pattern": pattern}))
            if client
            else None,
            lambda: self.api.async_play_pattern(device_id, pattern),
            {"onState": True, "pattern": deepcopy(pattern)},
        )

    @serialized
    async def async_play_design(self, device_id: str, design: dict[str, Any]) -> None:
        """Play an architectural design."""
        client = self._local.get(device_id)
        payload = {**design, "preview": False}
        await self._async_command(
            device_id,
            (lambda: client.async_play({"onState": True, "architectural": payload}))
            if client and not payload.get("zonePatterns")
            else None,
            lambda: self.api.async_play_design(device_id, design),
            {"onState": True, "architectural": deepcopy(payload)},
        )

    @serialized
    async def async_set_brightness(self, device_id: str, brightness: int) -> bool:
        """Dim or brighten whatever is playing without replacing it.

        A pattern or design carries its own brightness, so the same content is
        sent again with just that field changed. This keeps every colour of a
        multi-colour pattern and every zone of a design intact, which building
        a fresh single-colour pattern would not. Returns False when a solid
        colour (or nothing) is playing, since that path is handled elsewhere.
        """
        state = self.device_state(device_id)
        brightness = max(1, min(255, int(brightness)))

        if pattern := state.get("pattern"):
            await self.async_play_pattern(
                device_id, {**pattern, "brightness": brightness}
            )
            return True

        if design := state.get("architectural"):
            updated = {**design, "brightness": brightness}
            # Zone designs keep a brightness per zone pattern as well.
            if zone_patterns := design.get("zonePatterns"):
                updated["zonePatterns"] = [
                    {
                        **entry,
                        "pattern": {
                            **(entry.get("pattern") or {}),
                            "brightness": brightness,
                        },
                    }
                    for entry in zone_patterns
                ]
            await self.async_play_design(device_id, updated)
            return True

        return False


    # -- Gemstone's official pattern library ---------------------------------

    async def _async_refresh_library(self) -> None:
        """Load the official library: ~1700 patterns across ~68 folders."""
        try:
            folders = await self.api.async_get_library_folders()
            patterns = await self.api.async_get_library_patterns()
        except GemstoneError as err:
            _LOGGER.debug("Could not load the pattern library: %s", err)
            return

        self._library_folders = {f["id"]: f for f in folders if f.get("id")}
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in patterns:
            folder_id = item.get("downloadableFolderId")
            data = item.get("patternData")
            name = item.get("patternName")
            if not folder_id or not data or not name:
                continue
            grouped.setdefault(folder_id, []).append({"name": name, "data": data})

        for entries in grouped.values():
            entries.sort(key=lambda e: e["name"])
        self._library = grouped
        self._library_refreshed = dt_util.utcnow()
        _LOGGER.debug(
            "Loaded %s library patterns across %s folders",
            sum(len(v) for v in grouped.values()),
            len(grouped),
        )

    def _folder_label(self, folder_id: str) -> str:
        """Return a readable "category / folder" label."""
        folder = self._library_folders.get(folder_id, {})
        name = folder.get("folderName") or "Unknown"
        category = (folder.get("category") or "other").replace("-", " ")
        return f"{category} / {name}"

    def library_folder_options(self) -> list[str]:
        """Return every library folder, grouped by category."""
        labels = [
            self._folder_label(folder_id)
            for folder_id in self._library
            if self._library.get(folder_id)
        ]
        return sorted(labels)

    def selected_folder(self, device_id: str) -> str | None:
        """Return the library folder currently being browsed."""
        return self._selected_folder.get(device_id)

    def set_selected_folder(self, device_id: str, label: str) -> None:
        """Remember which library folder is being browsed."""
        self._selected_folder[device_id] = label

    def library_pattern_options(self, device_id: str) -> list[str]:
        """Return the patterns inside the folder being browsed."""
        label = self._selected_folder.get(device_id)
        if not label:
            return []
        for folder_id, entries in self._library.items():
            if self._folder_label(folder_id) == label:
                return [e["name"] for e in entries]
        return []

    def find_library_pattern(
        self, name: str, folder_label: str | None = None
    ) -> dict[str, Any] | None:
        """Find a library pattern by name, optionally within one folder."""
        wanted = name.strip().casefold()
        for folder_id, entries in self._library.items():
            if folder_label and self._folder_label(folder_id) != folder_label:
                continue
            for entry in entries:
                if entry["name"].casefold() == wanted:
                    return entry["data"]
        return None

    def library_pattern_names(self) -> list[str]:
        """Return every library pattern name (used for error hints)."""
        return [e["name"] for entries in self._library.values() for e in entries]

    def library_size(self) -> int:
        """Return how many library patterns are loaded."""
        return sum(len(v) for v in self._library.values())

    # -- zones ---------------------------------------------------------------

    def zone_ranges(self, device_id: str) -> dict[str, tuple[int, int]]:
        """Return each zone's inclusive pixel range, keyed by zone id.

        The cloud describes a zone as ``lights: [n, start, end]``.
        """
        ranges: dict[str, tuple[int, int]] = {}
        for zone in self.zones(device_id):
            lights = zone.get("lights") or []
            if not isinstance(lights, list) or not zone.get("id") or len(lights) < 3:
                continue
            try:
                start, end = int(lights[-2]), int(lights[-1])
            except (ValueError, TypeError):
                continue
            if 0 <= start <= end < 65536:
                ranges[zone["id"]] = (start, end)
        return ranges

    def zone_states(self, device_id: str) -> dict[str, dict[str, Any]]:
        """Return what each zone is showing.

        Cloud-applied designs carry ``zonePatterns``; the controller itself
        only reports ``staticColors``, so those are mapped back onto zones by
        matching pixel ranges.
        """
        design = self.device_state(device_id).get("architectural") or {}

        if not design:
            return {
                zone_id: {
                    "color": (entry["pattern"].get("colors") or [0])[0],
                    "brightness": entry["pattern"].get("brightness", 255),
                    "animation": entry["pattern"].get("animation", EFFECT_SOLID),
                }
                for zone_id, entry in self._zone_entries(device_id).items()
            }

        if zone_patterns := design.get("zonePatterns"):
            result: dict[str, dict[str, Any]] = {}
            for entry in zone_patterns:
                zone_id = entry.get("zoneId")
                pattern = entry.get("pattern") or {}
                colors = pattern.get("colors") or []
                if zone_id:
                    result[zone_id] = {
                        "color": colors[0] if colors else 0,
                        "brightness": pattern.get("brightness", 255),
                        "animation": pattern.get("animation") or EFFECT_SOLID,
                    }
            return result

        static = design.get("staticColors") or []
        if not static:
            return {}

        result = {}
        brightness = design.get("brightness", 255)
        for zone_id, (start, end) in self.zone_ranges(device_id).items():
            for entry in static:
                lights = entry.get("lights") or []
                if not lights or entry.get("color") in (None, 0):
                    continue
                covered = set(lights)
                if all(pixel in covered for pixel in range(start, end + 1)):
                    result[zone_id] = {
                        "color": entry["color"],
                        "brightness": brightness,
                        "animation": EFFECT_SOLID,
                    }
                    break
        return result

    def _zone_entries(self, device_id: str) -> dict[str, dict[str, Any]]:
        """Keep complete vendor patterns and entry metadata for neighboring zones."""
        design = self.device_state(device_id).get("architectural") or {}
        if not design:
            state = self.device_state(device_id)
            pattern = state.get("pattern")
            if not pattern:
                color_b = state.get("colorB") or {}
                color = color_b.get("value", state.get("color"))
                if color is None:
                    return {}
                pattern = self.build_pattern(
                    device_id, [color], EFFECT_SOLID,
                    brightness=color_b.get("brightness", 255),
                )
            return {
                zone["id"]: {"zoneId": zone["id"], "pattern": deepcopy(pattern)}
                for zone in self.zones(device_id) if zone.get("id")
            }
        if "zonePatterns" in design:
            return {
                entry["zoneId"]: deepcopy(entry)
                for entry in design["zonePatterns"]
                if entry.get("zoneId") and isinstance(entry.get("pattern"), dict)
            }
        return {
            zone_id: {
                "zoneId": zone_id,
                "pattern": self.build_pattern(
                    device_id, [spec["color"]], spec["animation"],
                    brightness=spec["brightness"],
                ),
            }
            for zone_id, spec in self.zone_states(device_id).items()
        }

    async def _async_play_zones(
        self, device_id: str, entries: dict[str, dict[str, Any]]
    ) -> None:
        """Send the full zone layout, translating only supported solid layouts."""
        if not entries:
            await self.async_set_power(device_id, False)
            return
        design = {
            "id": str(uuid.uuid4()), "name": "Home Assistant Zones",
            "brightness": 255, "preview": False,
            "zonePatterns": list(entries.values()),
        }
        ranges = self.zone_ranges(device_id)
        patterns = {zid: entry["pattern"] for zid, entry in entries.items()}
        can_translate = all(
            zid in ranges and len(pattern.get("colors") or []) == 1
            and pattern.get("animation") in (EFFECT_SOLID, "motionless")
            for zid, pattern in patterns.items()
        )
        client = self._local.get(device_id)
        local_design = None
        if can_translate:
            local_design = {
                "id": design["id"], "name": design["name"], "preview": False,
                "brightness": 255,
                "staticColors": [
                    {"lights": list(range(ranges[zid][0], ranges[zid][1] + 1)),
                     "color": scale_color(pattern["colors"][0], pattern.get("brightness", 255))}
                    for zid, pattern in patterns.items()
                ],
            }
        await self._async_command(
            device_id,
            (lambda: client.async_play({"onState": True, "architectural": local_design}))
            if client and local_design is not None else None,
            lambda: self.api.async_play_design(device_id, design),
            {"onState": True, "architectural": design},
            {"onState": True, "architectural": local_design} if local_design else None,
        )

    @serialized
    async def async_set_zone(
        self, device_id: str, zone_id: str, spec: dict[str, Any] | None
    ) -> None:
        """Apply only the requested fields, preserving every other zone's content."""
        if zone_id not in {z.get("id") for z in self.zones(device_id)}:
            raise HomeAssistantError("This zone no longer exists")
        desired = self._zone_entries(device_id)
        if not self.device_state(device_id).get("onState") and spec is not None:
            desired = {zone_id: desired[zone_id]} if zone_id in desired else {}
        if spec is None:
            desired.pop(zone_id, None)
        else:
            entry = desired.get(zone_id, {"zoneId": zone_id, "pattern": self.build_pattern(device_id, [0xFFFFFF], EFFECT_SOLID)})
            pattern = entry["pattern"]
            if "color" in spec:
                pattern["colors"] = [spec["color"]]
            if "brightness" in spec:
                pattern["brightness"] = spec["brightness"]
            if "animation" in spec:
                pattern["animation"] = "motionless" if spec["animation"] == EFFECT_SOLID else spec["animation"]
            desired[zone_id] = entry
        await self._async_play_zones(device_id, desired)
