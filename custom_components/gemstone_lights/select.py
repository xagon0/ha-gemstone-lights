"""Select platform for Gemstone Lights.

Two dropdowns per controller:

* **Design** - saved architectural designs. These are the ones that can target
  individual zones (front upper, rear lower, ...), so this is how you drive
  front/back independently.
* **Pattern** - the account's saved patterns, applied to the whole controller.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import GemstoneConfigEntry
from .const import OPTION_NONE
from .coordinator import GemstoneCoordinator
from .entity import GemstoneEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GemstoneConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the design and pattern selects."""
    coordinator = entry.runtime_data
    entities: list[SelectEntity] = []
    for device_id in coordinator.device_ids:
        entities.append(GemstoneDesignSelect(coordinator, device_id))
        entities.append(GemstonePatternSelect(coordinator, device_id))
    async_add_entities(entities)


class GemstoneDesignSelect(GemstoneEntity, SelectEntity):
    """Choose one of the saved (optionally per-zone) designs."""

    _attr_translation_key = "design"
    _attr_icon = "mdi:home-lightbulb-outline"

    def __init__(self, coordinator: GemstoneCoordinator, device_id: str) -> None:
        """Initialise the select."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_design"

    @property
    def options(self) -> list[str]:
        """Return the available design names."""
        names = [
            design["name"]
            for design in self.coordinator.designs(self._device_id)
            if design.get("name")
        ]
        return [OPTION_NONE, *sorted(names)]

    @property
    def current_option(self) -> str:
        """Return the design currently playing."""
        design = self._state.get("architectural") or {}
        name = design.get("name")
        return name if name in self.options else OPTION_NONE

    async def async_select_option(self, option: str) -> None:
        """Play the chosen design."""
        if option == OPTION_NONE:
            await self.coordinator.async_set_power(self._device_id, False)
            return

        for design in self.coordinator.designs(self._device_id):
            if design.get("name") == option:
                await self.coordinator.async_play_design(self._device_id, design)
                return

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """List the zones this controller has, for reference in automations."""
        zones = self.coordinator.zones(self._device_id)
        return {"zones": [zone.get("name") for zone in zones if zone.get("name")]}


class GemstonePatternSelect(GemstoneEntity, SelectEntity):
    """Choose one of the account's saved patterns."""

    _attr_translation_key = "pattern"
    _attr_icon = "mdi:palette"

    def __init__(self, coordinator: GemstoneCoordinator, device_id: str) -> None:
        """Initialise the select."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_pattern"

    @property
    def options(self) -> list[str]:
        """Return the available pattern names."""
        names = {
            pattern["name"]
            for pattern in self.coordinator.patterns()
            if pattern.get("name")
        }
        return [OPTION_NONE, *sorted(names)]

    @property
    def current_option(self) -> str:
        """Return the pattern currently playing."""
        pattern = self._state.get("pattern") or {}
        name = pattern.get("name")
        return name if name in self.options else OPTION_NONE

    async def async_select_option(self, option: str) -> None:
        """Play the chosen pattern."""
        if option == OPTION_NONE:
            await self.coordinator.async_set_power(self._device_id, False)
            return

        for pattern in self.coordinator.patterns():
            if pattern.get("name") == option:
                await self.coordinator.async_play_pattern(
                    self._device_id, pattern["data"]
                )
                return
