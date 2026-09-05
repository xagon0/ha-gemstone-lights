"""Data coordinator for Gemstone Lights."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import GemstoneApi, GemstoneAuthError, GemstoneError
from .const import (
    CATALOG_REFRESH_INTERVAL,
    DEFAULT_SPEED,
    EFFECT_SOLID,
    DATA_DESIGNS,
    DATA_DEVICES,
    DATA_INFO,
    DATA_PATTERNS,
    DATA_STATE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class GemstoneCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll controller state and cache the design/pattern catalog.

    Live state is cheap and polled every update. Saved designs and patterns
    change rarely, so they are refreshed on a slower cadence.
    """

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, api: GemstoneApi
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
        self._device_ids: list[str] = []
        self._designs: dict[str, list[dict[str, Any]]] = {}
        self._patterns: list[dict[str, Any]] = []
        self._zones: dict[str, list[dict[str, Any]]] = {}
        self._catalog_refreshed: datetime | None = None
        # Speed is not reported by the cloud, so remember what was asked for.
        self._speeds: dict[str, int] = {}

    @property
    def device_ids(self) -> list[str]:
        """Return the known controller ids."""
        return self._device_ids

    def zones(self, device_id: str) -> list[dict[str, Any]]:
        """Return the zones configured on a controller."""
        return self._zones.get(device_id, [])

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
        """Reload saved designs, zones and patterns."""
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

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch controller state (and the catalog when due)."""
        try:
            devices = await self._async_discover()
            self._device_ids = [d["id"] for d in devices if d.get("id")]

            due = (
                self._catalog_refreshed is None
                or dt_util.utcnow() - self._catalog_refreshed > CATALOG_REFRESH_INTERVAL
            )
            if due:
                await self._async_refresh_catalog(self._device_ids)

            result: dict[str, Any] = {DATA_DEVICES: {}}
            for device in devices:
                device_id = device.get("id")
                if not device_id:
                    continue
                try:
                    state = await self.api.async_get_state(device_id)
                except GemstoneError as err:
                    _LOGGER.debug("State fetch failed for %s: %s", device_id, err)
                    state = {}
                result[DATA_DEVICES][device_id] = {
                    DATA_INFO: device,
                    DATA_STATE: state,
                }

            result[DATA_DESIGNS] = self._designs
            result[DATA_PATTERNS] = self._patterns
            return result

        except GemstoneAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GemstoneError as err:
            raise UpdateFailed(str(err)) from err

    # -- helpers used by entities ------------------------------------------

    def device_info_raw(self, device_id: str) -> dict[str, Any]:
        """Return the raw controller record."""
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
        name: str = "Home Assistant",
        brightness: int = 255,
    ) -> dict[str, Any]:
        """Build a pattern payload the controller accepts.

        The controller happily plays patterns that were never saved to the
        account, which is what makes free-form effect selection possible.
        """
        return {
            "id": str(uuid.uuid4()),
            "name": name,
            "colors": colors or [0xFFFFFF],
            "animation": EFFECT_SOLID if animation == EFFECT_SOLID else animation,
            "speed": self.speed(device_id),
            "brightness": max(1, min(255, brightness)),
            "direction": 0,
            "backgroundColor": 0,
        }

    # -- per-zone (architectural) state -------------------------------------

    def zone_patterns(self, device_id: str) -> dict[str, dict[str, Any]]:
        """Return the pattern currently applied to each zone, keyed by zone id."""
        design = self.device_state(device_id).get("architectural") or {}
        return {
            entry["zoneId"]: entry.get("pattern") or {}
            for entry in design.get("zonePatterns") or []
            if entry.get("zoneId")
        }

    async def async_set_zone_pattern(
        self, device_id: str, zone_id: str, pattern: dict[str, Any] | None
    ) -> None:
        """Set or clear one zone, preserving whatever the other zones show.

        The controller replaces the whole design on every call, so the full
        picture has to be sent each time.
        """
        current = self.zone_patterns(device_id)
        if pattern is None:
            current.pop(zone_id, None)
        else:
            current[zone_id] = pattern

        if not current:
            await self.async_apply(self.api.async_set_power(device_id, False))
            return

        design = {
            "id": str(uuid.uuid4()),
            "name": "Home Assistant Zones",
            "brightness": 255,
            "zonePatterns": [
                {"zoneId": zid, "pattern": pat} for zid, pat in current.items()
            ],
        }
        await self.async_apply(self.api.async_play_design(device_id, design))

    async def async_apply(self, coro) -> None:
        """Run a control call then refresh state shortly after.

        The cloud needs a moment to reflect a change, so this waits briefly
        before asking for the new state.
        """
        await coro
        await asyncio.sleep(2)
        await self.async_request_refresh()
