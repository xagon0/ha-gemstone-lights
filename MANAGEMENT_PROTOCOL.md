# Hub2 native management investigation

Investigated 2026-09-06 using Android app 0.6.64, provisioned Hub2 firmware 1.1.5
(SPI 1.2.1, Wi-Fi 3.3.9) and the existing HA 2026.9.1 installation. These are
original protocol observations, not a vendor specification. The
investigation was conducted against integration 1.7.0. Version 1.7.1 fixes zone
geometry in existing control workflows; the management operations below are
**not HA features**. Controller scheduling and additional diagnostic sensors
are not being added as part of that reliability fix.

## Findings that change the implementation options

| Capability | Evidence | Practical opportunity and limit |
| --- | --- | --- |
| Native zone inventory | Physical zone-count and paginated zone-detail reads returned all four existing zones, including IDs, names, icons, update timestamps and geometry. | Account-free zone discovery is possible over BLE. Version 1.7.1 decodes mixed explicit/compressed geometry; native inventory synchronization is not implemented. |
| Native zone creation/deletion | An app-captured `setZone` command created one temporary two-pixel zone; `deleteZone` removed it. Inventory changed 4 → 5 → 4, with every original record unchanged. | HA could register native zone IDs before playing animated designs. This test did not establish physical animation rendering on the new zone. |
| Native timer creation/deletion | App-captured `setTimer` created a temporary future timer; `deleteTimer` removed it. Inventory changed 0 → 1 → 0. `setTimerEnabled: false` received success acknowledgment. | Native schedule management is possible over BLE. Timer detail reads, enabled-state readback, execution timing, DST and solar behavior remain unverified. |
| LAN management through the playback route | Posting the timer management envelope to the existing `/device-control/play` route returned HTTP 400 and left the timer inventory empty. | The playback endpoint cannot simply be reused for this payload. This does not prove that no other LAN management route exists. |
| Controller Wi-Fi signal | App-generated read `23` returned `23 cf` on the real controller. Replaying that response into the app displayed −49 dBm. | Local Wi-Fi signal diagnostics are possible over BLE. This is the controller's Wi-Fi RSSI, not HA's BLE RSSI. |
| Additional existing LAN data | `hub-settings` already reports component firmware, network interface/preference, output reversal, timezone offset and DST settings. | These can improve HA diagnostics without a new transport or controller mutation. |

For this installation, the HA Bluetooth adapter path timed out during the earlier
1.7.0 validation. Direct Mac BLE reads and writes succeeded. Reliable HA Bluetooth
coverage is still needed before exposing these management operations as usable
features on that HA installation. LAN remains selected there.

## Wire format

These requests use the same service, characteristic and `<BHH` fragmented JSON
format documented in [BLUETOOTH_PROTOCOL.md](BLUETOOTH_PROTOCOL.md). Subscribe
before requests and serialize complete exchanges. An application acknowledgment
is distinct from successful delivery of a GATT packet.

| Request | Verified response | Interpretation |
| --- | --- | --- |
| `23` | `23 cf` in the physical sample | Wi-Fi RSSI: signed 8-bit value after opcode, −49 dBm in this sample. |
| `33` | `33 00` initially; `33 01` with temporary timer | Native timer count, one byte after opcode. |
| `35` | `35 04` initially; `35 05` with temporary zone | Native zone count, one byte after opcode. |
| `36 00 02` | Framed JSON `{"zones":[...]}` | Two zone records beginning at index 0. |
| `36 02 02` | Same JSON shape | Two records beginning at index 2. |
| `36 04 01` | Same JSON shape during five-zone test | One final record beginning at index 4. This request was derived from the captured pagination and verified physically. |
| Framed `30` with management JSON | `30 01` for successful writes | Apply a desired-state management operation. `30 e1` is rejection; do not invent a more specific meaning. |

Count requests are one byte. Zone-detail requests are three bytes: opcode,
zero-based start index and requested record count. The app requested two records
per batch. The first physical two-zone response used frames of 240 and 44 bytes;
the next used 240 and 48 bytes. Their five-byte headers reported two fragments.
The byte fields do not establish a hardware limit on how many zones/timers can
be stored.

## Verified management envelopes

The BLE envelope is `{"state":{"desired":{...}}}`. These management keys are
siblings of `currentlyPlaying`, not fields inside it. The v1.7.0 lighting client
therefore cannot expose them merely by adding more lighting-state fields.

Sanitized timer creation example:

```json
{
  "state": {
    "desired": {
      "setTimer": {
        "id": "<new-timer-id>",
        "name": "Example timer",
        "timerData": {
          "onTime": "23:57",
          "offTime": "23:58",
          "timerType": "daily"
        },
        "assigneeId": "<controller-id>",
        "lastUpdatedAt": 1788740732,
        "color": 10197760
      }
    }
  }
}
```

The app capture used `onTime: "sunset"` and `offTime: "23:00"`. The physical
probe substituted future clock times to avoid triggering a lighting change.
Its temporary timer was immediately disabled and deleted. No scheduled event
was allowed to fire. The daily structure is verified; weekly/yearly structures,
pattern/design/playlist timer payloads and schedule execution are not.

