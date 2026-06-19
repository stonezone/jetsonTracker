# WaveCam Technical-Debt Review

**Date:** 2026-06-15  
**Scope:** backend (`orin/wavecam`), firmware (`firmware/direct-lora`), iOS/watchOS (`ios/WaveCam`)  
**Source tree reviewed:** `origin/main` at `a3b8c0c` (via `../jetsonTracker-review-main-final`) plus current working tree for iOS/firmware  
**Validation:** backend tests 484 passed, firmware `pio run -e tracker/base` green, iOS `xcodegen dump` valid, iOS simulator build **failed**  
**Method:** read-only; subagent deep scans + spot checks; no edits made

---

## Executive Summary

| Component | Lines | Files | Tests | Top Risk |
|---|---|---|---|---|
| Backend Python | 10,613 | 51 | 61 files, 9,590 lines, 484 pass | Calibration owner race + legacy route safety gaps |
| Firmware C++ | 559 | 6 | None | L76K never configured above ~1 Hz despite 2 Hz beacon |
| iOS/watchOS Swift | 9,394 | 27 | None | Simulator build fails; undefined `GlassEffectContainer`; tether polling stalls |

**Current debt level:** Moderate-High. The codebase is functionally solid (tests pass, builds green, deployed and running), but has accumulated safety-coupling issues, stale docs, missing tests, and iOS build blockers that will slow the next development cycle.

**Main velocity drag:** iOS build is not simulator-clean; no automated tests for firmware or iOS; backend control surface has mypy/typing debt and hot-config gaps.

**Main reliability risk:** Calibration/manual owner handoff can race; legacy web routes bypass v1 safety-kill path; iOS tether polling can stall every status request.

---

## Critical

### C1. iOS simulator build fails — watch asset catalog has no applicable icon content
- **Locations:** `ios/WaveCam/Sources-Watch/Assets.xcassets/AppIcon.appiconset/Contents.json`
- **Evidence:** `xcodebuild -scheme WaveCam -sdk iphonesimulator build` fails at `CompileAssetCatalogVariant` because the watch AppIcon set contains only `watchos` platform images; actool targeting iPhone/iPad finds none.
- **Impact:** Blocks simulator runs, SwiftUI previews, and CI. Every PR must be validated on a physical device.
- **Risk:** Critical (build/deployment).
- **Feasibility:** 9/10 — regenerate the watch AppIcon set or exclude watch target from simulator schemes.
- **Confidence:** High; **validated** by build attempt.

### C2. `MergedLiveView` references undefined `GlassEffectContainer`
- **Locations:** `ios/WaveCam/Sources/MergedLiveView.swift:282`
- **Evidence:** `grep -R GlassEffectContainer` finds only the call site.
- **Impact:** Once C1 is fixed, this will be a Swift compile error on iOS 26+ code paths.
- **Risk:** Critical (build).
- **Feasibility:** 9/10 — replace with existing `GlassSurface`/`GlassCard` or define the container.
- **Confidence:** High; **validated**.

---

## High

### H1. Calibration capture bypasses `PtzDispatcher` lock and hard-codes owner string
- **Locations:** `orin/wavecam/wavecam/control_calibration.py:727-733`
- **Evidence:** `self._api._ptz._restore_owner_after_manual = "calibrate"` mutates a private dispatcher field without acquiring its `RLock`; `CALIBRATE` constant is imported but unused.
- **Impact:** Race with concurrent manual claim/release; session owner corruption; possible stuck CALIBRATE state.
- **Risk:** High (safety/ownership).
- **Feasibility:** 7/10 — add `PtzDispatcher.stage_calibrate_restore()` under lock, use `CALIBRATE` constant.
- **Confidence:** High; **validated**.

### H2. Calibration standalone capture does not stop PTZ before sampling
- **Locations:** `orin/wavecam/wavecam/control_calibration.py:718-737`
- **Evidence:** Releases `calibrate` and claims `manual` without calling `ptz.stop()` / zoom stop first.
- **Impact:** Capture samples encoder values while camera may still be coasting from a calibration nudge.
- **Risk:** High (calibration accuracy → tracking error).
- **Feasibility:** 8/10 — stop pan/tilt/zoom before owner release.
- **Confidence:** High; **validated**.

### H3. Legacy web routes `/kill` and `/ptz/stop` bypass v1 safety path
- **Locations:** `orin/wavecam/wavecam/web.py:490-511`
- **Evidence:** `/kill` calls `pipeline.kill(True)` directly; `/api/v1/safety/kill` also cancels calibration, stops media, clears deadmen. Legacy `/ptz/stop` can release `"calibrate"` directly.
- **Impact:** Kill leaves recording/calibration timers running; legacy stop can silently drop a CALIBRATE session.
- **Risk:** High (safety/operational).
- **Feasibility:** 7/10 — route legacy endpoints through the same handlers as `/api/v1`.
- **Confidence:** High; **validated**.

