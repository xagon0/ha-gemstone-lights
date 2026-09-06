# Changelog

## 1.6.0 — 2026-09-06

- Add account-free setup by LAN address and an explicit option that disables all
  cloud authentication, discovery, catalog refreshes and command fallback.
- Add local pattern/design/zone editing, current-content saving, custom playback,
  portable catalog import/export and protection from cloud catalog overwrites.
- Render existing native animated zones over LAN on verified Hub2 firmware 1.1.5.
  Direct controller commands and timed camera frames confirmed chase movement.
- Expand motionless zone palettes into explicit pixels, supporting new HA-only
  static zones. Reject new/resized native animated zones until a zone-definition
  management protocol is verified.
- Provide HA script/automation blueprints for playlists, weekly schedules and
  sunset-to-sunrise operation, tested in the real HA automation engine.
- Request separate HTTP connections to avoid observed embedded-server stalls.
- Document a complete capability inventory and unresolved provisioning, music,
  firmware, settings and native schedule/playlist management protocols. This is
  not a claim of complete offline parity with every function in the vendor app.
- Extend the behavior suite to 69 tests with functional Given/When/Then assertions.

## 1.5.2 — 2026-09-06

- Retain logical zone colors and brightness after master dimming through the
  cloud. Hub2 copies the master level into each nested pattern's reported
  brightness; reconcile that redundant field without treating it as another
  brightness multiplier or losing the original palette.
- Extend the hardware-derived regression to poll after master dimming before
  brightening again. It reproduces the incorrect zone levels on 1.5.1.

## 1.5.1 — 2026-09-06

- Correct independent brightness for animated zones sent through the cloud. Live
  testing on Hub2 firmware 1.1.5 found that cloud playback resets each nested
  pattern's brightness to the master level (255 at full brightness). Encode
  independent dimming in every RGBW palette and
  background color while retaining the logical color and level in Home Assistant.
- Preserve those logical zone values through normalized controller echoes, local
  write failures, cloud power changes, and repeated dim/brighten operations.
- Schedule the command follow-up poll after the five-second optimistic-state
  window so accepted but unapplied commands are detected promptly.
- Add hardware-derived HTTP response normalization and four behavior regressions.
  The cloud brightness and refresh regressions were first verified to fail against
  the previous behavior.

Live validation of 1.5.0 confirmed power, red/white output, dim/brighten, simultaneous
zone edits, neighboring-zone preservation, saved designs and patterns, and zone
state persistence through an integration reload. It also exposed the cloud-zone
brightness issue corrected here. A full restart with the internet disconnected
remains covered by automated tests rather than a live outage drill.

## 1.5.0 — 2026-09-06

Requires Home Assistant **2026.9.1 or later**; the supported baseline is raised
from 2024.12. This release adds persistent offline recovery and corrects controller,
zone, authentication, and entity behavior identified in the 1.4.4 repository review.

- Restore known local controllers, zones, and cached catalogs after restarting
  without internet. Initial discovery and cloud-only features still need the cloud.
- Keep local lights usable during cloud outages and reauthentication. Report failed
  devices as unavailable without inventing off states.
- Serialize complete commands per controller, preserve neighboring zone palettes and
  metadata, retain both concurrent zone edits, and keep other zones on when one is
  switched off during whole-controller playback.
- Route designs according to local capabilities; preserve independent RGBW zone
  brightness and relative brightness when dimming a complete solid design offline.
- Decode three-pixel local layouts correctly and reject zone edits that would
  silently discard unrelated, unrepresentable pixel content.
- Preserve logical color/brightness through cloud dimming, controller echoes,
  power changes, and restarts; respect subsequently observed external changes.
- Bind address overrides to a selected controller and validate host input.
- Correct login error classification, token renewal, and bounded authentication
  retries. Validate vendor response shapes before rendering or editing state.
- Support zone-targeted library actions, discover new controllers on every platform,
  preserve complete catalogs on partial failures, and remove deleted patterns after
  successful empty refreshes.
- Keep off controllers off when speed changes. Track local-enable tasks through
  unload and allow retries after transient activation failures.
- Add pinned Home Assistant behavior tests, meaningful network-boundary regression
  tests, account-flow and lifecycle tests, and CI alongside HACS and hassfest.

Tests simulate external HTTP, authentication, and storage I/O. The two dedicated
edge-case regressions cover offline restart and simultaneous zone edits, and were
also verified to fail when their respective fixes were deliberately removed.
No live-controller smoke test was performed for this release.
