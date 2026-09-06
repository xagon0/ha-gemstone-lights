# Repository review — 1.4.4 baseline

Reviewed on 2026-09-06 at commit `357f38123a645a27cf3988c9db9aee17805cd00f` (integration 1.4.4).

## Scope and evidence

Reviewed every Python module, configuration and translations, service definition, README, manifest, HACS configuration, and CI workflow. No real Gemstone account or controller was used. Fourteen failure scenarios were reproduced against the actual integration classes with mocked network clients and Home Assistant 2026.2.3 on Python 3.13.14. Protocol conclusions additionally use the limitations documented in this repository; hardware behavior still needs verification.

The latest GitHub HACS and hassfest jobs pass. All Python source parses. The repository has no behavior tests. An isolated Ruff check finds two unused imports; these are minor compared with the behavior defects below.

## High-priority fixes

### 1. A cloud outage disables working local controllers

**Reproduced.** `GemstoneCoordinator._async_update_data` calls cloud discovery before attempting any local reads. A cloud discovery error raises `UpdateFailed` without trying the LAN. In addition, entity availability requires the cloud's `online` flag even after a successful local read.

**Impact:** The advertised internet-independent operation fails during an outage. One account-level discovery failure affects every entity.

**Fix:** Cache successful discovery, poll known local controllers independently, refresh cloud discovery less often, and determine availability per device from successful state reads. Distinguish temporary cloud errors from rejected credentials. Offline startup after a Home Assistant restart additionally needs persisted metadata and is a separate scope decision.

Source: `coordinator.py:257`, `coordinator.py:292`, `entity.py:47`, `__init__.py:37`.

### 2. State failures are reported as valid “off” states

**Reproduced.** `_async_state_for` catches `GemstoneError` and returns `{}`. With a previously reported `online: true`, the light remains available and `is_on` becomes false. The same catch also swallows authentication errors.

**Impact:** Dashboards and automations receive false off events; failed devices may look healthy.

**Fix:** Preserve last-known data for inspection, mark the affected device unavailable, and propagate authentication failures through Home Assistant's reauthentication handling.

Source: `coordinator.py:283`, `entity.py:47`, `light.py:138`.

### 3. Saved zone designs use an unsupported local transport

**Reproduced routing defect.** `async_play_design` sends any design locally, including `zonePatterns`. The README explicitly documents that the local controller does not understand that field. The local API accepting a request does not mean the design played, so fallback is not triggered.

**Impact:** Selecting a design or adjusting its brightness can appear to succeed while doing nothing.

**Fix:** Route cloud-only designs to cloud, or translate only the subset whose local representation is verified. Use the same routing rules for saved designs and generated zone designs.

Source: `coordinator.py:452`, `coordinator.py:464`, `README.md` local capability table.

### 4. Editing one zone destroys another zone's pattern details

**Reproduced.** `zone_states` reduces an existing pattern to its first color, brightness, and animation. `async_set_zone` rebuilds all zones from this reduced representation. A neighboring two-color pattern at speed 17 and direction 1 became a one-color pattern at speed 128 and direction 0.

**Impact:** An automation changing the front lights can change the back lights' colors and animation settings.

**Fix:** Preserve full pattern objects for untouched zones, including unknown vendor fields; mutate only the target zone's requested fields.

Source: `coordinator.py:602`, `coordinator.py:648`, `coordinator.py:706`.

### 5. Turning off one zone can turn off the whole controller

**Reproduced.** When a whole-run color or pattern is playing, `zone_states` returns no zones. Removing one zone therefore produces an empty design, and `async_set_zone` switches the controller off.

**Impact:** Zone entities report off while illuminated; turning one off affects every zone. Turning one on can likewise drop the other zones.

**Fix:** Resolve whole-run content into the known zones before applying a zone edit, preserving the other zones' content.

Source: `coordinator.py:602`, `coordinator.py:648`, `light.py:272`.

### 6. Concurrent commands overwrite each other

**Reproduced.** Two zone commands construct replacement designs from the same stale state and send designs containing only their respective zone. The existing lock covers only local transmission, not the read/modify/write operation; cloud writes bypass it altogether. Refresh requests may be debounced.

**Impact:** Parallel automations or quick dashboard changes lose updates. Different controllers also unnecessarily share the local write lock.

**Fix:** Use per-device serialization covering state resolution and both transports, plus a successful-command state snapshot so the next command does not depend on an immediate cloud echo. Coordinate polling with command state to avoid overwriting a newer command with an older read.

