"""Reconcile logical colors and zones with the controller's physical payload."""

from copy import deepcopy
from typing import Any

from .color_util import pack, unpack


def scale_color(color: int, brightness: int) -> int:
    """Apply brightness to all four channels for transports without zone dimmers."""
    return pack(*(round(c * brightness / 255) for c in unpack(color)))


def encode_cloud_design(design: dict[str, Any]) -> dict[str, Any]:
    """Encode zone dimmers that cloud play otherwise replaces with the master level.

    The design's master brightness remains supported. Scale only each zone's
    palette and background, leaving its animation and all other metadata intact.
    """
    encoded = deepcopy(design)
    for entry in encoded.get("zonePatterns") or []:
        pattern = entry["pattern"]
        brightness = pattern.get("brightness", 255)
        pattern["colors"] = [
            scale_color(color, brightness) for color in pattern.get("colors", [])
        ]
        if pattern.get("backgroundColor") is not None:
            pattern["backgroundColor"] = scale_color(
                pattern["backgroundColor"], brightness
            )
        pattern["brightness"] = 255
    return encoded


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
            if key not in ("id", "name", "preview")
            and item is not None
            and not (key in ("staticColors", "zonePatterns") and item == [])
        }
    if isinstance(value, list):
        return [_without_metadata(item) for item in value]
    return value


def same_content(observed: dict[str, Any], sent: dict[str, Any]) -> bool:
    """Only retain logical metadata while the physical content still matches."""
    return _content(observed) == _content(sent)
