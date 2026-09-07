# Hub2 Android protocol investigation

Investigation date: 2026-09-06. Integration baseline: v1.6.0. Live controller:
Hub2 firmware 1.1.5. This report supplements [Local operation](LOCAL_OPERATION.md).

## Result

Live captures recovered the Hub2 app's music transport: **UDP port 1902, eight
unsigned audio levels encoded as lowercase hexadecimal and then Base64, about
16 packets per second**. Synthetic audio supplied to the owner's existing Android
emulator changes these levels. This establishes an app-side local transport, not
a working independent music integration.

Both the vendor app and an independent sender failed to produce visible music
output on the tested controller. A solid-red control command was clearly visible
in the same camera. Connected UDP probes returned `ECONNREFUSED` on port 1902,
including while the controller reported the music animation active. The receiving
port, firmware compatibility and possible initialization requirement need to be
resolved before implementing playback in HA. Cloud-free initialization and
controller WAN isolation remain unverified.

Bluetooth controller-state reads and writes are now verified; see
[the captured protocol](BLUETOOTH_PROTOCOL.md). Bluetooth timer operations are also present. Native zone
creation, playlists, provisioning, settings writes, and firmware updates remain
unverified. All live lighting tests restored and read back the exact original
controller state. The initial music investigation changed no production code; the subsequent
Bluetooth implementation now provides optional controller-state transport.

## App identity and reproducibility

