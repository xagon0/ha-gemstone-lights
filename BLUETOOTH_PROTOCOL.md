# Hub2 Bluetooth controller-state protocol

Captured and independently exercised on 2026-09-06 with controller firmware
1.1.5 (SPI 1.2.1, Wi-Fi 3.3.9), Android app 0.6.64 and Home Assistant 2026.9.1.
This documents original observations, not a vendor specification.

## GATT interface

- Service: `2bd43a90-ac88-4b63-b52c-265228e62a1a`
- Characteristic: `524a9b39-c421-44b6-88ea-e558dbc74c2c`
- Properties: write with response and notify. No characteristic read operation.
- Subscribe to notifications before issuing a command. The tested controller
  sends no state merely because a subscription was opened.

The service and characteristic were discovered directly on the owner's controller
and independently matched identifiers in the Android app. Do not use the app's
music preset IDs as Bluetooth UUIDs. Advertisements did not include this service
UUID, so discovery cannot rely on an advertised service match alone.

## Verified commands

| Request | Response | Meaning |
| --- | --- | --- |
| `20` | `20` followed by UTF-8 `1.1.5` | Firmware version |
| `31` | Framed JSON with root `currentlyPlaying`, `origin`, `env` | Read playing state |
| `32` | Framed JSON with root `hubSettings`, `origin`, `env` | Read controller settings |
| Framed `30` plus JSON | `30 01` on the successful write tested | Write desired controller state |

Read requests are exactly one byte. A write has this JSON shape:

```json
{"state":{"desired":{"currentlyPlaying":{"onState":true}}}}
```

Its complete captured single-frame request is:

```text
30 01 00 01 00
7b227374617465223a7b2264657369726564223a7b2263757272656e746c79506c6179696e67223a7b226f6e5374617465223a747275657d7d7d7d
```

The app also sends architectural content inside `currentlyPlaying` using the
same envelope. Firmware returns playing state directly under `currentlyPlaying`
and settings under `hubSettings`; the LAN API's outer `state.reported` wrapper
is absent from these Bluetooth responses.

## Fragmentation

Every JSON-bearing frame starts with a five-byte header:

| Offset | Size | Meaning |
| --- | --- | --- |
| 0 | 1 | Command opcode |
| 1 | 2 | One-based fragment number, unsigned little-endian |
| 3 | 2 | Total fragment count, unsigned little-endian |
| 5 | remaining | Part of the UTF-8 JSON byte stream |

Concatenate payload bytes in order and decode UTF-8 only after the last fragment.
A captured 1,023-byte app design used headers `30 01 00 05 00` through
`30 05 00 05 00` at MTU 256: four 240-byte frames and one 88-byte frame.
With MTU 128, the same JSON used ten frames: nine 112-byte frames and one
65-byte frame. These observations match a maximum frame size of MTU minus 16,
including the five-byte header (JSON chunk size MTU minus 21).

At the Mac's negotiated MTU 256, a real `31` response used 240- and 105-byte
frames, and a `32` response used 240-, 240- and 22-byte frames. Their headers
reported two and three fragments respectively. Response assembly must use the
received headers rather than assume the sender's chosen chunk length.

## Evidence and limits

The owner's existing emulator ran the unmodified app business logic offline.
Temporary instrumentation replaced only its Android Bluetooth I/O, using real
GATT metadata and observed response formats. This exposed the actual commands
without forwarding unknown writes to the physical controller. Both MTU values
were supplied at that external I/O boundary; this is not proof of an Android
phone's negotiated MTU on the physical controller.

Independent Bluetooth probes then verified firmware, playing-state and settings
reads. The captured power-on write returned `30 01`; an independent HTTP read
confirmed `onState: true`. The original controller state was restored and read
back exactly. No reset, provisioning, timer edit, output configuration or firmware
write was performed. No password exchange was needed on this firmware. Protected
firmware, new-device provisioning and the app's Bluetooth credential mechanism
remain unverified.

A successful GATT write alone is insufficient: wait for the controller's matching
application acknowledgment. Only the observed success status is recognized;
other statuses must fail rather than be interpreted speculatively. A disconnected
or incomplete response must not become an invented off state. Release the BLE
connection after an operation so other clients can use the controller.

Raw app captures and controller settings remain outside the repository because
they can contain private device/network metadata. The older signed app build
0.4.83 also uses the incompatible Shorebird snapshot format; no stock Dart
decompilation was obtained. Emulator instrumentation was removed from the running
app, its network settings restored, and the emulator shut down after capture.


## Implemented client and additional live checks

The optional HA transport implements all four commands above through HA's
connectable Bluetooth discovery and `bleak-retry-connector`. It checks the GATT
interface, serializes exchanges, waits for application acknowledgment, bounds
fragment assembly and timeouts, and disconnects after each operation. The 15 KiB
message cap is an integration resource limit, not a measured BLE firmware limit.

The production client independently read firmware/state/settings, wrote power
and red RGBW at brightness 64, and replayed an app-generated architectural design
larger than 1 KiB. The design's fields matched readback except that firmware
removed `preview: false`. The original state was restored over Bluetooth and
verified exactly through independent HTTP reads after every probe.

A two-color pattern with an eight-byte name and one with a 31-byte name were
accepted with exact readback. A tested 32-byte name returned `30 e1` (status 225).
A 40-color palette returned success but was reported as a single color; its exact
palette-size boundary has not been measured. Do not infer physical rendering or
lossless storage from a positive acknowledgment. A definite negative
acknowledgment fails the action without marking the device disconnected or
resending via the cloud. Invalid responses and transport failures remain errors.

A daytime doorbell frame showed only weak roofline light visibility, so it does
not establish precise color or per-pixel rendering for these Bluetooth probes.
The controller's WAN connection was not blocked during physical replay; command
traffic itself used the local BLE connection without a cloud credential exchange.

On the owner's HA 2026.9.1 installation, the new version loaded successfully and
preserved existing entities. A temporary Bluetooth override reached a discovered
controller but its state operation timed out; the earlier advertisement was about
-94 dBm. Range is a plausible cause, not a confirmed diagnosis. The saved LAN
configuration was restored. Thus direct Mac BLE replay is physically verified,
while this HA adapter path still needs successful validation with better coverage.


The subsequent [native management investigation](MANAGEMENT_PROTOCOL.md) extends
the verified protocol with zone inventory/creation/deletion, timer counts and
creation/deletion, and Wi-Fi RSSI. These management operations are not exposed by
the v1.7.0 HA integration.
