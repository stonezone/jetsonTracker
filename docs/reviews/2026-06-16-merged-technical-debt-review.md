# WaveCam Merged Technical-Debt Review

**Date:** 2026-06-16  
**Reviewers:** Kimi (with focused validation) + DeepSeek v2 review (`docs/code-review-20260615-v2.md`)  
**Sources merged:**

- Kimi follow-up: `docs/reviews/2026-06-16-technical-debt-followup.md`
  - `origin/main` at `fbb1a10`
  - `fix/tracking-quality-20260615` at `d99b23c` (deployed)
- DeepSeek v2: `docs/code-review-20260615-v2.md`
  - `fix/tracking-quality-20260615` at `d99b23c`

**Validation run for this merge:**

- `python3 -m compileall -q orin/wavecam/wavecam` — clean
- `PYTHONPATH=orin/wavecam python3 -m pytest orin/wavecam/tests -q` — **496 passed**
- `cd firmware/direct-lora && pio run -e tracker -e base` — **green**
- `xcodebuild -scheme WaveCam -sdk iphonesimulator -destination 'platform=iOS Simulator,name=iPhone 16' build` — **fails** on watch AppIcon
- Spot-checked every DeepSeek open finding at the referenced line; also checked DeepSeek "fixed" and "invalid" claims where they differ from Kimi findings.

---

## Executive Summary

| Category | Closed / fixed | Confirmed invalid | Still open | New from merge |
|---|---:|---:|---:|---|
| Backend | 5 | 0 | 11 | 4 (C4, H7, H12, H13) |
| Firmware | 0 | 0 | 9 | 3 (H17, L36, L37) |
| iOS/watchOS | 0 | 7 | 18 | 4 (C5/HTTP, L33, L34, L35) |
| Cross-cutting | — | — | — | — |
| **Total** | **5** | **7** | **38** | **11** |

> The DeepSeek review was scoped to the deployed branch only and used a different 37-item baseline. This merge reconciles those findings with the Kimi follow-up, which compared both `origin/main` and the branch. All disagreements were spot-checked; none were materially contradictory.

**Top risks after merge:**

1. **iOS build is still broken** (C1/C2) — blocks simulator, previews, and CI.
2. **Auth token leaves the device unencrypted** — over HTTP to Orin (D-C5) and via WatchConnectivity to the watch, where it is not persisted (K-N3 / D-H14).
3. **Tether-probe logic stalls both reads and writes** (K-H5 / K-N2) — every status poll and control POST can wait 3 s on an absent tether subnet.
4. **Backend estimator covariance math is approximate** (D-C1 / K-M6 / D-H9) — DeepSeek escalates to Critical because COMMAND mode may rely on it; today it is shadow-only.
5. **GPS tracking can stutter** — H4 drops to idle when GPS becomes stale even if vision is locked; H13 sends STOP on any transient GPS-pointing failure.

---

## 1. Findings Closed / Fixed Since the First Kimi Review

| ID(s) | Severity | Title | Fixed by / where | Validation |
|---|---|---|---|---|
| K-H1 | High | Calibration capture bypasses `PtzDispatcher` lock | `97ad54f` (`control_ptz.py:59-81`, `control_calibration.py`) | Verified: `claim_manual_from_calibrate()` runs under lock. |
| K-H2 | High | Calibration capture does not stop PTZ before sampling | `97ad54f` (same path) | Verified: stops pan/tilt/zoom before capture. |
| K-H3 | High | Legacy `/kill` and `/ptz/stop` bypass v1 safety path | `97ad54f` (`web.py:492-511`) | Verified: legacy routes now use `api.kill_for_safety()` / `api.stop_ptz()`. |
| K-H6 | High | v3 GPS/fusion keys not hot-configurable or in `/config` | `d99b23c` (`control_utils.py`, `control_config.py`, `control_snapshots.py`) | Verified: keys added to hot-config setters and snapshots. |
| D-C2 | Critical | `compute_roi_crop` one-sided edge clamp | `f2c935a` (`pipeline.py:50-57`) | Verified: two-sided min-size enforcement. |
| D-C3 | Critical | `kill()` races with run-loop zoom | `pipeline.py:204-213` + `pipeline.py:642-647` | Verified: immediate zoom stop + per-frame zoom stop while killed. |
| D-H8 | High | `DirectRadioGps` clears cached fix on bad line | `f2c935a` (`gps_direct_lora.py:197-205`) | Verified: corrupt lines retain last-known-good fix. |
| D-H10 | High | Estimator permanently disabled on exception | `pipeline.py:597` re-init every 120 frames | Verified: `_maybe_init_estimator()` retried, not one-shot. |
| D-H15 | High | Bump flag never resets | `PhoneSensorPublisher.swift:185-186` | Verified: `bumpPending = false` each publish. |
| D-H16 | High | Kill-in-flight deadlock | `WaveCamClient.swift:1017-1035` | Verified: latch clears on backend confirmation **and** on POST failure. |
| D-M25 | Medium | Phone vs watch failover divergence | `WaveCamClient.swift` / `WatchClient.swift` | Verified: read failover includes `.timedOut`; write failover is restricted to pre-connection errors. |
| D-H19 | Low | Stale PMTK comment in firmware | `gps_l76k.h` comments updated | **Partial**: `gps_l76k.h` is correct, but `tracker/main.cpp:64-66` and `radio_config.h:25` still mention PMTK/57600/5 Hz floor. |

