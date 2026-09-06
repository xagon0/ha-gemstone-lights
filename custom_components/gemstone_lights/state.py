"""Reconcile logical colors and zones with the controller's physical payload."""

from typing import Any

from .color_util import pack, unpack


def scale_color(color: int, brightness: int) -> int:
    """Apply brightness to all four channels for transports without zone dimmers."""
    return pack(*(round(c * brightness / 255) for c in unpack(color)))


def _content(state: dict[str, Any]) -> Any:
    for mode in ("pattern", "architectural", "playlist", "impulse"):
        if state.get(mode):
            return {mode: _without_metadata(state[mode])}
    if color_b := state.get("colorB"):
        return {"color": scale_color(color_b["value"], color_b.get("brightness", 255))}
    return {"color": state.get("color")}


def _without_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_metadata(item)
            for key, item in value.items()
            if key not in ("id", "name", "preview") and item is not None
        }
    if isinstance(value, list):
        return [_without_metadata(item) for item in value]
    return value


def same_content(observed: dict[str, Any], sent: dict[str, Any]) -> bool:
    """Only retain logical metadata while the physical content still matches."""
    return _content(observed) == _content(sent)
