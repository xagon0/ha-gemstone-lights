"""Hub2 framing recovered from app captures and physical controller responses."""

from __future__ import annotations

import struct
from enum import IntEnum

SERVICE_UUID = "2bd43a90-ac88-4b63-b52c-265228e62a1a"
CHARACTERISTIC_UUID = "524a9b39-c421-44b6-88ea-e558dbc74c2c"
# A client resource limit, not a measured firmware limit.
MAX_MESSAGE_BYTES = 15 * 1024
_HEADER = struct.Struct("<BHH")


class Command(IntEnum):
    """Only commands observed in the app and verified on firmware 1.1.5."""

    FIRMWARE = 0x20
    WRITE_STATE = 0x30
    READ_STATE = 0x31
    READ_SETTINGS = 0x32


def encode_fragments(command: Command, payload: bytes, mtu: int) -> list[bytes]:
    """Split UTF-8 bytes using the app's MTU-minus-16 frame sizing."""
    if not 23 <= mtu <= 517:
        raise ValueError("Unsupported Bluetooth MTU")
    if not payload or len(payload) > MAX_MESSAGE_BYTES:
        raise ValueError("Bluetooth message is empty or exceeds the integration limit")
    chunk_size = mtu - 16 - _HEADER.size
    total = (len(payload) + chunk_size - 1) // chunk_size
    return [
        _HEADER.pack(command, index + 1, total) + payload[offset : offset + chunk_size]
        for index, offset in enumerate(range(0, len(payload), chunk_size))
    ]


class ResponseAssembler:
    """Reject truncated, missing, reordered or inconsistent response fragments."""

    def __init__(self, command: Command) -> None:
        self.command = command
        self._total: int | None = None
        self._next = 1
        self._payload = bytearray()
        self._complete = False

    def feed(self, frame: bytes) -> bytes | None:
        """Return the complete byte stream only after every fragment arrived."""
        if self._complete or len(frame) <= _HEADER.size:
            raise ValueError("Invalid Bluetooth response frame")
        command, index, total = _HEADER.unpack_from(frame)
        if (
            command != self.command
            or index != self._next
            or not 1 <= index <= total <= MAX_MESSAGE_BYTES
            or (self._total is not None and total != self._total)
        ):
            raise ValueError("Inconsistent Bluetooth response fragments")
        self._total = total
        self._payload.extend(frame[_HEADER.size :])
        if len(self._payload) > MAX_MESSAGE_BYTES:
            raise ValueError("Bluetooth response exceeds the integration limit")
        self._next += 1
        if index == total:
            self._complete = True
            return bytes(self._payload)
        return None