**Note:** D-C6 (bump detection 50 ms lag) was marked fixed by DeepSeek because `Date()` is sub-millisecond; it is treated as *confirmed low-impact / not a bug* rather than a code change.

---

## 2. Findings Confirmed Invalid / Not Bugs

These were flagged in the DeepSeek 37-item baseline and re-checked. They are **not retained** in the open list.

| ID | Original claim | Why invalid |
|---|---|---|
| D-#11 | Lead compensator bias | `self._last` stores raw error before `_lead()` — correct feed-forward. Retracted by original reviewer. |
| D-M20 | CALIBRATE not in `AUTONOMOUS` | Intentional design: CALIBRATE is a tracked owner but operator-driven, not autonomous. |
| D-M22 | `build_gps` double-fetches reader health | Values are cached in locals (`control_snapshots.py:263-272`); no double-fetch. |
| D-M24 | `refreshAfterLegacyResponse` fire-and-forget | Intentional: caller doesn't need result; weak self avoids retain cycle. |
| D-M26 | Live-mode commands silent no-op in mock | Intentional mock-mode behavior for UI demos. |
| D-M28 | Firmware dead airtime guard | Two independent guards (beacon interval + airtime) serve different purposes — correct. |
| D-M29 | `GPS_BAUDRATE` undefined | Defined by board variant/platform headers; builds and runs on hardware. |
| D-C6 | Bump detection 50 ms lag | `Date()` precision makes this negligible. |
| D-H16 | Kill-in-flight deadlock | Correct design, not a deadlock (see fixed table). |

---

## 3. Open Findings — Merged

### 3.1 Backend

| ID | Severity | Source(s) | Location | `origin/main` | Branch `d99b23c` | Notes |
|---|---|---|---|---|---|---|
| C4 | Critical | D | `pipeline.py:597` | Open | Open | Estimator re-init is gated on `_frame_i % 120`; if FOV curve arrives between checks, init is delayed up to ~4 s at 30 fps. |
| C1 / M6 / H9 | Critical / Medium / High | D + K | `estimator.py:414-432` | Open | Open | `_scalar_update` uses only the diagonal term `(K h) P`; can become non-PSD under large innovations. Shadow-only today; DeepSeek flags as blocker for future COMMAND mode. |
| H7 | High | D | `control_ptz.py:48-57` | Open | Open | `claim_manual(takeover=True)` releases current owner then requests manual; another caller could steal owner in the window. Restore-on-failure mitigates but does not prevent TOCTOU. |
| H4 | High | K | `tracking_arbiter.py:117-121` | Open | Open | GPS→vision handoff returns `idle` if GPS becomes stale before evaluating vision lock, causing re-acquisition stutter. |
| H13 | High | D | `pipeline.py:735-739` | Open | Open | `_gps_pointing_cmd` returning `None` (stale fix, invalid calibration, no base) causes immediate STOP. Fail-closed design produces visible aim stutter. |
| M1 / M21 | Medium | K + D | `config.py:258-299` | Open | Open | `config.local.yaml` overlay applies raw values with no type/range coercion; unknown keys inside known sections are silently ignored. |
| M2 | Medium | K | `control_calibration.py:793-799` | Open | Open | Save failures are caught and printed, then HTTP 200 is returned. Operator thinks calibration persisted. |
| M3 | Medium | K | `control_api.py:660-675` | Open | Open | Revision check and hot-config apply are not under one lock across the whole request. |
| M4 | Medium | K | `ptz_owner.py:30-33` | Open | Open | Public `owner` setter allows any value; safety/steal rules only enforced in `request()`. |
| M5 | Medium | K | `control_api.py:743-749` | Open | Open | `/api/v1/health` silently catches all exceptions; disk component omitted if recorder missing. |
| H12 | Medium | D | `fusion.py:46,50` | Open | Open | `match_dist_scale=True` clamps effective match radius to 40 px floor; tiny/far boxes get larger association window than intended. Flag-off path unaffected. |
| L1–L6 | Low | K | various | Open | Open | `testbed` owner refs, archived doc links, `gps_meshtastic` duplication, stale `run.py` GPS source, estimator default mismatch, mypy/type debt. |
| N5 | Medium | K | `config.orin.servo.yaml:53` / `detector.py:44-48` | Open | Open | yolo11n swap is config-only; no runtime existence check, fallback, or detector test. Missing engine file prevents pipeline start. |

