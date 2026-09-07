"""Bluetooth behavior using captured framing and a substituted external radio."""

import asyncio
import json
import struct

import pytest

from custom_components.gemstone_lights import bluetooth_api
from custom_components.gemstone_lights.bluetooth_protocol import (
    Command,
    ResponseAssembler,
    encode_fragments,
)
from custom_components.gemstone_lights.local_api import GemstoneLocalError

from .bluetooth_peer import response_frames


async def test_power_write_matches_captured_app_packet(bluetooth_client, peripheral):
    # Given the real app's captured power-on frame and an accepting peripheral.
    captured = bytes.fromhex(
        "30010001007b227374617465223a7b2264657369726564223a7b2263757272656e746c79506c6179696e67223a7b226f6e5374617465223a747275657d7d7d7d"
    )
    # When the integration sends a power-only update.
    await bluetooth_client.async_set_power(True)
    # Then the exact packet preserves content and the BLE slot is released.
    assert peripheral.writes == [captured]
    assert peripheral.active == 0


async def test_multi_fragment_write_preserves_rgbw_pixels_and_unicode(
    bluetooth_client, peripheral
):
    # Given a design that exceeds the requested 128-byte MTU.
    design = {
        "name": "暖白",
        "brightness": 64,
        "staticColors": [
            {"lights": [i], "color": 4278190080 if i % 2 else 255} for i in range(24)
        ],
    }
    # When the actual client serializes and transmits it.
    await bluetooth_client.async_play({"onState": True, "architectural": design})
    # Then frames fit the app's MTU-minus-16 rule and reconstitute the intended design.
    assert len(peripheral.writes) > 1
    headers = [struct.unpack_from("<BHH", p) for p in peripheral.writes]
    assert headers == [(0x30, i + 1, len(headers)) for i in range(len(headers))]
    assert all(len(p) == 112 for p in peripheral.writes[:-1])
    decoded = json.loads(b"".join(p[5:] for p in peripheral.writes))
    assert decoded["state"]["desired"]["currentlyPlaying"] == {
        "onState": True,
        "architectural": design,
        "colorB": None,
        "pattern": None,
        "impulse": None,
        "playlist": None,
    }
    assert peripheral.active == 0


async def test_reads_assemble_bytes_and_strip_transport_envelopes(
    bluetooth_client, peripheral
):
    # Given a controller returning fragmented UTF-8 and metadata envelopes.
    # When firmware, playing state and hub settings are requested independently.
    firmware = await bluetooth_client.async_get_firmware()
    state = await bluetooth_client.async_get_state()
    settings = await bluetooth_client.async_get_settings()
    # Then the actual state fields survive, with no invented defaults or wrapper metadata.
    assert peripheral.writes == [b"\x20", b"\x31", b"\x32"]
    assert firmware == "1.1.5"
    assert state == {
        "onState": True,
        "pattern": {"name": "暖白", "colors": [4278190080], "brightness": 64},
    }
    assert settings["pixelCount"] == [194, 74, 0, 0]
    assert "origin" not in settings
    assert len(peripheral.connections) == 3
    assert peripheral.active == 0


async def test_write_waits_for_application_acknowledgment(bluetooth_client, peripheral):
    # Given a GATT write accepted by the radio without a controller acknowledgment.
    peripheral.suppress_reply = True
    task = asyncio.create_task(bluetooth_client.async_set_power(False))
    await peripheral.write_seen.wait()
    # When only the transport-level write has completed.
    await asyncio.sleep(0)
    # Then the command stays pending until a matching application success arrives.
    assert not task.done()
    connection = peripheral.connections[0]
    connection.callback(None, bytearray(b"\x20ignored"))
    assert not task.done()
    connection.callback(None, bytearray(b"\x30\x01"))
    await task
    assert peripheral.active == 0


@pytest.mark.parametrize("ack", [b"\x30\x00", b"\x30", b"\x30\x01\x00"])
async def test_unverified_acknowledgments_fail(bluetooth_client, peripheral, ack):
    # Given a rejected or malformed application acknowledgment.
    peripheral.status = ack
    # When a state write reaches the controller.
    with pytest.raises(GemstoneLocalError, match="rejected"):
        await bluetooth_client.async_set_power(False)
    # Then no successful operation is reported and the connection is released.
    assert peripheral.active == 0


@pytest.mark.parametrize(
    "frames",
    [
        [b"\x31\x01"],
        [b"\x31\x02\x00\x02\x00{}"],
        [b"\x31\x01\x00\x02\x00{", b"\x31\x02\x00\x03\x00}"],
        [b"\x31\x01\x00\x02\x00{", b"\x31\x01\x00\x02\x00}"],
        [b"\x31\x01\x00\x01\x00not-json"],
        response_frames(0x31, {"currentlyPlaying": {"onState": "false"}}),
    ],
)
async def test_invalid_response_cannot_become_an_off_state(
    bluetooth_client, peripheral, frames
):
    # Given malformed framing, JSON, or a non-boolean power field.
    peripheral.override_reply = frames
    # When state is read through the real decoder and validator.
    with pytest.raises(GemstoneLocalError):
        await bluetooth_client.async_get_state()
    # Then the read fails instead of returning a fabricated state.
    assert peripheral.active == 0


