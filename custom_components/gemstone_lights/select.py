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
from .const import OPTION_NONE, OPTION_PICK_FOLDER
from .coordinator import GemstoneCoordinator
from .entity import GemstoneEntity, async_add_discovered_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GemstoneConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the design and pattern selects."""
    async_add_discovered_entities(entry, async_add_entities, lambda coordinator, device_id: [
        GemstoneDesignSelect(coordinator, device_id), GemstonePatternSelect(coordinator, device_id),
        GemstoneLibraryFolderSelect(coordinator, device_id), GemstoneLibraryPatternSelect(coordinator, device_id),
    ])


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


class GemstoneLibraryFolderSelect(GemstoneEntity, SelectEntity):
    """Browse Gemstone's official pattern library by folder.

    The library runs to well over a thousand patterns, which is far too many
    for one dropdown, so this picks the folder and the companion select then
    offers just that folder's patterns. Choosing a folder changes nothing on
    the lights.
    """

    _attr_translation_key = "library_folder"
    _attr_icon = "mdi:folder-multiple"

    def __init__(self, coordinator: GemstoneCoordinator, device_id: str) -> None:
        """Initialise the folder browser."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_library_folder"

    @property
    def options(self) -> list[str]:
        """Return every library folder."""
        return [OPTION_PICK_FOLDER, *self.coordinator.library_folder_options()]

    @property
    def current_option(self) -> str:
        """Return the folder being browsed."""
        chosen = self.coordinator.selected_folder(self._device_id)
        return chosen if chosen in self.options else OPTION_PICK_FOLDER

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Report how much of the library is loaded."""
        return {"library_patterns": self.coordinator.library_size()}

    async def async_select_option(self, option: str) -> None:
        """Browse a folder."""
        self.coordinator.set_selected_folder(self._device_id, option)
        # Refresh the companion select so it offers this folder's patterns.
        self.coordinator.async_update_listeners()


class GemstoneLibraryPatternSelect(GemstoneEntity, SelectEntity):
    """Play a pattern from the folder being browsed."""

    _attr_translation_key = "library_pattern"
    _attr_icon = "mdi:playlist-music"

    def __init__(self, coordinator: GemstoneCoordinator, device_id: str) -> None:
        """Initialise the library pattern select."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_library_pattern"

    @property
    def options(self) -> list[str]:
        """Return the patterns in the folder being browsed."""
        return [
            OPTION_NONE,
            *self.coordinator.library_pattern_options(self._device_id),
        ]

    @property
    def current_option(self) -> str:
        """Return the playing pattern when it belongs to this folder."""
        playing = (self._state.get("pattern") or {}).get("name")
        return playing if playing in self.options else OPTION_NONE

    async def async_select_option(self, option: str) -> None:
        """Play the chosen library pattern."""
        if option == OPTION_NONE:
            await self.coordinator.async_set_power(self._device_id, False)
            return

        folder = self.coordinator.selected_folder(self._device_id)
        data = self.coordinator.find_library_pattern(option, folder)
        if data:
            await self.coordinator.async_play_pattern(self._device_id, data)
