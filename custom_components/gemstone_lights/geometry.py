"""Decode native zone selections without filling gaps between their pixels."""

from typing import Any

MAX_PIXELS = 65536


def explicit_pixels(value: Any) -> tuple[int, ...]:
    """Validate an ordered, nonempty selection of distinct physical indices."""
    if (
        not isinstance(value, list)
        or not 0 < len(value) <= MAX_PIXELS
        or any(type(p) is not int or not 0 <= p < MAX_PIXELS for p in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError("Invalid zone pixel selection")
    return tuple(value)


def decode_lights(value: Any) -> tuple[int, ...]:
    """Expand repeated-start range markers interspersed with literal indices.

    A native [start, start, end] marker denotes an inclusive run. Single indices
    and short literal runs have no header. This encoding is specific to zone
    geometry; architectural staticColors already contains explicit indices.
    """
    if (
        not isinstance(value, list)
        or not 0 < len(value) <= MAX_PIXELS
        or any(type(p) is not int or not 0 <= p < MAX_PIXELS for p in value)
    ):
        raise ValueError("Invalid zone geometry")
    pixels: list[int] = []
    index = 0
    while index < len(value):
        start = value[index]
        if index + 1 < len(value) and value[index + 1] == start:
            if index + 2 >= len(value) or value[index + 2] <= start:
                raise ValueError("Invalid compressed zone range")
            end = value[index + 2]
            if len(pixels) + end - start + 1 > MAX_PIXELS:
                raise ValueError("Zone geometry exceeds pixel limit")
            pixels.extend(range(start, end + 1))
            index += 3
        else:
            pixels.append(start)
            index += 1
    return explicit_pixels(pixels)


def legacy_local_pixels(value: Any) -> tuple[int, ...]:
    """Read the count/start/end format used only by pre-fix local catalogs."""
    if (
        not isinstance(value, list)
        or len(value) != 3
        or any(type(p) is not int for p in value)
        or not 0 <= value[1] <= value[2] < 4096
        or value[0] != value[2] - value[1] + 1
    ):
        raise ValueError("Invalid legacy local zone range")
    return tuple(range(value[1], value[2] + 1))
