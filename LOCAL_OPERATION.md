# Local operation and remaining protocol gaps

Investigation date: 2026-09-06. Software baseline: Home Assistant 2026.9.1;
hardware tested: Gemstone Hub2 firmware 1.1.5.

This integration can run without a Gemstone account and can explicitly disable
all Gemstone cloud requests. **That is not complete parity with every function
in the vendor app.** The table below distinguishes implemented offline workflows
from unresolved controller features.

## Capability inventory

| Capability | Offline implementation | Boundary / remaining work |
| --- | --- | --- |
| Power, RGBW, brightness, speed, whole-run effects | Direct LAN control | Controller must already permit local commands. |
| Static architectural designs and repeating palettes | Direct explicit-pixel LAN designs | 15 KiB controller packet limit. |
| Existing animated zones | Native `zonePatterns` on verified firmware 1.1.5 | Requires the original controller zone IDs and unchanged ranges cached in the integration. Other firmware remains unverified. |
| Create/edit static zones | HA local catalog and dynamic light entities | Inclusive pixel indices 0–4095; overlapping ranges are rejected. |
| Create/resize native animated zones | Unresolved | Sending a new zone ID with `lights` does not create a native zone. HA reports this limitation instead of sending a non-rendering design. |
| Create/edit/save patterns and designs | Local actions and existing selects | Local names override imported vendor names; nothing is uploaded. |
| Pattern folders | Local pattern folder labels and imported library folders | No standalone visual folder editor or cloud synchronization. |
| Downloaded official library | Persisted local library, portable import/export | New vendor content must be downloaded or transferred before disconnecting. |
| Playlists | Included HA script blueprint | HA advances the sequence. Stop the script before manual control; HA restart stops playback advancement. No native playlist CRUD/upload protocol verified. |
| Weekly schedules | Included HA Schedule-helper automation blueprint | HA must run; reconciles the schedule at HA startup. |
| Sunrise/sunset | Included local Sun automation blueprint | Uses HA's configured location and clock. HA must run. |
| Existing controller timers | Vendor documents offline operation | Native timer read/create/edit/delete endpoints remain unverified. HA blueprints do not edit these timers. |
| Holiday/seasonal automation | HA local calendars, date conditions, scripts and locally stored patterns | Vendor Autopilot subscriptions and automatic new-content delivery are not replicated. |
| Groups and scenes | HA light groups/scenes and multi-entity action targets | Commands are independent; no frame-accurate multi-controller synchronization. |
| Music sync | Unresolved | Vendor describes phone microphone input. No verified audio/beat transport or local capture engine in this integration. |
| Firmware, output counts, color order | Read locally from `hub-settings` | Writing output configuration, network settings, clock/location or firmware is not implemented. |
| Account-free HA setup | Manual controller address | Existing provisioned Hub2 only. Reserve its DHCP address; this is not Bluetooth/Wi-Fi provisioning. |
| Bluetooth / factory-new provisioning | Unresolved in this integration | Vendor app has local Bluetooth control; its pairing/GATT protocol has not been implemented here. |
| Firmware upgrades | Unresolved | No verified local update protocol or independently supplied firmware package. |
| Multi-user access / backups | HA users and local HA backups | Vendor account services, remote cloud access and online sharing remain online services. |

HA and the controller still need power and a working LAN. “Local-only” controls
this integration's outbound behavior; it does not change the controller's router
firewall rules, prevent the vendor app from making requests, or disable other HA
integrations. A working local clock matters for schedules after a power outage.

## Set up without an account

1. On an already provisioned Hub2, enable **Allow Local Commands** in the vendor
   app if it is not enabled yet.
2. Add Gemstone Lights in HA and choose **Local controller (no account or internet)**.
3. Enter its IPv4 address or hostname and a name. No email, password, token,
   Cognito login, cloud discovery, or library request is used.
4. Reserve the controller's address in the router. Use the integration options
   to change the address later; the existing entity identities stay unchanged.

To preserve existing entity IDs and downloaded content, use **Configure → Disable
all Gemstone cloud access** on the existing account entry. It retains cached
controllers, zones, designs, library content and logical brightness state. Cloud
credentials in that existing entry are not used while the option is enabled.
Do not delete and re-add the entry to switch modes.

An offline HA installation also needs the integration's Python dependencies
already installed or supplied offline. Initial HACS installation, dependency
installation and downloading new releases are separate from offline operation.
A full HA backup is the recommended recovery mechanism for existing controller
identities and native-zone metadata.

## Local editing and backups

Use **Developer Tools → Actions**. Catalog actions require a Home Assistant
administrator and accept the whole-controller light in `controller`. These
editing actions work even while that controller is temporarily unavailable.

```yaml
action: gemstone_lights.save_content
data:
  controller: light.your_controller
  kind: pattern
  name: Green and white chase
  folder: My patterns
  content:
    colors: [65280, 4278190080, 0, 0]
    animation: chase
    brightness: 128
    speed: 100
    direction: 0
    backgroundColor: 0
```

The new name appears in the Pattern select. Save the same name to replace it.
`kind: design` accepts an architectural object with `zonePatterns` or
`staticColors`. `kind: zone` accepts `content: {start: 20, end: 29}` and creates a
new light entity. These indices address the controller's physical output layout;
do not assume every output immediately follows the preceding output's last LED.
Use existing zone ranges or installer documentation to identify the right pixels.

