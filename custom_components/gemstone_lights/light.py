"""Light platform for Gemstone Lights.

Two kinds of light are created per controller:

* one for the **whole run**, with colour, brightness and the controller's
  built-in animations exposed as Home Assistant effects, and
* one per **zone** (front upper, rear lower, ...), which behave like WLED
  segments and can each show their own colour and effect.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import GemstoneConfigEntry
from .const import EFFECT_LIST, EFFECT_SOLID
from .coordinator import GemstoneCoordinator
from .entity import GemstoneEntity


def _to_rgb(value: int | None) -> tuple[int, int, int] | None:
    """Convert a 0xRRGGBB integer to an RGB tuple."""
    if value is None:
        return None
    return ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)


def _to_int(rgb: tuple[int, int, int]) -> int:
    """Convert an RGB tuple to a 0xRRGGBB integer."""
    return (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GemstoneConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the whole-run light plus one light per zone."""
    coordinator = entry.runtime_data
    entities: list[LightEntity] = []
    for device_id in coordinator.device_ids:
        entities.append(GemstoneLight(coordinator, device_id))
        for zone in coordinator.zones(device_id):
            if zone.get("id") and zone.get("name"):
                entities.append(GemstoneZoneLight(coordinator, device_id, zone))
    async_add_entities(entities)


class _GemstoneBaseLight(GemstoneEntity, LightEntity):
    """Shared colour/effect behaviour."""

    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect_list = EFFECT_LIST

    def __init__(self, coordinator: GemstoneCoordinator, device_id: str) -> None:
        """Initialise shared state."""
        super().__init__(coordinator, device_id)
        self._last_rgb: tuple[int, int, int] = (255, 255, 255)

    def _resolve(self, kwargs: dict[str, Any]) -> tuple[tuple[int, int, int], int, str]:
        """Work out the colour, brightness and effect a command should apply."""
        rgb = kwargs.get(ATTR_RGB_COLOR) or self.rgb_color or self._last_rgb
        self._last_rgb = tuple(rgb)
        brightness = kwargs.get(ATTR_BRIGHTNESS) or self.brightness or 255
        effect = kwargs.get(ATTR_EFFECT) or self.effect or EFFECT_SOLID
        return tuple(rgb), int(brightness), effect


class GemstoneLight(_GemstoneBaseLight):
    """The whole light run."""

    _attr_name = None  # take the device name

    def __init__(self, coordinator: GemstoneCoordinator, device_id: str) -> None:
        """Initialise the light."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_light"

    @property
    def is_on(self) -> bool:
        """Return True when the lights are lit."""
        return bool(self._state.get("onState"))

    @property
    def _pattern(self) -> dict[str, Any] | None:
        return self._state.get("pattern") or None

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the colour, normalised to full brightness."""
        if pattern := self._pattern:
            colors = pattern.get("colors") or []
            return _to_rgb(colors[0]) if colors else None
        rgb = _to_rgb(self._state.get("color"))
        if rgb is None:
            return None
        peak = max(rgb)
        if peak == 0:
            return (0, 0, 0)
        return tuple(min(255, round(c * 255 / peak)) for c in rgb)

    @property
    def brightness(self) -> int | None:
        """Return brightness, from the pattern or the colour's peak channel."""
        if pattern := self._pattern:
            return pattern.get("brightness")
        rgb = _to_rgb(self._state.get("color"))
        return max(rgb) if rgb else None

    @property
    def effect(self) -> str | None:
        """Return the active animation, or Solid for a plain colour."""
        if pattern := self._pattern:
            return pattern.get("animation") or EFFECT_SOLID
        if self._state.get("color") is not None:
            return EFFECT_SOLID
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the design name when one is playing."""
        design = self._state.get("architectural") or {}
        return {"playing_design": design.get("name"), "speed": self.coordinator.speed(self._device_id)}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on, optionally setting colour, brightness and effect."""
        if not kwargs:
            await self.coordinator.async_apply(
                self.coordinator.api.async_set_power(self._device_id, True)
            )
            return

        rgb, brightness, effect = self._resolve(kwargs)

        if effect == EFFECT_SOLID:
            scaled = tuple(round(c * brightness / 255) for c in rgb)
            await self.coordinator.async_apply(
                self.coordinator.api.async_play_color(self._device_id, _to_int(scaled))
            )
            return

        pattern = self.coordinator.build_pattern(
            self._device_id, [_to_int(rgb)], effect, brightness=brightness
        )
        await self.coordinator.async_apply(
            self.coordinator.api.async_play_pattern(self._device_id, pattern)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the lights off."""
        await self.coordinator.async_apply(
            self.coordinator.api.async_set_power(self._device_id, False)
        )


class GemstoneZoneLight(_GemstoneBaseLight):
    """One zone of the run, behaving like a WLED segment."""

    def __init__(
        self, coordinator: GemstoneCoordinator, device_id: str, zone: dict[str, Any]
    ) -> None:
        """Initialise the zone light."""
        super().__init__(coordinator, device_id)
        self._zone_id: str = zone["id"]
        self._attr_name = zone["name"]
        self._attr_unique_id = f"{device_id}_zone_{self._zone_id}"

    @property
    def _zone_pattern(self) -> dict[str, Any] | None:
        return self.coordinator.zone_patterns(self._device_id).get(self._zone_id)

    @property
    def is_on(self) -> bool:
        """Return True when this zone is showing something."""
        return bool(self._state.get("onState")) and self._zone_pattern is not None

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return this zone's first colour."""
        pattern = self._zone_pattern or {}
        colors = pattern.get("colors") or []
        return _to_rgb(colors[0]) if colors else None

    @property
    def brightness(self) -> int | None:
        """Return this zone's brightness."""
        return (self._zone_pattern or {}).get("brightness")

    @property
    def effect(self) -> str | None:
        """Return this zone's animation."""
        if pattern := self._zone_pattern:
            return pattern.get("animation") or EFFECT_SOLID
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Apply a colour and effect to this zone only."""
        rgb, brightness, effect = self._resolve(kwargs)
        pattern = self.coordinator.build_pattern(
            self._device_id,
            [_to_int(rgb)],
            "motionless" if effect == EFFECT_SOLID else effect,
            name=self._attr_name or "Zone",
            brightness=brightness,
        )
        await self.coordinator.async_set_zone_pattern(
            self._device_id, self._zone_id, pattern
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Clear this zone, leaving the others as they are."""
        await self.coordinator.async_set_zone_pattern(
            self._device_id, self._zone_id, None
        )