### H4. GPS→vision handoff drops lock when GPS becomes stale
- **Locations:** `orin/wavecam/wavecam/tracking_arbiter.py:117-121`
- **Evidence:** In `auto` mode, if GPS becomes unviable while last owner was `gps_tracker`, it returns `owner="idle"` before evaluating vision lock hysteresis.
- **Impact:** Unnecessary tracking drop/stutter during GPS→vision transition.
- **Risk:** High (tracking quality).
- **Feasibility:** 8/10 — move GPS-loss exit after `_decide_owner` or only return idle when vision not locked.
- **Confidence:** High; **validated**.

### H5. iOS tether is probed on every status poll despite 15-second recheck interval
- **Locations:** `ios/WaveCam/Sources/WaveCamClient.swift:1778-1786`
- **Evidence:** `candidateOrder` still includes `tetherBaseURL` in the returned array even when `now < nextTetherProbeAt`; the 3-second read timeout then stalls every poll if the tether subnet is absent.
- **Impact:** Repeated 3-second blackouts at home/Wi-Fi-only use.
- **Risk:** High (UX/responsiveness).
- **Feasibility:** 9/10 — exclude tether from candidate list until the recheck interval expires.
- **Confidence:** High; **validated** by static analysis.

### H6. Backend v3 GPS keys are not hot-configurable and not exposed in `/config`
- **Locations:** `orin/wavecam/wavecam/control_utils.py:20-58` (HOT_CONFIG_KEYS), `orin/wavecam/wavecam/control_config.py`, `orin/wavecam/wavecam/control_snapshots.py:61-69`
- **Evidence:** `gps.base_drift_enabled` is documented as hot but missing from `HOT_CONFIG_KEYS` and setters. `gps.drive_zoom_near_m`, `drive_zoom_far_m`, `drive_zoom_max_enc`, `drive_zoom_max_frac`, and `fusion.gps_bearing_cue_enabled` are also absent from hot keys and snapshots.
- **Impact:** Operator cannot see or tune new v3 knobs via API/iOS without restart; `base_drift_enabled` cannot be disabled in the field if it misbehaves.
- **Risk:** High (operational).
- **Feasibility:** 8/10 — add keys to HOT_CONFIG_KEYS, setters, and snapshot builders.
- **Confidence:** High; **validated**.

### H7. L76K GPS rate is never configured above default ~1 Hz
- **Locations:** `firmware/direct-lora/src/common/gps_l76k.h:63-65`, `src/tracker/main.cpp:70`
- **Evidence:** `l76k_init()` takes `interval_ms` but `(void)interval_ms;`; never sends CASIC `PCAS02` rate command. Tracker beacons at 2 Hz (`BEACON_INTERVAL_MS=500`) but GPS updates at ~1 Hz.
- **Impact:** Half the packets carry aged/redundant positions; Phase 3 goal of measuring/raising to 5 Hz cannot be met.
- **Risk:** High (tracking accuracy/GPS cadence).
- **Feasibility:** 6/10 — implement `PCAS02` with 5 Hz cap and verify L76K accepts it.
- **Confidence:** High; **validated**.

---

## Medium

### M1. `config.local.yaml` overlay applies raw values without validation
- **Locations:** `orin/wavecam/wavecam/config.py:258-299`
- **Evidence:** `_apply_overlay` does `setattr(target, k, v)` with no type/range coercion.
- **Impact:** Invalid overlay causes startup/runtime crashes after field edits.
- **Feasibility:** 6/10 — reuse typed setters from `control_utils` at load time.
- **Confidence:** High; **validated**.

### M2. Calibration/FOV save failures silently return HTTP 200
- **Locations:** `orin/wavecam/wavecam/control_calibration.py:793-799`, `714`
- **Evidence:** `try: self._store.save(); except Exception as e: print(...)` then returns `calibration_ok()` (200).
- **Impact:** Operator believes calibration persisted; restart loses it.
- **Feasibility:** 9/10 — return 503 refusal or `persisted: false`.
- **Confidence:** High; **validated**.

### M3. Hot-config apply is not atomic with revision check
- **Locations:** `orin/wavecam/wavecam/control_api.py:660-675`, `control_config.py`
- **Evidence:** Revision check and apply are not under a single lock across the full request.
- **Impact:** Two concurrent requests with same revision can interleave applies.
- **Feasibility:** 7/10 — hold adapter lock across validation, dry-run, apply, persist, bump.
- **Confidence:** Medium; inferred from code structure.