### 3.2 Firmware

| ID | Severity | Source(s) | Location | `origin/main` | Branch `d99b23c` | Notes |
|---|---|---|---|---|---|---|
| H7 | High | K | `gps_l76k.h:63-65`, `tracker/main.cpp:70` | Open | Open | `l76k_init()` ignores `interval_ms`; L76K stays at default ~1 Hz despite 2 Hz beacon. |
| H17 | High | D | `tracker/main.cpp:50-58` | Open | Open | `battery_mv()` reconfigures ADC reference and resolution on every beacon cycle (~500 ms). |
| H18 / N1 | High / Medium | D + K | `base/main.cpp:158` | Open | Open | Reboot detector `pkt.tracker_ms + 1000 < last_tracker_ms` false-positives on `millis()` wrap (~49.7 days) and delayed packets. |
| M7 | Medium | K | `base/main.cpp:42-50` | Open | Open | `arm_receive()` retries once and discards return value; base can go deaf silently. |
| M8 | Medium | K | `platformio.ini:20` | Open | Open | RadioLib `^7.1.2` resolves to 7.7.1; build not reproducible. |
| M9 | Medium | K | `tracker/main.cpp` | Open | Open | Unconditional `Serial.printf` may block when no USB host attached. |
| L36 | Low | D | `tracker/main.cpp:57` | Open | Open | Battery ADC result is not range-validated before transmission. |
| L37 | Low | D | `base/main.cpp:139-145` | Open | Open | RX is not re-armed until after `readData()` and RSSI/SNR reads; a packet arriving during processing can be missed. |
| L7–L9 | Low | K | `tracker/main.cpp`, `gps_l76k.h`, `packet.h` | Open | Open | Stale PMTK/57600 comments (partially fixed in `gps_l76k.h`), dead `interval_ms` param, `_Static_assert` in C++. |

### 3.3 iOS / watchOS

| ID | Severity | Source(s) | Location | `origin/main` | Branch `d99b23c` | Notes |
|---|---|---|---|---|---|---|
| C1 | Critical | K | `Sources-Watch/Assets.xcassets/AppIcon.appiconset` | Open | Open | Simulator build fails because watch AppIcon set has no iPhone/iPad-applicable content. |
| C2 | Critical | K | `MergedLiveView.swift:297` | Open | Open | `GlassEffectContainer` is called but never defined; will be a compile error once C1 is fixed. |
| C5 / N3 / H14 | Critical / High | D + K | `WaveCamClient.swift:5-7`, `WaveCamApp.swift:82`, `ConnectionView.swift:136`, `WatchConnectionStore.swift:12` | Open | Open | Default Orin URLs are HTTP; bearer token is sent in the clear. Token is also sent to watch via `updateApplicationContext` and held only in memory (lost on watch app restart). |
| H5 / N2 | High | K | `WaveCamClient.swift:1778-1786`, `1812-1832`, `1899-1927` | Open | Open | `candidateOrder()` still includes tether when `activeRoute == .wifi` and `now < nextTetherProbeAt`; both status polls **and** control POSTs wait 3 s on absent tether. |
| M10 | Medium | K | `TuneView.swift:287-329` | Open | Open | Nested `Button` breaks preset delete affordance. |
| M11 | Medium | K | `CalibrateView.swift:353-366` | Open | Open | UI resets to `.idle` even if `session/exit` POST fails. |
| M12 | Medium | K | `CalibrateView.swift:378-379` | Open | Open | Level uses `gravity.x * 90.0` small-angle approximation. |
| M13 | Medium | K | `WatchClient.swift:136-169`, `209-219` | Open | Open | Watch probes tether on every poll with no recheck interval. |
| M14 | Medium | K | `WatchSessionRecorder.swift:155-160` | Open | Open | JSONL written synchronously on `@MainActor`. |
| M15–M16 | Medium | K | entire iOS/firmware | Open | Open | No automated iOS or firmware tests; no firmware CI. |
| N4 | Low | K | `MergedLiveView.swift:243-253` | Open | Open | Unstructured `Task` in `handleHome()` can outlive the view; no cancellation handling. |
| L33 | Low | D | `WaveCamClient.swift:22,26` | Open | Open | Default URL force-unwraps (`URL(string: ...)!`). |
| L34 | Low | D | `FeedComponents.swift:39,166` | Open | Open | MJPEG coordinator mixes GCD with async/await and scans buffer with `range(of:)` O(n) per frame. |
| L35 | Low | D | `SessionLogView.swift:174` | Open | Open | `#Preview` shows `ToolsView()` instead of `SessionLogView()`. |
| L10–L14 | Low | K | `build-device.sh`, `project.yml`, `WaveCamDefaults`, etc. | Open | Open | Hard-coded UDID/team/paths, hidden build output, duplicated URLs, possible log duplication, background health timer. |