The correct Hub2 Android package is `com.gemstone.lights`, linked from its
[Google Play listing](https://play.google.com/store/apps/details?id=com.gemstone.lights).
The installed Mac application is the older `com.gemstone.gemstonehub` / Hub1
application and is not a suitable Hub2 protocol reference.

The emulator was found on the owner's other Mac through its existing SSH setup.
Its AVD is named `gem`, stored in `$HOME/.android/avd/gem.avd`, using Android 11
(API 30), Google APIs, x86_64. The SDK is under `$HOME/Library/Android/sdk`.
The installed `com.gemstone.lights` reports version 0.6.64 / build 664 and was
already signed in. Its active Shorebird patch version has not been established.

Public APKPure downloads supplied these signed builds:

| Version | Architecture inspected | Analysis |
| --- | --- | --- |
| 0.6.64, build 664 | ARMv7 | APK inventory, Java decompilation, native strings |
| 0.6.31, build 631 | ARM64 | Native strings and attempted Dart snapshot analysis |
| 0.5.77, build 577 | ARM64 | Confirms the same Dart 3.9.2 snapshot format as 0.6.31 |
| 0.5.17, build 517 | ARM64 | Older Dart 3.6.2 snapshot; same decoder incompatibility |

The current mirror's web/search metadata was inconsistent with the downloaded
version. The versions above come from the downloaded XAPK manifests and binaries.
Android's `apksigner` verified the 0.6.64 base APK and the inspected 0.6.31 and
0.5.17 native splits. Their signing certificate SHA-256 matched:

```text
6b341c5ead282e3b9fe15f0db424bb91b49e450954c3f0ce7be7070d91326646
```

This checks signature integrity and continuity between these mirror artifacts;
the certificate has not been independently compared with an owner-installed
Google Play copy.

Artifact SHA-256 values:

```text
0.6.64 base APK:
a373e1ff771a99f84aa7051b792852e4e45048b384852f60aa23ca5080e85557
0.6.31 ARM64 split APK:
671415b16e7531649a3142fdcde3b4de008bedb06e6097b03a87864bbb47fcf7
0.5.17 ARM64 split APK:
5c73660c316362f28140488f6836b943af6925547a4f0a1ea4154067df42ea32
```

The app uses Flutter AOT with Shorebird Code Push. Most application logic lives
in `libapp.so`, not the Java/Dex classes. JADX 1.5.6 produced Java output but
reported 940 decompilation errors; that output is not a complete decompilation.
It identifies Android plugins and the entry activity, not the Dart protocol logic.

Blutter built matching nominal Dart 3.9.2 and 3.6.2 runtimes, but both failed in
class deserialization. The app snapshot hashes differ from stock Dart. Native
Shorebird exports and the packaged `shorebird.yaml` identify the modified runtime.
The publicly distributed Shorebird iOS `analyze_snapshot_arm64` was also tried;
it explicitly rejected the Android compressed-pointer snapshot configuration.
No valid object-pool dump or annotated Dart function disassembly was obtained.

[Shorebird documents its modified runtime and app-code updates](https://docs.shorebird.dev/code-push/system-architecture/).
Consequently, an APK version alone may not identify the code currently running in
the owner's app. Record its active patch version when capturing behavior.

All APKs, extracted vendor code, downloaded tools, and diagnostic dumps remain
outside this repository in `/private/tmp/gemstone-apk-research`. Temporary files
may be removed by macOS. This repository contains original findings only.

## Findings by capability

The identifiers below were found in app binaries. Unless stated otherwise,
they are **static leads**, not verified call graphs, HTTP routes on the controller,
GATT mappings, or accepted command payloads. Compiler snapshot metadata can adjoin
string literals; raw `strings` output must not be copied blindly into requests.

| Capability | Concrete evidence | Remaining work |
| --- | --- | --- |
| Music sync | Live UDP 1902 captures; eight Base64/hex levels; synthetic-input response. Static FFT and `musicModeUdpPortChange` identifiers. | Resolve port refusal on tested firmware, recover initialization and frequency-band mapping, then verify physical response with WAN blocked. See the live results below. |
| Native timers | `BluetoothReadTimerDataCmdResponse`, `readNumberOfTimers`, `readTimerData`, `setTimerShadowState`, `setTimerEnabledShadowState`, and cloud `/timer/create`, `/timer/update`, `/timer/delete` strings | Capture BLE reads and one reversible timer edit. Determine whether native timer writes are available over LAN or BLE, and how clock/DST and enabled state are encoded. |
| Native zones | `zone_service.dart`, `zone_notifier.dart`, `/deviceControl/zone/list`, `/save`, `/delete`, `/reset` strings | Capture creating and deleting a temporary zone. Determine the actual transport and controller-side zone-definition format. Existing v1.6.0 arbitrary-zone limitations still apply. |
| Native playlists | `playlist_service.dart`, `/deviceControl/playlist/list`, `/save`, `/delete`, `/deviceControl/play/playlist` strings | Separate app/cloud catalog CRUD from controller playback/upload. Capture a short two-step playlist and its stop operation. |
| Bluetooth control | `bluetooth_packet.dart`, `BluetoothCmdHeader`, `writeControllerState`, controller-state response types, `writeBluetoothPassword`, `_getBluetoothPasswordFromStorage` | GATT mapping, firmware/state/settings reads, state-write framing and success acknowledgment verified in [Bluetooth protocol](BLUETOOTH_PROTOCOL.md). Authentication on protected firmware remains unverified. |
| Provisioning | `writeSsid`, `writePassword`, `requestLocalIp`, cloud `/deviceManagement/bluetoothPassword`, Bluetooth password caching | Determine whether a new controller can be provisioned account-free or whether a credential must first be obtained online. Do not factory-reset the installed controller to test this. |
| Settings and clock | `setPixelCount`, `setTimeZone`, `setTimeThroughBluetooth`, `setTcpEnabled`; live LAN settings read succeeds | Establish which writes use BLE, LAN, or cloud. Test only reversible settings with a saved original value; output wiring/count changes need an appropriate test controller. |
| Firmware and downloadable animations | `MicropythonService`, `downloadMicropythonFileLinks`, OTA-related API names | These do not prove a local update route. Obtain legitimate packages, identify integrity checks and update handshake, and establish recovery before any update experiment. |
| Accounts, sharing, catalogs, Autopilot | Cloud service paths coexist with local music/BLE code | Independent local HA catalogs and automations already cover some workflows. New vendor content and online account services still need an online source or prior offline transfer. |

The [vendor's music documentation](https://www.gemstonelights.com/support/music-sync/)
confirms phone microphone input, same-Wi-Fi operation, and individual-controller
support. The [Bluetooth documentation](https://www.gemstonelights.com/support/connect-to-wifi/)
confirms local Bluetooth control and limits it to one connected client at a time;
firmware updates require Wi-Fi. Neither document specifies wire protocols.

## Live music captures and replay

The emulator's built-in network capture recorded traffic at its virtual network
interface. Its host microphone was explicitly disabled and checked through the
emulator API. Synthetic mono PCM, 44.1 kHz signed 16-bit audio, was injected through
the authenticated emulator gRPC interface. No room audio was recorded. The app
received a temporary microphone permission so it could read this synthetic input.

Three stopped captures contained 1,242, 772 and 746 outgoing controller music
datagrams respectively. The first lasted approximately 75.5 seconds, with a median
inter-packet interval of 60.889 ms. These are observed timings, not a proven
firmware requirement. The destination was the controller's LAN address, UDP 1902;
the app used an ephemeral source port. No controller UDP acknowledgment was seen.

Each observed UDP payload was 24 ASCII bytes. Base64 decoding yields 16 lowercase
hexadecimal characters; interpreting each pair gives eight unsigned byte values.
There was no additional header, sequence number or timestamp in these payloads.
Sanitized examples from the captures:

| UDP payload (ASCII) | Base64-decoded text | Eight levels |
| --- | --- | --- |
| `MDAwMDAwMDAwMDAwMDAwMA==` | `0000000000000000` | 0, 0, 0, 0, 0, 0, 0, 0 |
| `MDkwOTAwMDAwMDAwMDAwMA==` | `0909000000000000` | 9, 9, 0, 0, 0, 0, 0, 0 |
| `ZmZmZmZmZmZmZmZmYTI1NQ==` | `ffffffffffffa255` | 255, 255, 255, 255, 255, 255, 162, 85 |

Synthetic tones from 80 Hz through 10 kHz changed both the app's eight displayed
bars and the transmitted values. Silence usually produced zeros, with small
residual values also observed. Exact FFT band boundaries, scaling, smoothing and
silence behavior have not been recovered. The tone experiment does not establish
a frequency-to-index mapping or calibrated sound level.

Selecting Music Gradient 1 in the app produced this controller-reported pattern:

```json
{
  "name": "Music Gradient 1",
  "animation": "music_gradient_bar",
  "id": "90000000-0000-0000-0000-000000000001",
  "backgroundColor": 0,
  "brightness": 255,
  "speed": 255,
  "direction": 0,
  "colors": [255, 65280]
}
```

These `90000000-...` identifiers are preset IDs, not evidence of Bluetooth GATT
UUIDs. No direct TCP request to the controller was observed during the captured
app selection. Cloud traffic was encrypted; the initial selection command has
not been decoded. A cloud-mediated selection is a hypothesis, not an established
requirement.

Independent replay posted that pattern to the existing local
`/device-control/play` endpoint with `onState: true`, cleared the inactive modes,
and sent the captured framing to UDP 1902 at approximately the observed cadence.
Tests used all-zero, all-255 and single-band vectors, both a generated pattern ID
and the exact vendor ID, and brightness values 64 and 255. The controller echoed
the music animation, but camera frames showed no visible music output. A normal
solid-red command at brightness 128 visibly illuminated the same roofline.

A separate vendor-app test injected synthetic tones while capturing camera
frames and network traffic. The app displayed Connected, a playing music preset
and changing audio bars. A camera frame taken during nonzero outgoing music
levels also showed no visible roofline output. This rules out treating the app's
Connected label or a matching HTTP state report as proof of working music.

Finally, a connected UDP socket sent the observed silence payload to port 1902.
All three probes while the lights were off and all three while the controller
reported `music_gradient_bar` returned `ConnectionRefusedError` / errno 61
(`ECONNREFUSED`). This is consistent with a closed or explicitly rejected UDP
endpoint; the ICMP origin was not independently captured. The emulator host uses
a routed network path, while the independent sender is on the controller's local
subnet. HTTP reads and ordinary light commands work from both hosts.

The controller reports firmware 1.1.5, SPI 1.2.1 and Wi-Fi 3.3.9. Its local
settings report `tcpEnabled: true` but no music/UDP port field. The static
`musicModeUdpPortChange` identifier is a useful compatibility lead; its version
threshold and alternate port have not been recovered. No port scan, guessed
alternate-port playback, controller reset or firmware update was performed.

All senders and captures were stopped, the app was force-stopped, and the original
light state was restored and verified after each test sequence. Raw captures,
camera frames, app artifacts and emulator credentials remain outside the repo.

## Next capture session

**Update:** the [native management investigation](MANAGEMENT_PROTOCOL.md) has now
verified zone inventory and native creation/deletion, timer count and native
creation/deletion, plus Wi-Fi RSSI. Timer detail reads, clock semantics and complete
zone geometry decoding remain open. The earlier list below is the starting plan;
consult that report for completed work and current limits.

Use the running **Hub2** app, preferably in the owner's Android emulator. A
connected Android phone is also useful. Record package version, active Shorebird
patch, controller firmware, and whether the app is using Wi-Fi or Bluetooth.

1. Establish whether the owner's phone currently produces physical music output
   on the controller's Wi-Fi. Compare its destination port, app/patch version,
   firmware view and initialization with the emulator. Resolve the observed
   port refusal before changing the audio encoder or implementing an HA sender.
2. Recover the port-selection gate and initial command, then repeat a bounded
   tone/silence/preset-switch test with fresh saved state, packet timestamps and
   physical observation. Always restore and read back the original state.
3. Repeat with the controller's Internet access blocked while its LAN remains
   reachable. Separately test whether the app can enter music mode without cloud
   access; local audio transport does not prove cloud-free initialization.
4. Capture Bluetooth HCI traffic for timer reads and a reversible edit, with the
   owner's phone/emulator as the sole BLE client. Preserve captures privately:
   they may contain pairing credentials or network configuration.
5. Capture temporary zone/playlist creation, playback, and deletion one operation
   at a time. Use restored state and isolated test content. Avoid settings resets
   and firmware writes on the installed controller.

For each recovered protocol, retain sanitized packet examples, implement the
encoding/decoding independently, and add Given/When/Then tests for real data
transformations and external I/O behavior before enabling it in HA. Logical
changes should have separate commits and share one reviewed PR/release.

Audio-source selection is intentionally deferred at the owner's request until
the protocol investigation is complete. No microphone bridge or audio UI has
been added. Full controller WAN isolation has also not yet been verified.