Other captured and physically accepted desired-state objects:

```json
{"setTimerEnabled":{"id":"<timer-id>","enabled":false}}
{"deleteTimer":{"timerId":"<timer-id>"}}
{"setZone":{"name":"Example zone","id":"<new-zone-id>","lights":[0,1],"icon":"house_3"}}
{"deleteZone":{"zoneId":"<zone-id>"}}
```

The identifier field names differ: `id` when setting a timer or zone, `timerId`
when deleting a timer and `zoneId` when deleting a zone. No reset-all command was
used. A disable request for a nonexistent timer was rejected; cleanup must not
stop before checking deletion/inventory just because a preceding disable fails.

The timer probe verified storage by count and cleanup by count. It did not read
the timer object back, so acknowledgment alone must not be presented as proof of
its exact stored content or enabled flag. A production editor needs those reads
before promising reliable reconciliation or editing existing schedules.

## Zone geometry and an existing integration limitation

The app-created two-pixel zone used `lights: [0,1]`, and the physical controller
returned that exact array. An isolated app capture for two separate three-pixel
runs produced `[0,1,2,4,5,6]`. Those six indices were selected individually in the
app; this latter zone was not sent to the physical controller.

Existing longer zones use a compressed representation. A native record with
`[0,0,102]` opens in the app editor with intermediate pixels selected (the first
25 were visible), rather than only indices 0 and 102. The app binary also contains
`LightsCompressor` and `lights_compressor.dart`. A subsequent isolated-app check
supplied `[0,0,3,5,5,8,10]`. The editor selected
indices 0–3, 5–8 and 10, leaving 4 and 9 clear. This confirms repeated-start
`[start,start,end]` inclusive range markers interspersed with literal indices.
The new decoder bounds expansion and rejects malformed markers, duplicates and
invalid indices. The integration exports explicit indices rather than attempting
to reproduce the vendor encoder's compression threshold.

Before 1.7.1, the coordinator's `zone_ranges` method required at least three elements
and used only the final two as start/end. It skipped the verified two-pixel
zone, and would treat `[0,1,2,4,5,6]` as only pixels 5–6. Version 1.7.1 replaces that assumption with complete pixel
selections for
control, readback, native matching, overlap checks and portable catalogs. Its
existing start/end editing action remains a contiguous-range interface; importing
existing noncontiguous geometry preserves its pixels. Legacy local records are
migrated separately because HA previously wrote a different count/start/end format.
The owner's four existing contiguous zones were preserved in this probe.

One isolated app save interleaved two multi-fragment state writes while previewing
and saving a zone. The capture contained enough JSON to reconstruct the zone
object, but this is not proof that firmware tolerates interleaving. The production
transport should continue serializing whole commands.

## LAN diagnostics available now

The existing settings endpoint provides these fields without additional commands:

| Field | Observed type / meaning |
| --- | --- |
| `firmware`, `firmwareSpi`, `firmwareWifi` | Separate firmware version strings. |
| `network.interface`, `network.preferred` | Active interface identifier and network preference; sample values `wifi-bw236b` and `auto`. |
| `reversePixels` | Per-output booleans. |
| `timeZone` | Offset string, e.g. `-07:00`, not an IANA timezone name. |
| `dstActive`, `dstMode` | The tested firmware sends `dstActive` as a string (`"true"`), with mode `auto`. Normalize explicitly; `bool("false")` is incorrect. |
| `tcpEnabled` | Boolean local-HTTP enablement. |
| `pixelCount`, `pixelOutputNames`, `rgbwSequence` | Already partly exposed by the integration. |

Expose freshness alongside settings that can be cached after a failed read.
Do not infer an IANA zone or a current effective UTC offset from the offset and
DST fields without verifying the controller's clock semantics. Location and
network identifiers can be private; avoid indiscriminate raw-settings exports.

## Remaining investigation

- Timer detail reads: static app code names them, but the tested offline app did
  not request them after a simulated nonzero count. The feature/account-state
  gate is unresolved. No guessed timer-detail opcode was sent to the controller.
- Native zone editing: creation/deletion is established, but native resizing, state reconciliation and physical animated rendering need
  validation before an HA editor is released.
- Clock/DST/solar behavior: a 20-byte `41` command was captured at app connection
  as in the previous investigation; it was not replayed or decoded. Merely opening
  timezone settings did not reveal a new clock read.
- Other LAN routes: HTTP 400 on the playback route is a boundary for that tested
  route and payload, not a complete enumeration of management APIs.
- Music and firmware: no new evidence resolves the earlier music port refusal,
  music initialization, protected firmware or firmware update protocol.

Raw captures, APKs, IDs and rollback records remain outside the repository.
The emulator used simulated Bluetooth I/O and had no network connectivity;
unknown writes stayed there. Physical probes used only captured operations plus
the verified pagination variation. Timer and zone inventories and lighting state
were restored. The instrumentation was detached, the app force-stopped and the
emulator shut down with its original network settings preserved. Physical WAN
isolation was not tested, and no claim of complete offline parity is made.