### M4. `ptz_owner.py` public `owner` setter bypasses ownership rules
- **Locations:** `orin/wavecam/wavecam/ptz_owner.py:30-33`
- **Evidence:** Setter allows any value; `request()` enforces killed/steal rules.
- **Impact:** Abstraction leak; accidental ownership assignment without safety checks.
- **Feasibility:** 8/10 — remove public setter or rename to `_set_owner`.
- **Confidence:** High; **validated**.

### M5. `/api/v1/health` omits disk component when recorder is missing
- **Locations:** `orin/wavecam/wavecam/control_api.py:743-749`
- **Evidence:** Catches all exceptions silently; no explicit `recorder is None` handling.
- **Impact:** Health endpoint hides missing recorder / disk status.
- **Feasibility:** 9/10 — guard `recorder` and emit `ok: false` or omit with reason.
- **Confidence:** High; **validated** (note: original subagent said "crashes" but the try/except prevents crash; the debt is silent omission).

### M6. Estimator covariance update is approximate and can become non-PSD
- **Locations:** `orin/wavecam/wavecam/estimator.py:430-432`
- **Evidence:** Uses `P[i][j] -= KhP[i][j] * P[j][j]` instead of standard Joseph or `(I-Kh)P` form.
- **Impact:** Shadow logs become misleading; future closed-loop use could trust bad uncertainty.
- **Feasibility:** 6/10 — switch to numerically stable update.
- **Confidence:** Medium; **validated** by code inspection.

### M7. Firmware `arm_receive()` swallows persistent receive failures
- **Locations:** `firmware/direct-lora/src/base/main.cpp:42-50`
- **Evidence:** Retries once, discards return value, goes silent if retry fails.
- **Impact:** Base can go deaf without diagnostic.
- **Feasibility:** 8/10 — capture/log retry result and surface repeated failures.
- **Confidence:** High; **validated**.

### M8. Firmware RadioLib version not pinned
- **Locations:** `firmware/direct-lora/platformio.ini:20`
- **Evidence:** `^7.1.2` resolved to 7.7.1 locally; spec says verified against 7.1.2.
- **Impact:** Non-reproducible builds; possible API/behavior differences.
- **Feasibility:** 9/10 — pin exact version and re-test.
- **Confidence:** High; **validated**.

### M9. Tracker `Serial.printf` may block when no USB host attached
- **Locations:** `firmware/direct-lora/src/tracker/main.cpp:81, 94-96, 147-151, 189-192`
- **Evidence:** Verbose logs unconditional; battery-powered tracker usually has no USB host.
- **Impact:** Loop stalls, dropped GPS bytes, delayed TX.
- **Feasibility:** 8/10 — guard with `if (Serial)` or rate-limit.
- **Confidence:** Medium; board-specific behavior.

### M10. iOS `TuneView` nested `Button` breaks preset delete affordance
- **Locations:** `ios/WaveCam/Sources/TuneView.swift:287-329`
- **Evidence:** Outer `Button` label contains inner `Button`; gesture conflict likely applies preset instead of deleting.
- **Impact:** Operator cannot reliably delete custom presets.
- **Feasibility:** 9/10 — use context menu, swipe action, or external delete button.
- **Confidence:** High; **validated**.

### M11. iOS `CalibrateView` resets UI even when `session/exit` fails
- **Locations:** `ios/WaveCam/Sources/CalibrateView.swift:353-366`
- **Evidence:** Sets `wizardStep = .idle` regardless of network result.
- **Impact:** UI shows idle while backend is still in CALIBRATE.
- **Feasibility:** 9/10 — only transition on success.
- **Confidence:** High; **validated**.

### M12. iOS `CalibrateView` level uses small-angle approximation
- **Locations:** `ios/WaveCam/Sources/CalibrateView.swift:378-379`
- **Evidence:** `levelRoll = m.gravity.x * 90.0`; inaccurate away from 0°.
- **Impact:** Misleading level reading.
- **Feasibility:** 9/10 — use `asin(clamped_component) * 180/π`.
- **Confidence:** High; **validated**.

### M13. Watch client probes tether on every poll with no recheck interval
- **Locations:** `ios/WaveCam/Sources-Watch/WatchClient.swift:136-169, 209-219`
- **Evidence:** Always returns `[preferred, tetherBase, wifiBase]`; no `nextTetherProbeAt` logic.
- **Impact:** Hangs on absent tether every 2-second poll.
- **Feasibility:** 8/10 — add route caching + recheck interval.
- **Confidence:** High; **validated**.