Source: `coordinator.py:371`, `coordinator.py:648`.

### 7. Local zone brightness is not independent

**Reproduced.** The generated local design sets global brightness to the maximum requested zone brightness and sends each zone's original unscaled color. Zones requesting brightness 80 and 200 are both sent at 200.

**Impact:** Dimming one zone changes or fails to dim the expected output.

**Fix:** Translate independent brightness into correctly scaled RGBW colors while retaining logical zone state, or use cloud when the local representation cannot preserve the requested design.

Source: `coordinator.py:675`.

### 8. The IP override can control the wrong controller

**Reproduced.** One account-wide host override is used for every discovered controller. With two controllers, both entity sets send their commands to the same IP.

**Impact:** A command addressed to one device can change another physical device.

**Fix:** Associate an override with a specific controller. For backward compatibility, apply an existing single-host override automatically only when the account has exactly one controller; do not guess when it has multiple controllers. Validate host input.

Source: `config_flow.py:36`, `coordinator.py:144`.

## Other confirmed defects

### 9. Three-pixel local zones disappear from state

**Reproduced.** Local writes use explicit pixel indices, but the reader treats any three-element list as a range descriptor. `[10, 11, 12]` becomes the range 11–12, which no longer covers a zone beginning at pixel 10.

**Fix:** Decode explicit local pixel lists according to the documented local format; keep cloud range descriptors separate. Require full coverage when matching a zone, rather than only its endpoints.

Source: `coordinator.py:632`.

### 10. Cloud solid-color brightness cannot be restored reliably

**Reproduced.** Cloud dimming scales the color channels, but the light reports brightness 255 and later reuses the already-scaled color. Raising a dimmed red from 128 to 255 sends red 128 again.

**Fix:** Maintain logical color/brightness for integration-originated commands and reconcile it against returned state. Clearly document the information lost when colors are changed externally through the cloud; do not invent an original color that the API does not provide.

Source: `coordinator.py:408`, `light.py:154`, `light.py:162`, `light.py:191`.

### 11. Network login failures are classified as bad passwords

**Reproduced.** Every exception from the synchronous login is converted into `GemstoneAuthError`, including a network timeout.

**Fix:** Classify Cognito credential rejection separately from connection errors, throttling, and unexpected failures. Transient errors should allow setup retries without requesting a new password.

Source: `api.py:130`.

### 12. Token refresh and repeated authorization rejection need correction

**Confirmed by source and installed dependency inspection.** The client starts refresh 120 seconds before expiry but calls `pycognito.Cognito.check_token()`, which does not renew an unexpired token. The rejected-token retry also clears the access token before calling a method that requires an access token. After a repeated HTTP 401/403, the client raises a generic API error instead of an authentication error.

**Fix:** Explicitly renew tokens when renewal is needed, serialize renewal, retry rejected requests once with a genuinely renewed session, and classify persistent credential rejection appropriately.

