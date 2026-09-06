"""Validate vendor state at the transport boundary."""

from typing import Any


def _brightness(value: Any) -> None:
    if type(value) is not int or not 0 <= value <= 255:
        raise ValueError("Invalid brightness")


def validate_pattern(pattern: Any) -> None:
    """Validate fields consumed by entities without discarding vendor extensions."""
    if not isinstance(pattern, dict):
        raise ValueError("Invalid pattern")
    colors = pattern.get("colors", [])
    if not isinstance(colors, list) or any(
        type(c) is not int or not 0 <= c <= 0xFFFFFFFF for c in colors
    ):
        raise ValueError("Invalid pattern colors")
    _brightness(pattern.get("brightness", 255))


def validate_design(design: Any) -> None:
    """Validate the zone/pixel collections used in rendering and command editing."""
    if not isinstance(design, dict):
        raise ValueError("Invalid design")
    _brightness(design.get("brightness", 255))
    for key in ("zonePatterns", "staticColors"):
        entries = design.get(key, [])
        if entries is None:
            continue
        if not isinstance(entries, list) or any(
            not isinstance(e, dict) for e in entries
        ):
            raise ValueError(f"Invalid {key}")
        for entry in entries:
            if key == "zonePatterns":
                if not isinstance(entry.get("zoneId"), str):
                    raise ValueError("Invalid zone id")
                validate_pattern(entry.get("pattern"))
            else:
                lights = entry.get("lights")
                if not isinstance(lights, list) or any(
                    type(p) is not int or not 0 <= p < 65536 for p in lights
                ):
                    raise ValueError("Invalid pixel indices")
                color = entry.get("color")
                if type(color) is not int or not 0 <= color <= 0xFFFFFFFF:
                    raise ValueError("Invalid pixel color")


def validate_state(value: Any) -> dict[str, Any]:
    """Reject unusable state rather than inventing an off state."""
    if not isinstance(value, dict) or not isinstance(value.get("onState"), bool):
        raise ValueError("Missing boolean onState")
    for mode in ("colorB", "pattern", "architectural", "impulse", "playlist"):
        if value.get(mode) is not None and not isinstance(value[mode], dict):
            raise ValueError(f"Invalid {mode}")
    for color in (value.get("color"), (value.get("colorB") or {}).get("value")):
        if color is not None and (
            type(color) is not int or not 0 <= color <= 0xFFFFFFFF
        ):
            raise ValueError("Invalid RGBW color")
    if color_b := value.get("colorB"):
        if color_b.get("value") is None:
            raise ValueError("Missing color value")
        _brightness(color_b.get("brightness", 255))
    validate_pattern(value.get("pattern") or {})
    validate_design(value.get("architectural") or {})
    return value
