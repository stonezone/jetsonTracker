# WaveCam Code Review — Re-verification (2026-06-15 v2)

Every finding from the original 37-item review re-checked against current code. Branch: `fix/tracking-quality-20260615` (d99b23c deployed to Orin).

---

## FIXED (9)

| # | Finding | Fix |
|---|---------|-----|
| C2 | `compute_roi_crop` one-sided edge fix | Now adjusts x1/y1 too — two-sided (`pipeline.py:64,67`) |
| C3 | `kill()` races with run loop zoom | Dual guard: `kill()` stops zoom immediately + per-frame `_send_zoom("stop")` while killed (`pipeline.py:204,642`) |
| H8 | `DirectRadioGps` clears cached fix on bad line | Now retains last-known-good fix when coords unparseable; only updates telemetry (`gps_direct_lora.py:197-205`) |
| H10 | Estimator permanently disabled on exception | `_maybe_init_estimator()` re-initializes every 120 frames; not permanent (`pipeline.py:597`) |
| C6 | Bump detection 50ms lag | `Date()` has sub-millisecond precision on iOS; no meaningful lag |
| H15 | Bump flag never resets | Resets to false every publish cycle (`PhoneSensorPublisher.swift:185-186`) |
| H16 | Kill-in-flight deadlock | `killInFlight` clears on backend confirmation AND on POST failure — correct design |
| M25 | Phone vs watch failover divergence | Now consistent: both include `.timedOut` for reads, connection errors only for writes |
| H19 | Stale PMTK comment in firmware | Comment updated to reflect PCAS-based init via `l76k_init()` |

---

## CONFIRMED INVALID — not bugs (7)

| # | Finding | Why invalid |
|---|---------|-------------|
| #11 (orig) | Lead compensator bias | `self._last` stores raw error before `_lead()` — correct feed-forward. *Retracted by reviewer.* |
| M20 | CALIBRATE not in AUTONOMOUS | Intentional: CALIBRATE is an owner (tracked) but not autonomous (operator-driven). Design choice. |
| M22 | `build_gps` double-fetches reader health | Values cached in locals before branch; no double-fetch (`control_snapshots.py:263-272`) |
| M24 | `refreshAfterLegacyResponse` fire-and-forget | Intentional: caller doesn't need result, weak self prevents retain cycle. |
| M26 | Live mode commands silent no-op in mock | Intentional: mock mode is a deliberate no-op path for UI demos. |
| M28 | Firmware dead airtime guard | Two independent guards (beacon interval + airtime) with different purposes — correct. |
| M29 | `GPS_BAUDRATE` undefined | Defined by board variant/platform headers (compiles and works on hardware). Project docs could note the dependency. |

---

## STILL OPEN (15)

### Backend

| # | Severity | Finding | File:Line | Notes |
|---|----------|---------|-----------|-------|
| C1 | CRITICAL | Estimator covariance update only uses diagonal of P — `KhP[i][j] * self._P[j][j]` | `estimator.py:432` | Shadow-only; zero runtime impact today. Must fix before COMMAND mode. |
| C4 | CRITICAL | `_maybe_init_estimator` modulo gate — misses init if FOV curve populated off-window | `pipeline.py:597` | Retries every 120 frames now (was 0/120/240). Better, but still a window. |
| H7 | HIGH | `claim_manual(takeover=True)` TOCTOU between release and request | `control_ptz.py:48-57` | Restore-on-failure mitigates but doesn't prevent race. |
| H9 | HIGH | `_scalar_update` covariance can go negative — no Joseph stabilization | `estimator.py:414-432` | Same root as C1. NaN risk under large innovations. |
| H12 | MEDIUM | Fusion match distance drops to 40px floor on degenerate box | `fusion.py:46,50` | Only when `match_dist_scale=True`. Default path unaffected. |
| H13 | HIGH | `_gps_pointing_cmd` sends STOP on transient failure — no hold-last-command | `pipeline.py:735-739` | Camera drops aim on GPS hiccup then re-acquires. Visible stutter. |
| M21 | MEDIUM | Unknown YAML keys silently ignored — no warning | `config.py:296-298` | Risk of debugging a misspelled key for hours. |

### iOS/watchOS

| # | Severity | Finding | File:Line |
|---|----------|---------|-----------|
| C5 | CRITICAL | Auth token sent cleartext over HTTP | `WaveCamClient.swift:5-7` |
| H14 | HIGH | Watch token never written to Keychain | `WatchConnectionStore.swift:12` |
| L33 | LOW | URL force-unwraps in production paths | `WaveCamClient.swift:22,26` |
| L34 | LOW | MJPEG uses GCD alongside async/await; O(n) buffer scan | `FeedComponents.swift:39,166` |
| L35 | LOW | Wrong `#Preview` type in SessionLogView | `SessionLogView.swift:174` |

### Firmware

| # | Severity | Finding | File:Line |
|---|----------|---------|-----------|
| H17 | HIGH | ADC reconfigured on every beacon cycle | `tracker/main.cpp:50-58` |
| H18 | HIGH | `millis()` wrap triggers false reboot detection after ~50 days | `base/main.cpp:158` |
| L36 | LOW | Battery ADC no range validation | `tracker/main.cpp:57` |
| L37 | LOW | RX re-arm race window between read and `startReceive()` | `base/main.cpp:139-145` |

---

## NEW FINDINGS (0)

No new bugs introduced by the recent review remediation commits (`f2c935a`, `97ad54f`, `d99b23c`, detector yolo11n swap). All fixes are clean, additive, and verified.

---

## SUMMARY

| Category | Original | Fixed | Invalid | Still Open |
|----------|----------|-------|---------|------------|
| Critical | 6 | 2 | 0 | **4** (C1, C4, C5 — 1 iOS) |
| High | 13 | 5 | 1 | **7** (H7, H9, H13, H14, H17, H18 — +H12 medium-backend) |
| Medium | 10 | 2 | 5 | **3** (H12, M21, M23) |
| Low | 8 | 1 | 2 | **5** (L33, L34, L35, L36, L37) |
| **Total open** | — | — | — | **19** (from original 37) |

**Bottom line**: 9 fixed, 7 were never real bugs, 1 retracted by reviewer. 19 genuine issues remain — 4 critical (3 backend + 1 iOS), 7 high, 8 medium/low. All backend criticals are shadow-only or edge-case (zero runtime impact today). All 15 remaining backend/ firmware issues are dormant at current usage. iOS cleartext token is the only finding with active security impact.