Source: `api.py:105`, `api.py:139`, `api.py:183`. Dependency reference: [pycognito token handling](https://pypi.org/project/pycognito/).

### 13. Changing speed while off turns the lights on

**Reproduced.** Power-off preserves the pattern by design. The speed entity sees this stored pattern and replays it regardless of `onState`; replay explicitly requests power on locally.

**Fix:** Store the chosen speed while off and only immediately replay an active pattern. Preserve the rest of the pattern unchanged.

Source: `number.py:53`, `coordinator.py:441`.

### 14. The library service exposes unsupported zone targets

**Reproduced method mismatch.** The entity service and YAML target selector include all integration lights, but `GemstoneZoneLight` has no `async_play_library_pattern` method.

**Fix:** Either implement zone-targeted library playback with preserved neighboring zones, or reject unsupported zone targets with a clear Home Assistant error. Never silently apply a zone-targeted command to the whole controller.

Source: `light.py:94`, `light.py:226`, `light.py:239`, `services.yaml`.

### 15. Empty and partial catalog refreshes leave misleading data

**Empty-catalog case reproduced; partial-refresh case confirmed by source.** An empty successful pattern refresh keeps deleted patterns because the cache is only replaced when the result is nonempty. A failure partway through fetching folders can replace the cache with only the successful prefix. Failed design/zone refreshes are also marked refreshed for the full interval.

**Fix:** Replace caches atomically after complete successful fetches, including successful empty results; retain prior data on failure and use a bounded retry interval.

Source: `coordinator.py:224`, `coordinator.py:504`.

### 16. Controllers added later get only some of their entities

**Confirmed by source.** Light setup listens for newly discovered controllers, but sensor, number, and select setup take only the initial device list.

**Fix:** Use consistent dynamic discovery for all platforms, with duplicate protection and listeners cleaned up on unload.

Source: `light.py:57`, `select.py:25`, `number.py:16`, `sensor.py:16`.

### 17. Malformed API responses bypass normal fallback handling

**Confirmed by source.** Valid JSON with the wrong shape (for example a list or `state: null` in a local response) can cause `AttributeError` outside the client's exception handling. Cloud list and state endpoints similarly assume types without validating them.

**Fix:** Validate endpoint response shapes and required fields at the API boundary; raise transport-specific errors so normal retry/fallback and per-device availability behavior applies.

Source: `local_api.py:45`, `local_api.py:59`, `api.py:157`, cloud read endpoint methods.

## Improvements beyond the bug fixes

- Add regression tests for the failures above, mocked HTTP transport tests, setup/reauth/options-flow tests, and entity discovery/unload tests. Run behavior tests and lint in CI alongside HACS and hassfest.
- Keep the claimed Home Assistant minimum version unless a tested change requires raising it. Test both the minimum supported API and the user's actual version; the review environment alone does not establish the full compatibility range.
- Add a concise contributor guide with reproducible dependency installation and test commands. Pin a compatible test dependency set so a fresh install does not silently select an older Home Assistant based on Python version.
- Reduce cloud discovery and settings polling, bound library retry frequency, and avoid optional catalog loading blocking fresh local state for long periods.
- Preserve complete payloads instead of repeatedly reducing them to simplified representations. Separate payload transformation, transport selection, and state reconciliation to make the coordinator easier to test.
- Resolve duplicate saved pattern/design names by stable IDs and display disambiguation; current name-only selection chooses the first match. Handle collisions with placeholder labels such as `None` explicitly.
- Consider restoring speed and the browsed library folder across restarts. This is a usability change, separate from controller correctness.
- Keep documentation aligned with tested behavior: describe offline-startup limits, zone transport restrictions, cloud brightness limitations, and override scope. Correct the cloud API color docstring, which says `0xRRGGBB` despite the implementation correctly using little-endian RGBW.
- Ensure local-enable background tasks are tied to config-entry lifetime and can retry transient failures; the current one-attempt flag lasts until reload.
- Add redacted diagnostics if troubleshooting real devices is a priority. Exclude passwords, tokens, account identifiers, and unnecessary network details from exported diagnostics.

Home Assistant references: [coordinated polling and error handling](https://developers.home-assistant.io/docs/integration_fetching_data/), [setup failures and reauthentication](https://developers.home-assistant.io/docs/integration_setup_failures/), [dynamic device discovery](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/dynamic-devices/).

## Proposed execution

1. Add failing regression tests for the confirmed defects.
2. Fix device availability, cached discovery, authentication, and response validation.
3. Fix command serialization, zone preservation, design routing, and brightness behavior.
4. Fix entity discovery, library-service targeting, speed behavior, and catalog refreshes.
5. Add CI and contributor documentation; run the tests and review the full diff.
6. Perform a real-controller smoke test when the user's environment is available: power, color, RGBW, dim/brighten, multi-zone designs, simultaneous zone commands, cloud-only mode, and a controlled internet outage.

## Implementation outcome for 1.5.0

Findings 1–17 have corresponding fixes and regression coverage in separate logical
commits. The implementation targets Home Assistant 2026.9.1 and includes persisted
metadata for startup without cloud access, as requested. One release PR preserves
those commits when merged to main.

Exactly one dedicated test covers each designated edge case: A, startup after an
offline restart; B, simultaneous zone edits with a held HTTP response and stale
cloud echoes. Both tests also failed when their respective fixes were temporarily
removed, after which the source was restored.

The remaining optional follow-ups are broader catalog-name disambiguation,
redacted diagnostics, further coordinator decomposition, and reducing optional
catalog/settings work during polling. These are not claimed as completed. Hardware
protocol behavior still needs the live-controller checks in CONTRIBUTING.md;
unsupported pixel layouts now fail explicitly instead of discarding content.

See CHANGELOG.md for release behavior and CONTRIBUTING.md for reproducible tests.