### M14. Watch recorder writes JSONL synchronously on main actor
- **Locations:** `ios/WaveCam/Sources-Watch/WatchSessionRecorder.swift:155-160`
- **Evidence:** `FileHandle.write` called from `@MainActor` on 4 Hz motion timer.
- **Impact:** UI stalls, battery/timer interference.
- **Feasibility:** 7/10 — move to dedicated serial queue.
- **Confidence:** High; **validated**.

### M15. No automated tests for firmware or iOS
- **Locations:** entire `firmware/direct-lora/src/`, `ios/WaveCam/`
- **Evidence:** No test targets found.
- **Impact:** Regressions caught only manually or in field.
- **Feasibility:** 5/10 — add host-compiled firmware tests and XCTest target.
- **Confidence:** High; **validated**.

### M16. No firmware CI in GitHub Actions
- **Locations:** `.github/workflows/backend-tests.yml` only
- **Evidence:** No `pio run` CI job.
- **Impact:** Firmware build regressions go unnoticed.
- **Feasibility:** 8/10 — add workflow running `fetch_variant.sh` + `pio run -e tracker/base`.
- **Confidence:** High; **validated**.

---

## Low / Tech Debt

### L1. `testbed` owner still referenced despite docs saying retired
- **Locations:** `orin/wavecam/wavecam/ptz_owner.py:15`, `pipeline.py:547, 574, 721, 740, 779, 843`, `web.py:389`, `control_api.py:359`
- **Impact:** Docs/code inconsistency; potential confusion.
- **Feasibility:** 7/10 — remove or clearly mark legacy.

### L2. `wavecam_build_plan.md` archived but references may remain
- **Locations:** `archive/docs-superseded-20260615/` (archived), but other docs may still link.
- **Impact:** Stale canonical references.
- **Feasibility:** 9/10 — grep and update links.

### L3. `gps_meshtastic.py` duplicates helpers from `gps_geo.py`
- **Locations:** `orin/wavecam/wavecam/control_snapshots.py:309`
- **Impact:** Import from wrong module; duplication.
- **Feasibility:** 9/10 — import from `gps_geo`.

### L4. `run.py` logged GPS source defaults to stale `"meshtastic"`
- **Locations:** `orin/wavecam/wavecam/run.py:131`
- **Impact:** Misleading field diagnostics.
- **Feasibility:** 9/10 — reuse computed `source` variable.

### L5. `control_snapshots.py` estimator default mismatches `Config`
- **Locations:** `orin/wavecam/wavecam/control_snapshots.py:97-98`
- **Impact:** Snapshot misrepresents estimator state when section absent.
- **Feasibility:** 9/10 — change default to `False`.

### L6. Backend mypy/type debt
- **Locations:** 220 `no-untyped-def` errors across `control_api.py`, `web.py`.
- **Impact:** Blocks strict static analysis of safety-critical control surface.
- **Feasibility:** 5/10 — gradual annotation.

### L7. Firmware stale comments about PMTK/57600 and rate config
- **Locations:** `firmware/direct-lora/src/tracker/main.cpp:64-66`
- **Impact:** Dangerous comment drift.
- **Feasibility:** 9/10 — rewrite comment to match PCAS/9600/no-rate-config behavior.

### L8. Firmware `l76k_init()` dead `interval_ms` parameter
- **Locations:** `firmware/direct-lora/src/common/gps_l76k.h:60-65`
- **Impact:** Misleading API.
- **Feasibility:** 9/10 — implement or remove parameter.

### L9. Firmware `_Static_assert` in C++ code
- **Locations:** `firmware/direct-lora/src/common/packet.h:40`
- **Impact:** Non-portable; works as GCC extension.
- **Feasibility:** 10/10 — replace with `static_assert`.

### L10. iOS hard-coded device UDID/team/build paths
- **Locations:** `ios/WaveCam/build-device.sh:9,16,22`, `project.yml:12`
- **Impact:** Checked-in personal config.
- **Feasibility:** 7/10 — move to env vars / `project.user.yml`.

### L11. `build-device.sh` hides diagnostic output
- **Locations:** `ios/WaveCam/build-device.sh:17`
- **Impact:** Hard to diagnose CI/device failures.
- **Feasibility:** 9/10 — tee to log or remove grep pipe.

### L12. iOS hard-coded Orin URLs duplicated
- **Locations:** `ios/WaveCam/Sources/WaveCamDefaults.swift:5-6`, `Sources-Watch/WatchConnectionStore.swift:13-14`
- **Impact:** Drift risk.
- **Feasibility:** 9/10 — centralize in shared config/plist.