async def test_missing_final_fragment_times_out_and_disconnects(
    bluetooth_client, peripheral, monkeypatch
):
    # Given only the first of two promised state fragments.
    peripheral.override_reply = [b"\x31\x01\x00\x02\x00{"]
    monkeypatch.setattr(bluetooth_api, "_RESPONSE_TIMEOUT", 0.01)
    # When the final fragment never arrives.
    with pytest.raises(GemstoneLocalError, match="TimeoutError"):
        await bluetooth_client.async_get_state()
    # Then the connection is freed for a later attempt.
    assert peripheral.active == 0


async def test_disconnect_fails_pending_read_and_next_request_reconnects(
    bluetooth_client, peripheral
):
    # Given an in-flight request with no response yet.
    peripheral.suppress_reply = True
    task = asyncio.create_task(bluetooth_client.async_get_state())
    await peripheral.write_seen.wait()
    # When the radio disconnects during that request.
    await peripheral.connections[0].disconnect()
    with pytest.raises(GemstoneLocalError, match="disconnected"):
        await task
    peripheral.suppress_reply = False
    state = await bluetooth_client.async_get_state()
    # Then a fresh connection recovers real state without reusing the failed client.
    assert state["onState"] is True
    assert len(peripheral.connections) == 2
    assert peripheral.active == 0


async def test_concurrent_requests_do_not_mix_fragments(bluetooth_client, peripheral):
    # Given simultaneous state and settings consumers sharing a single BLE device.
    # When both real client methods execute concurrently.
    state, settings = await asyncio.gather(
        bluetooth_client.async_get_state(), bluetooth_client.async_get_settings()
    )
    # Then only one connection is active at a time and responses reach the right consumer.
    assert peripheral.max_active == 1
    assert state["pattern"]["brightness"] == 64
    assert settings["pixelCount"][0] == 194
    assert peripheral.active == 0


async def test_cancelled_request_releases_connection_and_lock(
    bluetooth_client, peripheral
):
    # Given an unanswered state request.
    peripheral.suppress_reply = True
    task = asyncio.create_task(bluetooth_client.async_get_state())
    await peripheral.write_seen.wait()
    # When its caller cancels it and subsequently retries.
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    peripheral.suppress_reply = False
    await bluetooth_client.async_get_settings()
    # Then cancellation leaks neither a connection nor the request lock.
    assert len(peripheral.connections) == 2
    assert peripheral.active == 0


async def test_unrelated_gatt_device_receives_no_state_command(
    bluetooth_client, peripheral
):
    # Given a device without the verified Hub2 service.
    peripheral.interface_present = False
    # When it is selected by address.
    with pytest.raises(GemstoneLocalError, match="GATT"):
        await bluetooth_client.async_get_state()
    # Then no application bytes are sent to it.
    assert peripheral.writes == []
    assert peripheral.active == 0


def test_fragment_indices_use_both_little_endian_bytes():
    # Given enough content at MTU 23 to require fragment 257.
    payload = bytes(range(256)) * 2 + b"AB"
    # When framed and then independently parsed on the receiver.
    frames = encode_fragments(Command.WRITE_STATE, payload, 23)
    assembled = ResponseAssembler(Command.WRITE_STATE)
    result = None
    for frame in frames:
        result = assembled.feed(frame)
    # Then indices/counts retain their high byte and the full binary stream survives.
    assert frames[0][:5] == b"\x30\x01\x00\x01\x01"
    assert frames[-1][:5] == b"\x30\x01\x01\x01\x01"
    assert result == payload


async def test_early_ack_does_not_confirm_an_incomplete_write(
    bluetooth_client, peripheral
):
    # Given an acknowledgment arriving after only the first fragment of a design.
    peripheral.override_reply = [b"\x30\x01"]
    state = {"onState": True, "pattern": {"name": "Long name" * 40, "colors": [255]}}
    # When the client begins the fragmented write.
    with pytest.raises(GemstoneLocalError, match="Premature"):
        await bluetooth_client.async_play(state)
    # Then it stops sending rather than treating partial delivery as success.
    assert len(peripheral.writes) == 1
    assert peripheral.active == 0


async def test_oversized_write_fails_before_connecting(bluetooth_client, peripheral):
    # Given content exceeding the integration's bounded Bluetooth message size.
    state = {"onState": True, "pattern": {"name": "x" * 16000}}
    # When the caller attempts to play it.
    with pytest.raises(GemstoneLocalError, match="integration limit"):
        await bluetooth_client.async_play(state)
    # Then no connection is acquired and no partial command reaches the device.
    assert peripheral.connections == []
    assert peripheral.writes == []
