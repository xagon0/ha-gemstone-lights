"""A simulated external GATT peripheral; integration logic is never substituted."""

import asyncio
import json
import struct
from types import SimpleNamespace

from bleak.backends.device import BLEDevice

ADDRESS = "AA:BB:CC:DD:EE:FF"
DEVICE = BLEDevice(ADDRESS, "Test Hub2", {})
SERVICE = "2bd43a90-ac88-4b63-b52c-265228e62a1a"
CHARACTERISTIC = "524a9b39-c421-44b6-88ea-e558dbc74c2c"


def response_frames(opcode, body):
    """Model controller notifications with UTF-8 split across arbitrary boundaries."""
    data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    pieces = [data[i : i + 7] for i in range(0, len(data), 7)]
    return [
        struct.pack("<BHH", opcode, i + 1, len(pieces)) + part
        for i, part in enumerate(pieces)
    ]


class Peripheral:
    def __init__(self):
        self.mtu = 128
        self.state = {
            "onState": True,
            "pattern": {"name": "暖白", "colors": [4278190080], "brightness": 64},
        }
        self.settings = {
            "firmware": "1.1.5",
            "pixelCount": [194, 74, 0, 0],
            "bluetoothName": "Test Hub2",
            "tcpEnabled": False,
        }
        self.connections = []
        self.active = 0
        self.max_active = 0
        self.writes = []
        self.write_seen = asyncio.Event()
        self.suppress_reply = False
        self.override_reply = None
        self.status = b"\x30\x01"
        self.interface_present = True

    async def connect(
        self, client_class, device, name, *, disconnected_callback, **kwargs
    ):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        connection = Connection(self, disconnected_callback)
        self.connections.append(connection)
        return connection


class Connection:
    def __init__(self, peer, disconnected_callback):
        self.peer = peer
        self.mtu_size = peer.mtu
        self.callback = None
        self.disconnected_callback = disconnected_callback
        self.is_connected = True
        self._parts = []
        characteristic = SimpleNamespace(
            uuid=CHARACTERISTIC, properties=["write", "notify"]
        )
        service = SimpleNamespace(
            get_characteristic=lambda uuid: (
                characteristic if uuid == CHARACTERISTIC else None
            )
        )
        self.services = SimpleNamespace(
            get_service=lambda uuid: (
                service if peer.interface_present and uuid == SERVICE else None
            )
        )

    async def start_notify(self, characteristic, callback):
        self.callback = callback

    async def write_gatt_char(self, characteristic, packet, *, response):
        assert response  # This peripheral exposes WRITE, not WRITE_WITHOUT_RESPONSE.
        self.peer.writes.append(bytes(packet))
        self.peer.write_seen.set()
        await asyncio.sleep(0)
        if self.peer.suppress_reply:
            return
        if self.peer.override_reply is not None:
            for frame in self.peer.override_reply:
                self.callback(characteristic, bytearray(frame))
            return
        opcode = packet[0]
        if opcode == 0x20:
            replies = [b"\x201.1.5"]
        elif opcode == 0x31:
            replies = response_frames(
                opcode,
                {
                    "currentlyPlaying": self.peer.state,
                    "origin": "local",
                    "env": "Production",
                },
            )
        elif opcode == 0x32:
            replies = response_frames(
                opcode,
                {
                    "hubSettings": self.peer.settings,
                    "origin": "local",
                    "env": "Production",
                },
            )
        elif opcode == 0x30:
            _, index, total = struct.unpack_from("<BHH", packet)
            self._parts.append(packet[5:])
            if index != total:
                return
            if self.peer.status == b"\x30\x01":
                playing = json.loads(b"".join(self._parts))["state"]["desired"][
                    "currentlyPlaying"
                ]
                self.peer.state.update(playing)
                self.peer.state = {
                    k: v for k, v in self.peer.state.items() if v is not None
                }
            replies = [self.peer.status]
        else:
            raise AssertionError(f"Unobserved command sent: {opcode}")
        for frame in replies:
            self.callback(characteristic, bytearray(frame))

    async def disconnect(self):
        if self.is_connected:
            self.is_connected = False
            self.peer.active -= 1
            self.disconnected_callback(self)