`save_current` takes `controller` and `name` to save the last known pattern, color
or design. `delete_content` takes `controller`, `kind` and `name`; it deletes only
the local item. A vendor item hidden by the same local name becomes visible again.

`export_catalog` takes `controller` and returns a versioned object containing
patterns, designs, zone geometry and downloaded library content. In an automation,
use `response_variable` to access it. Save that response as a local file or in a
backup. `import_catalog` accepts it in the `catalog` field; it validates the whole
input before merging matching names. It contains no device addresses or account
credentials. Importing content does not provision firmware zone definitions on
another controller. Native animated-zone identity is preserved by a full HA
backup of the original entry, not by this portable content import alone.

`play_content` takes `kind: pattern` or `kind: design` and a `content` object,
using the normal light `target`. Patterns can target a zone; designs must target
the controller. It plays immediately without saving a catalog item.

Custom content receives generated identity fields and default brightness, speed,
direction and background values when omitted. Direction values 0–5 are accepted,
matching the patterns observed in the vendor catalog.

Local edits and imported libraries are stored separately from vendor catalogs,
so a subsequent cloud refresh cannot erase them. Local data is included in HA's
`.storage/gemstone_lights.<entry_id>` backup. Do not edit that storage manually.

## Playlists and schedules

Import these blueprint URLs using HA's blueprint import interface:

- [Local playlist script](https://github.com/xagon0/ha-gemstone-lights/blob/main/blueprints/script/gemstone_lights/local_playlist.yaml)
- [Weekly schedule automation](https://github.com/xagon0/ha-gemstone-lights/blob/main/blueprints/automation/gemstone_lights/local_schedule.yaml)
- [Sunset-to-sunrise automation](https://github.com/xagon0/ha-gemstone-lights/blob/main/blueprints/automation/gemstone_lights/local_sun.yaml)

For an offline installation, copy the files from this repository's `blueprints`
folder into HA's corresponding `blueprints/script` or `blueprints/automation`
folder. Importing the GitHub URLs is only an installation convenience.

Example playlist `steps` input:

```yaml
- kind: pattern
  content:
    colors: [255, 0, 0]
    animation: chase
    speed: 100
  seconds: 30
- kind: pattern
  content:
    colors: [65280, 4278190080]
    animation: motionless
  seconds: 60
```

Choose one pass, a finite number of passes, or zero for continuous repetition.
Intervals are at least two seconds to respect Hub2 write pacing. Stop the script
before manually changing lights or starting an overlapping schedule, otherwise
its next step will replace the manual selection. A stopped script leaves the
last pattern playing; switch the light off separately if desired.

For weekly schedules, create a **Schedule** helper in HA, then select it in the
blueprint. For solar schedules, configure HA's **Sun** integration and location.
Existing vendor timers can still override HA commands; adjust those in the vendor
app before making HA the schedule owner. These HA schedules are not installed
on the controller and cannot run while HA is shut down.

## Evidence and next protocol investigations

The [Android app investigation](APK_PROTOCOL_RESEARCH.md) records newer static
evidence for local music transport and Bluetooth timer operations, the Shorebird
decoder limitation, and the captures required to establish their wire formats.
These findings do not change the implemented capability boundaries above.

Direct LAN tests sent new design IDs with an existing lower zone running a green
chase and an existing upper zone set blue. Hub2 returned the nested animation,
palette and brightness fields intact. Two doorbell camera frames showed the
visible lower LEDs advancing. The upper roofline was outside the useful camera
view, so its physical blue output was not independently checked in that probe.
All probes restored and read back the exact starting controller state.

A control probe replaced the lower zone with a new random ID and supplied
`lights: [103, 0, 102]`. The controller removed `lights` from its report and the
visible lower LEDs stayed dark. An HTTP 200 and a matching state echo therefore
must not be treated as proof that arbitrary new native zones work.

The readable documentation/XML in the vendor-linked Control4 driver exposes
custom pattern JSON and architectural playback. Its Lua implementation is
encrypted; it was not decrypted. Read-only guesses for `/device-state/zones`,
`timers`, `playlists`, `architectural`, `patterns`, and `device-settings` returned
404. Those results do not prove that no management protocol exists.

Next evidence needed for the unresolved rows: capture the owner's app traffic
while creating a zone/timer/playlist and changing settings; identify the official
Bluetooth GATT services and framing; determine whether management uses local HTTP,
Bluetooth or cloud-to-device messages; replay only verified reversible requests.
Firmware work additionally needs a legitimate firmware package, a documented
update handshake and a recovery path. Music sync needs capture of the phone's
beat/event stream and timing behavior. None of these gaps is solved by repeatedly
posting unknown JSON fields to the playback endpoint.

Automated tests run the actual integration logic with external HTTP substituted,
including credential-free setup, zero cloud URLs, LAN failure/recovery, cache
migration, catalog persistence, atomic import, native payload routing, and the
shipped HA script/automation engines. The physical controller's WAN isolation
has not yet been independently tested; LAN visual success alone is not that test.

Primary vendor sources:

- [Hub2 feature inventory](https://www.gemstonelights.com/app/hub2-upgrade/) — offline controller timers, Autopilot, cloud syncing and phone-based music sync.
- [Control4 setup](https://www.gemstonelights.com/support/control4/) — enabling LAN commands, SDDP discovery and the vendor-linked driver.
- [Bluetooth and Wi-Fi setup](https://www.gemstonelights.com/support/connect-to-wifi/) — local Bluetooth control, pairing workflow and limitations.
