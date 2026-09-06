# Hub2 Android protocol investigation

Investigation date: 2026-09-06. Integration baseline: v1.6.0. Live controller:
Hub2 firmware 1.1.5. This report supplements [Local operation](LOCAL_OPERATION.md).

## Result

Music sync is a strong candidate for an independent local implementation. The
Hub2 app contains a UDP client, microphone recording, FFT processing, music
presets, and firmware-dependent UDP configuration. The vendor explicitly requires
the phone and controller to share Wi-Fi. **The wire format, destination port,
initialization sequence, and timing have not been recovered or tested.** This is
evidence of a promising implementation path, not a working music integration.

Bluetooth timer and controller-state operations are also present. Native zone
creation, playlists, provisioning, settings writes, and firmware updates remain
unverified. No new commands were sent to the controller during this investigation.
Home Assistant was reachable and reported the whole-controller light off with
local control enabled.

## App identity and reproducibility

The correct Hub2 Android package is `com.gemstone.lights`, linked from its
[Google Play listing](https://play.google.com/store/apps/details?id=com.gemstone.lights).
The installed Mac application is the older `com.gemstone.gemstonehub` / Hub1
application and is not a suitable Hub2 protocol reference.

Android platform tools and an SDK were found on this Mac. ADB reported no connected
devices, and the local AVD directory contained no configured emulator. This does
not establish whether an emulator exists on another computer or inside a VM.

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
| Music sync | `services/udp_client.dart`, `MusicVisualizerNotifier`, `_calculateFFT`, `_createLogarithmicFrequencyBands`, `frequencyBins`, `clampMagnitude`, `musicModeUdpPortChange`, microphone recording plugin, `music_pulse` / `music_gradient_bar` presets | Capture start, steady audio, silence, preset switch, and stop. Recover UDP endpoint, framing, fields, cadence, and firmware gate. Verify physical response and restoration. |
| Native timers | `BluetoothReadTimerDataCmdResponse`, `readNumberOfTimers`, `readTimerData`, `setTimerShadowState`, `setTimerEnabledShadowState`, and cloud `/timer/create`, `/timer/update`, `/timer/delete` strings | Capture BLE reads and one reversible timer edit. Determine whether native timer writes are available over LAN or BLE, and how clock/DST and enabled state are encoded. |
| Native zones | `zone_service.dart`, `zone_notifier.dart`, `/deviceControl/zone/list`, `/save`, `/delete`, `/reset` strings | Capture creating and deleting a temporary zone. Determine the actual transport and controller-side zone-definition format. Existing v1.6.0 arbitrary-zone limitations still apply. |
| Native playlists | `playlist_service.dart`, `/deviceControl/playlist/list`, `/save`, `/delete`, `/deviceControl/play/playlist` strings | Separate app/cloud catalog CRUD from controller playback/upload. Capture a short two-step playlist and its stop operation. |
| Bluetooth control | `bluetooth_packet.dart`, `BluetoothCmdHeader`, `writeControllerState`, controller-state response types, `writeBluetoothPassword`, `_getBluetoothPasswordFromStorage` | Map GATT services/characteristics, framing, fragmentation, opcodes, acknowledgments, and authentication. No UUID-to-operation mapping or packet format is established yet. |
| Provisioning | `writeSsid`, `writePassword`, `requestLocalIp`, cloud `/deviceManagement/bluetoothPassword`, Bluetooth password caching | Determine whether a new controller can be provisioned account-free or whether a credential must first be obtained online. Do not factory-reset the installed controller to test this. |
| Settings and clock | `setPixelCount`, `setTimeZone`, `setTimeThroughBluetooth`, `setTcpEnabled`; live LAN settings read succeeds | Establish which writes use BLE, LAN, or cloud. Test only reversible settings with a saved original value; output wiring/count changes need an appropriate test controller. |
| Firmware and downloadable animations | `MicropythonService`, `downloadMicropythonFileLinks`, OTA-related API names | These do not prove a local update route. Obtain legitimate packages, identify integrity checks and update handshake, and establish recovery before any update experiment. |
| Accounts, sharing, catalogs, Autopilot | Cloud service paths coexist with local music/BLE code | Independent local HA catalogs and automations already cover some workflows. New vendor content and online account services still need an online source or prior offline transfer. |

The [vendor's music documentation](https://www.gemstonelights.com/support/music-sync/)
confirms phone microphone input, same-Wi-Fi operation, and individual-controller
support. The [Bluetooth documentation](https://www.gemstonelights.com/support/connect-to-wifi/)
confirms local Bluetooth control and limits it to one connected client at a time;
firmware updates require Wi-Fi. Neither document specifies wire protocols.

## Next capture session

Use the running **Hub2** app, preferably in the owner's Android emulator. A
connected Android phone is also useful. Record package version, active Shorebird
patch, controller firmware, and whether the app is using Wi-Fi or Bluetooth.

1. Capture only traffic involving the test controller. With an emulator on the
   capture host this avoids relying on a switched LAN to expose phone unicast
   traffic. UDP music traffic does not need HTTPS decryption.
2. Save fresh controller state; start a music preset at low brightness, supply a
   controlled tone and silence, switch preset, stop, then restore and read back
   the original state. Correlate packet timestamps with each action and the
   doorbell view. An HTTP success/state echo alone does not verify LED behavior.
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
