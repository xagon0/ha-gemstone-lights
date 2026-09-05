"""Sensor platform for Gemstone Lights."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import GemstoneConfigEntry
from .coordinator import GemstoneCoordinator
from .entity import GemstoneEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GemstoneConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a "now playing" sensor per controller."""
    coordinator = entry.runtime_data
    async_add_entities(
        GemstoneNowPlaying(coordinator, device_id)
        for device_id in coordinator.device_ids
    )


class GemstoneNowPlaying(GemstoneEntity, SensorEntity):
    """Report what the controller is showing right now."""

    _attr_translation_key = "now_playing"
    _attr_icon = "mdi:lightbulb-on-outline"

    def __init__(self, coordinator: GemstoneCoordinator, device_id: str) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_now_playing"

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
        if (color := state.get("color")) is not None:
            return f"#{color:06X}"
        return "On"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the underlying detail."""
        state = self._state
        info = self._info
        hub = info.get("hub") or {}
        return {
            "on_state": state.get("onState"),
            "design": (state.get("architectural") or {}).get("name"),
            "pattern": (state.get("pattern") or {}).get("name"),
            "color": state.get("color"),
            "firmware": info.get("firmware"),
            "outputs": hub.get("outputNames"),
            "local_ip": hub.get("localIp"),
        }
