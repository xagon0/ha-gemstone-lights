"""Optional local Bluetooth transport for Hub2 controller-state operations."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from .bluetooth_protocol import (
    CHARACTERISTIC_UUID,
    MAX_MESSAGE_BYTES,
    SERVICE_UUID,
    Command,
    ResponseAssembler,
    encode_fragments,
)
from .local_api import GemstoneLocalError
from .validation import validate_state

_RESPONSE_TIMEOUT = 10
_OPERATION_TIMEOUT = 40
_MODES = ("colorB", "pattern", "architectural", "impulse", "playlist")


class GemstoneBluetoothApi:
    """Use HA's selected BLE device, releasing its connection after each request.

    The device callback must use HA's connectable Bluetooth discovery so remote
    adapters remain available. It also allows an independent physical probe to
    supply a device from BleakScanner without a running HA instance.
    """

    def __init__(
        self, address: str, get_device: Callable[[], BLEDevice | None]
    ) -> None:
        self.address = address
        self._get_device = get_device
        self._lock = asyncio.Lock()

    @property
    def host(self) -> str:
        """Provide an unambiguous transport label for coordinator logging."""
        return f"Bluetooth {self.address}"

    async def _exchange(self, command: Command, payload: bytes | None = None) -> bytes:
        if payload is not None and (not payload or len(payload) > MAX_MESSAGE_BYTES):
            raise GemstoneLocalError("Bluetooth message exceeds the integration limit")
        async with self._lock:
            final_write_started = False
            device = self._get_device()
            if device is None:
                raise GemstoneLocalError(
                    "Controller is not in connectable Bluetooth range"
                )
            client = None
            response = asyncio.get_running_loop().create_future()
            assembler = ResponseAssembler(command)

            def disconnected(_client) -> None:
                if not response.done():
                    response.set_exception(
                        GemstoneLocalError("Bluetooth disconnected before the response")
                    )

            def received(_characteristic, data: bytearray) -> None:
                if response.done() or not data or data[0] != command:
                    return
                try:
                    if command == Command.WRITE_STATE:
                        if not final_write_started:
                            raise ValueError("Premature Bluetooth write acknowledgment")
                        if bytes(data) != bytes((Command.WRITE_STATE, 1)):
                            raise ValueError(
                                "Controller rejected the Bluetooth state write "
                                f"(status {data[1] if len(data) == 2 else 'invalid response'})"
                            )
                        complete = bytes(data[1:])
                    elif command == Command.FIRMWARE:
                        if not 2 <= len(data) <= 65:
                            raise ValueError("Invalid Bluetooth firmware response")
                        complete = bytes(data[1:])
                    else:
                        complete = assembler.feed(bytes(data))
                    if complete is not None:
                        response.set_result(complete)
                except ValueError as err:
                    response.set_exception(GemstoneLocalError(str(err)))

            try:
                async with asyncio.timeout(_OPERATION_TIMEOUT):
                    client = await establish_connection(
                        BleakClientWithServiceCache,
                        device,
                        "Gemstone controller",
                        disconnected_callback=disconnected,
                        max_attempts=2,
                    )
                    service = client.services.get_service(SERVICE_UUID)
                    characteristic = (
                        service.get_characteristic(CHARACTERISTIC_UUID)
                        if service
                        else None
                    )
                    if characteristic is None or not {"write", "notify"}.issubset(
                        characteristic.properties
                    ):
                        raise GemstoneLocalError(
                            "Controller lacks the verified Hub2 GATT interface"
                        )
                    frames = (
                        encode_fragments(command, payload, client.mtu_size)
                        if payload is not None
                        else [bytes((command,))]
                    )
                    await client.start_notify(characteristic, received)
                    async with asyncio.timeout(_RESPONSE_TIMEOUT):
                        for index, frame in enumerate(frames):
                            final_write_started = index == len(frames) - 1
                            await client.write_gatt_char(
                                characteristic, frame, response=True
                            )
                            if response.done() and response.exception() is not None:
                                return await response
                        return await response
            except (BleakError, OSError, TimeoutError, ValueError) as err:
                raise GemstoneLocalError(
                    f"Bluetooth {command.name.lower()} failed ({type(err).__name__})"
                ) from err
            finally:
                if not response.done():
                    response.cancel()
                elif not response.cancelled():
                    # Retrieve an exception even if a simultaneous transport error won.
                    response.exception()
                if client is not None:
                    try:
                        async with asyncio.timeout(5):
                            await client.disconnect()
                    except BleakError, OSError, TimeoutError:
                        pass

    async def async_get_firmware(self) -> str:
        """Read the version without requesting or changing credentials."""
        try:
            value = (await self._exchange(Command.FIRMWARE)).decode("utf-8")
            if not value.isprintable():
                raise ValueError("Invalid firmware string")
            return value
        except (UnicodeError, ValueError) as err:
            raise GemstoneLocalError("Invalid Bluetooth firmware response") from err

    async def _read_object(self, command: Command, key: str) -> dict[str, Any]:
        try:
            body = json.loads(await self._exchange(command))
            value = body[key]
            if not isinstance(value, dict):
                raise ValueError("Invalid state object")
            return value
        except (ValueError, KeyError, TypeError) as err:
            raise GemstoneLocalError("Invalid Bluetooth state response") from err

    async def async_get_state(self) -> dict[str, Any]:
        """Read actual playing state; never substitute a default off state."""
        try:
            return validate_state(
                await self._read_object(Command.READ_STATE, "currentlyPlaying")
            )
        except ValueError as err:
            raise GemstoneLocalError("Invalid Bluetooth playing state") from err

    async def async_get_settings(self) -> dict[str, Any]:
        return await self._read_object(Command.READ_SETTINGS, "hubSettings")

    async def async_play(
        self, currently_playing: dict[str, Any], *, null_modes: bool = True
    ) -> None:
        """Write lighting state and require the controller's success acknowledgment."""
        try:
            payload = dict(validate_state(currently_playing))
            if null_modes:
                for mode in _MODES:
                    payload.setdefault(mode, None)
            encoded = json.dumps(
                {"state": {"desired": {"currentlyPlaying": payload}}},
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (ValueError, TypeError) as err:
            raise GemstoneLocalError("Invalid Bluetooth command state") from err
        await self._exchange(Command.WRITE_STATE, encoded)

    async def async_set_power(self, on: bool) -> None:
        await self.async_play({"onState": on}, null_modes=False)