### L13. `SessionLogView` may duplicate last event
- **Locations:** `ios/WaveCam/Sources/SessionLogView.swift:53-55`
- **Impact:** Duplicate log entries.
- **Feasibility:** 9/10 — filter by `t > sinceCursor`.

### L14. `ConnectionView` health timer runs in background
- **Locations:** `ios/WaveCam/Sources/ConnectionView.swift:60-67`
- **Impact:** Background polling.
- **Feasibility:** 8/10 — observe `scenePhase`.

---

## Metrics Dashboard

```yaml
backend:
  python_files: 51
  lines_of_code: 10613
  test_files: 61
  test_lines: 9590
  tests_passed: 484
  compileall: clean
  mypy_no_untyped_def_errors: 220
  largest_files:
    - control_api.py: 1052
    - pipeline.py: 848
    - control_calibration.py: 799
    - web.py: 562

firmware:
  cpp_h_files: 6
  lines_of_code: 559
  tests: 0
  ci: none
  builds: tracker/base green

ios_watch:
  swift_files: 27
  lines_of_code: 9394
  tests: 0
  simulator_build: failed (watch AppIcon)
  largest_files:
    - WaveCamClient.swift: 1970
    - CalibrateView.swift: 1220
    - MergedLiveView.swift: 776
    - TuneView.swift: 717

debt_gaps:
  hot_config_keys_missing_v3_gps: validated
  v3_gps_keys_missing_from_config_snapshot: validated
  legacy_web_routes_bypass_v1_safety: validated
  calibration_owner_race: validated
  firmware_gps_rate_unconfigured: validated
  ios_simulator_build_broken: validated
  ios_undefined_GlassEffectContainer: validated
```

---

## Prioritized Remediation Roadmap

### Immediate (this week)

1. **Fix iOS simulator build** (C1) and `GlassEffectContainer` (C2) — unblocks CI/previews.
2. **Fix calibration owner race** (H1) and stop PTZ before capture (H2) — safety/accuracy.
3. **Align legacy web kill/stop with v1 safety path** (H3) — safety parity.
4. **Wire v3 GPS keys into hot config + snapshots** (H6) — operational control.
5. **Decide/fix firmware GPS rate strategy** (H7) — implement `PCAS02` or align beacon to 1 Hz and update docs.

### Short-term (next 2–4 weeks)

6. Fix GPS→vision handoff drop (H4).
7. Add iOS tether recheck interval enforcement (H5) and watch route caching (M13).
8. Add firmware CI (M16) and host-compiled firmware tests.
9. Add iOS XCTest target (M15) starting with JSON decode and failover tests.
10. Validate `config.local.yaml` overlay values (M1).
11. Surface calibration save failures (M2).
12. Pin RadioLib version (M8).

### Medium-term (next 1–3 months)

13. Refactor oversized iOS files (`WaveCamClient`, `CalibrateView`, `MergedLiveView`, `TuneView`).
14. Remove/retire `testbed` owner references (L1).
15. Stabilize estimator covariance update (M6).
16. Gradually annotate backend with types to clear mypy errors.
17. Consolidate legacy web routes with `/api/v1` handlers.

---

## Prevention Plan

```yaml
ci_pipeline:
  - backend-tests.yml (existing; keep)
  - firmware-pio-build.yml: pio run -e tracker/base
  - ios-build.yml: xcodegen generate + xcodebuild iphonesimulator
  - dependency_audit: pin RadioLib, Python deps

pre_merge_checks:
  - compileall -q orin/wavecam/wavecam
  - pytest orin/wavecam/tests -q
  - pio run -e tracker -e base
  - xcodegen dump && xcodebuild -sdk iphonesimulator build

code_review:
  - any PTZ/owner/calibration change requires a test for the race/failure path
  - any new config key requires hot-key + snapshot + docs update
  - docs update when runtime contract changes

field_gates:
  - simulator build must pass before claiming iOS changes ready
  - firmware build must pass in CI before merge
```

---

## Unknowns / Unvalidated

- Whether the L76K actually accepts and produces clean 5 Hz fixes with `PCAS02` (field verification needed).
- Whether `GlassEffectContainer` was intended to be a local helper that was deleted or renamed.
- Exact impact of approximate estimator covariance on shadow logs in real surf scenarios.
- Whether legacy web UI is still actively used in the field vs. the native iOS app.

---

*No files were edited during this review. Report saved to `docs/reviews/2026-06-15-technical-debt-review.md`.*
