"""Shared entity base for Gemstone Lights."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import GemstoneCoordinator


class GemstoneEntity(CoordinatorEntity[GemstoneCoordinator]):
    """Base entity tied to one Gemstone controller."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GemstoneCoordinator, device_id: str) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self._device_id = device_id

    @property
    def _info(self) -> dict[str, Any]:
        return self.coordinator.device_info_raw(self._device_id)

    @property
    def _state(self) -> dict[str, Any]:
        return self.coordinator.device_state(self._device_id)

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device registry entry for this controller."""
        info = self._info
        hub = info.get("hub") or {}
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            manufacturer=MANUFACTURER,
            name=info.get("name") or self._device_id,
            model="Hub2" if hub else "Gemstone Controller",
            sw_version=info.get("firmware"),
            configuration_url="https://www.gemstonelights.com/app/",
        )

    @property
    def available(self) -> bool:
        """Return True when the controller is reachable."""
        return bool(super().available and self.coordinator.device_available(self._device_id))
