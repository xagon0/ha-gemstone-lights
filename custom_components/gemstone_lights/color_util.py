"""Colour packing helpers for Gemstone controllers.

The controller packs colour into a 32-bit integer as::

    R | G << 8 | B << 16 | W << 24

which is little-endian RGBW (hex reads ``WWBBGGRR``). This is confirmed by
the vendor's own Control4 and Elan drivers, and by saved patterns containing
``4278190080`` (0xFF000000, pure white channel), a value that cannot be
expressed as 24-bit RGB.
"""

from __future__ import annotations


def pack(red: int, green: int, blue: int, white: int = 0) -> int:
    """Pack RGBW channels into the controller's integer format."""
    return (
        (max(0, min(255, red)))
        | (max(0, min(255, green)) << 8)
        | (max(0, min(255, blue)) << 16)
        | (max(0, min(255, white)) << 24)
    )


def unpack(value: int | None) -> tuple[int, int, int, int] | None:
    """Unpack the controller's integer into (red, green, blue, white)."""
    if value is None:
        return None
    return (
        value & 0xFF,
        (value >> 8) & 0xFF,
        (value >> 16) & 0xFF,
        (value >> 24) & 0xFF,
    )
