"""Light platform for Gemstone Lights."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
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
    """Set up a light for each controller."""
    coordinator = entry.runtime_data
    async_add_entities(
        GemstoneLight(coordinator, device_id) for device_id in coordinator.device_ids
    )


class GemstoneLight(GemstoneEntity, LightEntity):
    """The controller as a single light.

    The hardware exposes colour only; brightness is expressed by scaling the
    RGB value, which is the usual convention for RGB-only lights in Home
    Assistant. A pattern or design being active is reported as "on" with no
    colour, since no single colour describes it.
    """

    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB
    _attr_name = None  # use the device name

    def __init__(self, coordinator: GemstoneCoordinator, device_id: str) -> None:
        """Initialise the light."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_light"
        # Remembered so turning on after a pattern has a sensible colour.
        self._last_rgb: tuple[int, int, int] = (255, 255, 255)

    @property
    def is_on(self) -> bool:
        """Return True when the lights are lit."""
        return bool(self._state.get("onState"))

    @property
    def _device_rgb(self) -> tuple[int, int, int] | None:
        """Return the raw colour reported by the controller."""
        color = self._state.get("color")
        if color is None:
            return None
        return ((color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF)

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the colour normalised to full brightness."""
        rgb = self._device_rgb
        if rgb is None:
            return None
        peak = max(rgb)
        if peak == 0:
            return (0, 0, 0)
        return tuple(min(255, round(channel * 255 / peak)) for channel in rgb)

    @property
    def brightness(self) -> int | None:
        """Return brightness derived from the colour's strongest channel."""
        rgb = self._device_rgb
        if rgb is None:
            return None
        return max(rgb)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose what is playing, which is richer than colour alone."""
        state = self._state
        pattern = state.get("pattern") or {}
        design = state.get("architectural") or {}
        return {
            "playing_pattern": pattern.get("name"),
            "playing_design": design.get("name"),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on, optionally setting a colour and/or brightness."""
        rgb = kwargs.get(ATTR_RGB_COLOR)
        brightness = kwargs.get(ATTR_BRIGHTNESS)

        if rgb is None and brightness is None:
            # A plain "on" should restore whatever was last showing.
            await self.coordinator.async_apply(
                self.coordinator.api.async_set_power(self._device_id, True)
            )
            return

        if rgb is None:
            rgb = self.rgb_color or self._last_rgb
        self._last_rgb = tuple(rgb)

        if brightness is None:
            brightness = self.brightness or 255

        scaled = tuple(round(channel * brightness / 255) for channel in rgb)
        value = (scaled[0] << 16) | (scaled[1] << 8) | scaled[2]

        await self.coordinator.async_apply(
            self.coordinator.api.async_play_color(self._device_id, value)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the lights off."""
        await self.coordinator.async_apply(
            self.coordinator.api.async_set_power(self._device_id, False)
        )