---

## 4. Cross-Reviewer Notes

### Estimator covariance (D-C1 / K-M6 / D-H9)

- **Kimi** scored this Medium because it is shadow-only today.
- **DeepSeek** scored it Critical because the same `_scalar_update` will be trusted by future COMMAND/estimator-driven control.
- **Merged assessment:** keep **Critical** with the caveat *"no runtime impact today; must be fixed before closed-loop use"*.

### Token security (D-C5 / K-N3 / D-H14)

- **DeepSeek** flagged the HTTP cleartext transport to Orin.
- **Kimi** flagged the WatchConnectivity plaintext context sync and the fact that the watch does not persist the token.
- **Merged assessment:** combine into one Critical finding covering both transport vectors and watch persistence.

### Tether polling (K-H5 / K-N2 vs. D-M25)

- **DeepSeek** says the phone/watch failover divergence is fixed.
- **Kimi** found that the iOS candidate order itself is buggy and now also stalls control POSTs.
- **Merged assessment:** the failover *logic* is intentional and consistent, but the *candidate order* still includes tether during the recheck interval. Both findings are valid; K-N2 is the actionable bug.

### Stale PMTK comments (K-L7 vs. D-H19)

- **DeepSeek** marked H19 fixed because `gps_l76k.h` comments were updated.
- **Kimi** L7 remains open because `tracker/main.cpp:64-66` and `radio_config.h:25` still mention PMTK/57600/5 Hz floor.
- **Merged assessment:** partially fixed; retain L7 with updated locations.

### config overlay / unknown YAML keys (K-M1 / D-M21)

- **Kimi** focused on raw-value application without type/range validation.
- **DeepSeek** focused on unknown keys being silently ignored.
- **Merged assessment:** same code path; combined into one Medium finding.

---

## 5. Prioritized Next Steps

### Immediate (before next field test)

1. **C1** — fix watch AppIcon set so the iOS simulator build passes.
2. **C2** — define or replace `GlassEffectContainer`.
3. **C5/N3/H14** — move watch token to the watch Keychain; document HTTP risk on local network or add HTTPS/TLS option.
4. **H5/N2** — exclude tether from `apiCandidates()` until `nextTetherProbeAt` expires for both reads and writes.
5. **K-H4 / D-H13** — decide whether GPS fail-closed STOP is acceptable or implement a hold/cost behavior.
6. **D-C4** — initialize estimator as soon as FOV curve becomes non-empty instead of waiting for the 120-frame gate.

### Short-term (next 1–2 weeks)

7. **D-C1/H9** — switch estimator covariance update to numerically stable `(I-Kh)P` or Joseph form.
8. **D-H7** — close `claim_manual(takeover=True)` race by requesting manual before releasing the old owner, or by serializing through `ptz_owner`.
9. **K-M2** — return non-200 / `persisted: false` when calibration/FOV save fails.
10. **K-H7** — implement/config L76K 5 Hz (`PCAS02`) or align beacon/doc to actual ~1 Hz cadence.
11. **D-H17** — move ADC reference/resolution config to `setup()`.
12. **D-H18/N1** — use a boot/session nonce in packets instead of `millis()` wrap heuristic.
13. **K-N5** — add detector model existence check and a smoke test; document rollback to `yolov8n.engine`.

### Medium-term

14. Add firmware CI (M16) and a minimal host-compiled firmware test harness.
15. Add iOS XCTest target (M15) starting with JSON decode and failover tests.
16. Refactor oversized iOS files (`WaveCamClient`, `CalibrateView`, `MergedLiveView`, `TuneView`).
17. Address remaining low-priority debt (L1–L14, D-L33–L37).

---

## 6. Prevention / Merge Criteria

- Any new config key must be added to `HOT_CONFIG_KEYS`, setters, `/config` snapshot, and docs.
- Any PTZ/owner/calibration change must include a regression test for the race or failure path.
- iOS simulator build must pass before an iOS PR is merged.
- Firmware `pio run` must pass in CI before a firmware PR is merged.
- Model-path changes require runtime existence check and a documented rollback.

---

## 7. Validation Disclaimers

- DeepSeek's review was branch-only; `origin/main` status for DeepSeek findings is inferred from the Kimi follow-up (they are at least as bad as the branch, minus the four branch fixes).
- The yolo11n engine file itself is not tracked in Git; only the config path changed. Deployment truth must be verified separately.
- No hardware-in-the-loop tests were run; firmware findings are validated by code inspection and `pio run`.

---

*No source files were edited during this merge review. Report saved to `docs/reviews/2026-06-16-merged-technical-debt-review.md`.*
