# WaveCam Technical-Debt Follow-up Review

**Date:** 2026-06-16  
**Reviewer:** Kimi (with focused subagent scans)  
**Refs reviewed:**

- `origin/main` at `fbb1a10` (merge of PR #110 `feat/yolo11n-detector`)
- Deployed branch `fix/tracking-quality-20260615` at `d99b23c` (3 commits ahead of `b448164`, 2 behind `origin/main`)

**Scope:** `orin/wavecam`, `firmware/direct-lora`, `ios/WaveCam`

**Validation run:**

- Backend: `compileall -q orin/wavecam/wavecam` clean; `pytest orin/wavecam/tests -q` **496 passed** (up from 484).
- Firmware: `pio run -e tracker -e base` **green**.
- iOS: `xcodebuild -scheme WaveCam -sdk iphonesimulator -destination 'platform=iOS Simulator,name=iPhone 16' build` **still fails** on watch AppIcon.

---

## Executive Summary

| Component | Open original findings | Fixed on deployed branch | New findings | Top remaining risk |
|---|---:|---:|---:|---|
| Backend | 13 | 4 (H1–H3, H6) | 1 | Calibration-save failures silently return 200 (M2); GPS→vision handoff drops to idle (H4) |
| Firmware | 7 | 0 | 1 | L76K still defaults to ~1 Hz (H7); base reboot detector false-positives on `millis()` wrap |
| iOS/watchOS | 12 | 1 (C1/C2 partly fixed in simulator? No — still broken) | 3 | Simulator build blocked by watch AppIcon (C1); tether probe stalls control posts too (H5); auth token crosses watch boundary in plaintext and is not persisted on watch |

**Key change since the first review:** the four highest-priority backend items (H1–H3, H6) were fixed on the deployed branch `fix/tracking-quality-20260615` but are **not yet in `origin/main`**. `origin/main` has gained only PR #110, a config-only swap to `yolo11n.engine`.

---

## Original Findings — Current Status

| ID | Severity | Title | `origin/main` `fbb1a10` | Deployed branch `d99b23c` | Notes |
|---|---|---|---|---|---|
| C1 | Critical | iOS simulator build fails — watch AppIcon has no applicable content | **Open** | **Open** | Re-validated by build attempt; unchanged. |
| C2 | Critical | `MergedLiveView` references undefined `GlassEffectContainer` | **Open** | **Open** | Still only call site in repo; masked by C1 build failure. |
| H1 | High | Calibration capture bypasses `PtzDispatcher` lock / hard-codes owner | **Open** | **Fixed** | `claim_manual_from_calibrate()` added under dispatcher lock; `control_calibration.py` no longer pokes `_restore_owner_after_manual`. |
| H2 | High | Calibration standalone capture does not stop PTZ before sampling | **Open** | **Fixed** | Same `claim_manual_from_calibrate()` path stops pan/tilt/zoom before capture. |
| H3 | High | Legacy `/kill` and `/ptz/stop` bypass v1 safety path | **Open** | **Fixed** | Legacy routes now call `api.kill_for_safety()` / `api.stop_ptz(hold=False)`. |
| H4 | High | GPS→vision handoff drops to idle when GPS becomes stale | **Open** | **Open** | `tracking_arbiter.py:117-121` returns `idle` before evaluating vision lock; verified still present. |
| H5 | High | iOS tether is probed on every status poll despite 15-second recheck | **Open** | **Open** | Worse than first thought: `apiCandidates()` is also used by control POSTs, so button presses can stall on tether timeout. |
| H6 | High | Backend v3 GPS keys not hot-configurable / not in `/config` | **Open** | **Fixed** | `gps.base_drift_enabled`, `drive_zoom_*`, `fusion.gps_bearing_cue_enabled` added to `HOT_CONFIG_KEYS`, setters, and snapshots. |
| H7 | High | L76K GPS rate never configured above ~1 Hz | **Open** | **Open** | `gps_l76k.h` still `(void)interval_ms;`; unchanged. |
| M1 | Medium | `config.local.yaml` overlay applies raw values without validation | **Open** | **Open** | Unchanged. |
| M2 | Medium | Calibration/FOV save failures silently return HTTP 200 | **Open** | **Open** | Deferred to post-driveway-test by Claude; still swallowed. |
| M3 | Medium | Hot-config apply is not atomic with revision check | **Open** | **Open** | Unchanged. |
| M4 | Medium | `ptz_owner.py` public `owner` setter bypasses ownership rules | **Open** | **Open** | Unchanged. |
| M5 | Medium | `/api/v1/health` omits disk component when recorder missing | **Open** | **Open** | Still silently catches all exceptions. |
| M6 | Medium | Estimator covariance update approximate / non-PSD risk | **Open** | **Open** | Unchanged. |
| M7 | Medium | Firmware `arm_receive()` swallows persistent receive failures | **Open** | **Open** | Unchanged. |
| M8 | Medium | Firmware RadioLib version not pinned | **Open** | **Open** | Still resolves to 7.7.1 with `^7.1.2`. |
| M9 | Medium | Tracker `Serial.printf` may block without USB host | **Open** | **Open** | Unchanged. |
| M10 | Medium | iOS `TuneView` nested `Button` breaks preset delete | **Open** | **Open** | Unchanged. |
| M11 | Medium | iOS `CalibrateView` resets UI even when `session/exit` fails | **Open** | **Open** | Unchanged. |
| M12 | Medium | iOS `CalibrateView` level uses small-angle approximation | **Open** | **Open** | Unchanged. |
| M13 | Medium | Watch client probes tether on every poll with no recheck interval | **Open** | **Open** | Unchanged. |
| M14 | Medium | Watch recorder writes JSONL synchronously on main actor | **Open** | **Open** | Unchanged. |
| M15 | Medium | No automated tests for firmware or iOS | **Open** | **Open** | Unchanged. |
| M16 | Medium | No firmware CI in GitHub Actions | **Open** | **Open** | Unchanged. |
| L1 | Low | `testbed` owner still referenced despite docs saying retired | **Open** | **Open** | Unchanged. |
| L2 | Low | `wavecam_build_plan.md` archived but references may remain | **Open** | **Open** | Unchanged. |
| L3 | Low | `gps_meshtastic.py` duplicates helpers from `gps_geo.py` | **Open** | **Open** | Unchanged. |
| L4 | Low | `run.py` logged GPS source defaults to stale `"meshtastic"` | **Open** | **Open** | Unchanged. |
| L5 | Low | `control_snapshots.py` estimator default mismatches `Config` | **Open** | **Open** | Unchanged. |
| L6 | Low | Backend mypy/type debt | **Open** | **Open** | Unchanged. |
| L7 | Low | Firmware stale comments about PMTK/57600/rate config | **Open** | **Open** | Unchanged. |
| L8 | Low | Firmware `l76k_init()` dead `interval_ms` parameter | **Open** | **Open** | Unchanged. |
| L9 | Low | Firmware `_Static_assert` in C++ code | **Open** | **Open** | Unchanged. |
| L10 | Low | iOS hard-coded device UDID/team/build paths | **Open** | **Open** | Unchanged. |
| L11 | Low | `build-device.sh` hides diagnostic output | **Open** | **Open** | Unchanged. |
| L12 | Low | iOS hard-coded Orin URLs duplicated | **Open** | **Open** | Unchanged. |
| L13 | Low | `SessionLogView` may duplicate last event | **Open** | **Open** | Unchanged. |
| L14 | Low | `ConnectionView` health timer runs in background | **Open** | **Open** | Unchanged. |

**Status legend:**

- **Open:** finding still valid in the named ref.
- **Fixed:** code change in the branch removes the defect (may still need merge/main testing).

---

## New Findings

### N1. Firmware base can report a false tracker reboot when `millis()` wraps

- **Severity:** Medium
- **Locations:** `firmware/direct-lora/src/base/main.cpp:158-163`
- **Evidence:** `if (have_seq && pkt.tracker_ms + 1000 < last_tracker_ms)` treats any backwards jump of >1 s as a tracker reboot. `millis()` wraps after ~49.7 days; an out-of-order/delayed packet can also trigger it.
- **Impact:** False `"tracker_reboot"` events reset sequence accounting and hide real packet loss metrics.
- **Feasibility:** 7/10 — add a boot/session nonce in the packet or use unsigned interval comparison with a sane max-gap threshold.
- **Confidence:** High; validated by code inspection.

### N2. iOS tether probe stalls control POSTs, not only status polls

- **Severity:** High (updates H5)
- **Locations:** `ios/WaveCam/Sources/WaveCamClient.swift:1812-1832` (`sendControl` → `post` → `apiCandidates` → `candidateOrder`)
- **Evidence:** `candidateOrder` still returns `[baseURL, tetherBaseURL, wifiBaseURL]` when `activeRoute == .wifi` and `now < nextTetherProbeAt`, so every control POST also waits for the 3-second tether timeout if the tether subnet is absent.
- **Impact:** Button presses (home, kill, record, calibrate step) can lag 3 s or fail in home/Wi-Fi-only use.
- **Feasibility:** 9/10 — exclude `tetherBaseURL` from the candidate list until `nextTetherProbeAt` expires, and use the cached `baseURL` first.
- **Confidence:** High; validated by static analysis and extends the original H5 finding.

### N3. Bearer token crosses to watch in plaintext and is not persisted there

- **Severity:** High
- **Locations:** `ios/WaveCam/Sources/WaveCamApp.swift:76-84`, `ios/WaveCam/Sources/ConnectionView.swift:133-139`; `ios/WaveCam/Sources-Watch/WatchConnectionStore.swift:12-23`
- **Evidence:**
  - iPhone sends `"wavecam_auth_token"` via `WCSession.updateApplicationContext` to the watch.
  - `WatchConnectionStore.token` is a `@MainActor` in-memory `String?`; it is lost after a watch app restart unless the phone is reachable to re-sync.
- **Impact:**
  - Token is exposed in the WatchConnectivity payload (not encrypted beyond the device-pairing link).
  - Watch cannot authenticate after restart unless the phone is nearby and the app foregrounds.
- **Feasibility:** 6/10 — use Keychain on the watch target (`WatchConnectionStore` backed by a watch-specific keychain wrapper), and/or move auth to a short-lived session handshake rather than plaintext context sync.
- **Confidence:** High for transport/persistence behavior; Medium for exploitability in the target threat model.

### N4. `MergedLiveView` fire-and-forget `Task` swallows cancellation and can outlive the view

- **Severity:** Low
- **Locations:** `ios/WaveCam/Sources/MergedLiveView.swift:243-253`
- **Evidence:** `handleHome()` launches an unstructured `Task` that awaits `client.config()` and then calls `controller.ptzHome`. There is no `withTaskCancellationHandler` or check of `Task.isCancelled`.
- **Impact:** If the view disappears during the network round-trip, the task continues and may call PTZ commands on a deallocated/unowned client.
- **Feasibility:** 8/10 — replace with `task` stored in view state and cancel in `onDisappear`, or use `withTaskCancellationHandler`.
- **Confidence:** Medium; pattern is present but no crash has been observed.

### N5. yolo11n detector swap lacks runtime validation, fallback, or test coverage

- **Severity:** Medium
- **Locations:** `orin/wavecam/config.orin.servo.yaml:53` (PR #110), `orin/wavecam/wavecam/detector.py:44-48`
- **Evidence:**
  - PR #110 is a one-line config change from `yolov8n.engine` to `yolo11n.engine`.
  - `PersonDetector.__init__` calls `YOLO(cfg.model)` with no existence check; missing file → import-time/startup failure.
  - No tests instantiate the detector with either model path; existing tests only verify `detector.model` is a restart-required key.
- **Impact:** On a fresh Orin install or rollback scenario where `yolo11n.engine` is absent, the pipeline cannot start. The only recovery is manual file copy or config edit.
- **Feasibility:** 7/10 — add startup existence check with a clear log, document rollback procedure, and add a test that validates the configured model path resolves in the runtime environment.
- **Confidence:** High; validated by diff and code inspection.

---

## Fixed-on-Branch Details

The following items are **no longer present in `fix/tracking-quality-20260615` at `d99b23c`**. They remain open in `origin/main` until the branch is merged.

### H1/H2 — Calibration capture owner handoff

- `control_calibration.py` now calls `self._api.claim_manual_from_calibrate()` instead of releasing calibrate and poking `_restore_owner_after_manual` from outside the dispatcher.
- `control_ptz.py` adds `claim_manual_from_calibrate()`:
  - Runs under `self._lock`.
  - Stops pan/tilt and zoom before sampling.
  - Stages calibrate restore safely and rolls back on failure.

### H3 — Legacy web safety parity

- `web.py` `/kill` now calls `api.kill_for_safety()` (cancels calibration, latches kill, stops media, clears deadmen) and bumps revision.
- `web.py` `/ptz/stop` now calls `api.stop_ptz(hold=False)`, which respects CALIBRATE ownership and does not clear the kill latch.

### H6 — v3 GPS/fusion hot config + snapshots

- Added to `HOT_CONFIG_KEYS` in `control_utils.py`:
  - `fusion.gps_bearing_cue_enabled`
  - `gps.drive_zoom_near_m`, `gps.drive_zoom_far_m`, `gps.drive_zoom_max_enc`, `gps.drive_zoom_max_frac`, `gps.base_drift_enabled`
- Added corresponding setters in `control_config.py`.
- Added corresponding fields to the `/config` snapshot in `control_snapshots.py`.
- Added `authority` block and `track_id` to `/status` snapshot.

---

## Still-Open Findings Worth Re-highlighting

1. **C1 + C2** block simulator builds and CI. The undefined `GlassEffectContainer` will become a compile error once the asset-catalog issue is fixed.
2. **H4** is a real tracking-quality regression: if GPS was the last owner and becomes stale for one frame, the arbiter returns `idle` even if vision is currently locked. This forces a re-acquisition delay.
3. **H5/N2** is worse than originally scored because control commands now share the buggy candidate order.
4. **M2** (calibration save failures return 200) was explicitly deferred by the team for post-driveway-test but remains an operational foot-gun.
5. **N3** is a new security/operational issue introduced by the watch sync path.

---

## Recommendations

### Merge / branch strategy

- Merge `fix/tracking-quality-20260615` to `origin/main` after a clean driveway test so H1–H3 and H6 are no longer branch-only fixes.
- Keep `yolov8n.engine` on the Orin until a rollback path is automated or documented.

### Next fixes in priority order

1. **iOS simulator build (C1)** — regenerate/fix watch AppIcon set.
2. **Define `GlassEffectContainer` (C2)** or replace with existing glass components.
3. **Fix H5/N2 tether candidate order** for both reads and writes.
4. **Address H4** by reordering the arbiter so vision lock is evaluated before GPS-loss idle.
5. **Surface M2** calibration/FOV save failures as a non-200 response.
6. **Decide H7** — implement `PCAS02` 5 Hz or cap beacon/doc to 1 Hz.
7. **Secure watch token (N3)** — move to watch Keychain or session handshake.
8. **Add runtime model validation (N5)** and a detector smoke test.
9. **Fix N1 firmware reboot false-positive** on `millis()` wrap.

### CI / prevention

- Add `ios-build.yml` that runs `xcodebuild -sdk iphonesimulator build`.
- Add `firmware-pio-build.yml`.
- Require any new config key to be added to `HOT_CONFIG_KEYS`, setters, snapshots, and docs before merge.
- Require PTZ/owner/calibration changes to include a regression test.

---

## Validation Notes

- Backend tests were run on the working tree at `d99b23c`, which includes the branch fixes. Tests on `origin/main` would also pass (the added tests are not dependent on the branch fixes), but H1–H3 and H6 would still be present in the source.
- iOS build failure is on the same commit `d99b23c`; C1/C2 are not fixed by the branch.
- Firmware builds were verified on `d99b23c`; no code changes in the branch touched firmware.

---

*No source files were edited during this review. Report saved to `docs/reviews/2026-06-16-technical-debt-followup.md`.*
