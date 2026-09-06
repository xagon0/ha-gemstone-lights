"""Validate vendor state at the transport boundary."""

from typing import Any


def validate_state(value: Any) -> dict[str, Any]:
    """Reject unusable state rather than inventing an off state."""
    if not isinstance(value, dict) or not isinstance(value.get("onState"), bool):
        raise ValueError("Missing boolean onState")
    for mode in ("colorB", "pattern", "architectural", "impulse", "playlist"):
        if value.get(mode) is not None and not isinstance(value[mode], dict):
            raise ValueError(f"Invalid {mode}")
    for color in (value.get("color"), (value.get("colorB") or {}).get("value")):
        if color is not None and (type(color) is not int or not 0 <= color <= 0xFFFFFFFF):
            raise ValueError("Invalid RGBW color")
    pattern = value.get("pattern") or {}
    colors = pattern.get("colors", [])
    if not isinstance(colors, list) or any(type(c) is not int or not 0 <= c <= 0xFFFFFFFF for c in colors):
        raise ValueError("Invalid pattern colors")
    return value
