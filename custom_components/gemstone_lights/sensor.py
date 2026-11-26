"""Sensor platform for Gemstone Lights."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from . import GemstoneConfigEntry
from .color_util import unpack
from .coordinator import GemstoneCoordinator
from .entity import GemstoneEntity, async_add_discovered_entities
from .playback import playlist_step, timer_has_no_content


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GemstoneConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a "now playing" sensor per controller."""
    async_add_discovered_entities(
        entry,
        async_add_entities,
        lambda coordinator, device_id: [GemstoneNowPlaying(coordinator, device_id)],
    )


class GemstoneNowPlaying(GemstoneEntity, SensorEntity):
    """Report what the controller is showing right now."""

    _attr_translation_key = "now_playing"
    _attr_icon = "mdi:lightbulb-on-outline"

    def __init__(self, coordinator: GemstoneCoordinator, device_id: str) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_now_playing"
        self._playlist_pattern: str | None = None
        self._cancel_transition: Callable[[], None] | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._clear_transition)
        self._update_playlist()

    @callback
    def _clear_transition(self) -> None:
        if self._cancel_transition:
            self._cancel_transition()
            self._cancel_transition = None

    @callback
    def _update_playlist(self) -> None:
        self._clear_transition()
        self._playlist_pattern = None
        if self.available and self._state.get("onState"):
            playlist = self.coordinator.playing_playlist(self._device_id)
            self._playlist_pattern, delay = playlist_step(
                playlist, dt_util.utcnow().timestamp()
            )
            if delay is not None:
                self._cancel_transition = async_call_later(
                    self.hass, delay, self._next_step
                )

    @callback
    def _next_step(self, _now: datetime) -> None:
        self._cancel_transition = None
        self._update_playlist()
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_playlist()
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> str:
        """Return a short description of the current output."""
        state = self._state
        if not state.get("onState"):
            return "Off"
        if design := (state.get("architectural") or {}).get("name"):
            return design
        if pattern := (state.get("pattern") or {}).get("name"):
            return pattern
        if playlist := self.coordinator.playing_playlist(self._device_id):
            return self._playlist_pattern or playlist.get("name") or "Playlist"
        if timer_has_no_content(state):
            return "Timer active"
        color = state.get("color")
        if isinstance(color_b := state.get("colorB"), dict):
            color = color_b.get("value")
        if color is not None:
            red, green, blue, white = unpack(color) or (0, 0, 0, 0)
            return f"#{red:02X}{green:02X}{blue:02X}" + (f"+W{white}" if white else "")
        return "On"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the underlying detail."""
        state = self._state
        info = self._info
        hub = info.get("hub") or {}
        settings = self.coordinator.settings(self._device_id)
        color = state.get("color")
        if isinstance(color_b := state.get("colorB"), dict):
            color = color_b.get("value")
        return {
            "on_state": state.get("onState"),
            "design": (state.get("architectural") or {}).get("name"),
            "pattern": (state.get("pattern") or {}).get("name")
            or self._playlist_pattern,
            "playlist": (
                self.coordinator.playing_playlist(self._device_id).get("name")
                if state.get("onState")
                else None
            ),
            "color": color,
            "control": self.coordinator.control_transport(self._device_id),
            "local_ip": self.coordinator.local_host(self._device_id)
            or hub.get("localIp"),
            "firmware": settings.get("firmware") or info.get("firmware"),
            "outputs": settings.get("pixelOutputNames") or hub.get("outputNames"),
            "pixel_count": settings.get("pixelCount") or hub.get("pixelCount"),
            "rgbw_sequence": settings.get("rgbwSequence") or hub.get("rgbwSequence"),
            **self.coordinator.recovery_details(self._device_id),
        }
