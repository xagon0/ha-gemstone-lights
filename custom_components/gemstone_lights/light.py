"""Light platform for Gemstone Lights.

Two kinds of light are created per controller:

* one for the **whole run**, with colour, brightness and the controller's
  built-in animations exposed as Home Assistant effects, and
* one per **zone** (front upper, rear lower, ...), which behave like WLED
  segments and can each show their own colour and effect.

These are RGBW fixtures: the dedicated white channel is separate from the
red, green and blue ones.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGBW_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import GemstoneConfigEntry
from .color_util import pack, unpack
from .const import EFFECT_LIST, EFFECT_SOLID
from .coordinator import GemstoneCoordinator
from .entity import GemstoneEntity


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
    """Shared colour and effect behaviour."""

    _attr_supported_color_modes = {ColorMode.RGBW}
    _attr_color_mode = ColorMode.RGBW
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect_list = EFFECT_LIST

    def __init__(self, coordinator: GemstoneCoordinator, device_id: str) -> None:
        """Initialise shared state."""
        super().__init__(coordinator, device_id)
        self._last_rgbw: tuple[int, int, int, int] = (255, 255, 255, 0)

    def _resolve(
        self, kwargs: dict[str, Any]
    ) -> tuple[tuple[int, int, int, int], int, str]:
        """Work out the colour, brightness and effect a command should apply."""
        rgbw = kwargs.get(ATTR_RGBW_COLOR) or self.rgbw_color or self._last_rgbw
        self._last_rgbw = tuple(rgbw)
        brightness = kwargs.get(ATTR_BRIGHTNESS) or self.brightness or 255
        effect = kwargs.get(ATTR_EFFECT) or self.effect or EFFECT_SOLID
        return tuple(rgbw), int(brightness), effect


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
    def _color_value(self) -> int | None:
        """Return the packed colour from either transport's field."""
        if isinstance(color_b := self._state.get("colorB"), dict):
            return color_b.get("value")
        return self._state.get("color")

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        """Return the current colour."""
        if pattern := self._pattern:
            colors = pattern.get("colors") or []
            return unpack(colors[0]) if colors else None
        return unpack(self._color_value)

    @property
    def brightness(self) -> int | None:
        """Return brightness, which the controller keeps separate."""
        if isinstance(color_b := self._state.get("colorB"), dict):
            return color_b.get("brightness")
        if pattern := self._pattern:
            return pattern.get("brightness")
        if (design := self._state.get("architectural")) and isinstance(design, dict):
            return design.get("brightness")
        return 255 if self.is_on else None

    @property
    def effect(self) -> str | None:
        """Return the active animation, or Solid for a plain colour."""
        if pattern := self._pattern:
            return pattern.get("animation") or EFFECT_SOLID
        if self._color_value is not None:
            return EFFECT_SOLID
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose transport and what design is playing."""
        design = self._state.get("architectural") or {}
        return {
            "playing_design": design.get("name"),
            "speed": self.coordinator.speed(self._device_id),
            "control": "local" if self.coordinator.is_local(self._device_id) else "cloud",
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on, optionally setting colour, brightness and effect."""
        if not kwargs:
            await self.coordinator.async_set_power(self._device_id, True)
            return

        rgbw, brightness, effect = self._resolve(kwargs)

        if effect == EFFECT_SOLID:
            await self.coordinator.async_play_color(
                self._device_id, pack(*rgbw), brightness
            )
            return

        pattern = self.coordinator.build_pattern(
            self._device_id, [pack(*rgbw)], effect, brightness=brightness
        )
        await self.coordinator.async_play_pattern(self._device_id, pattern)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the lights off."""
        await self.coordinator.async_set_power(self._device_id, False)


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
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        """Return this zone's first colour."""
        colors = (self._zone_pattern or {}).get("colors") or []
        return unpack(colors[0]) if colors else None

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
        rgbw, brightness, effect = self._resolve(kwargs)
        pattern = self.coordinator.build_pattern(
            self._device_id,
            [pack(*rgbw)],
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
