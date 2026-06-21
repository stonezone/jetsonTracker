# WaveCam Full Project Code Review — 2026-06-15

Deep review across all three codebases: backend (`orin/wavecam/`), iOS/watchOS (`ios/WaveCam/`), firmware (`firmware/direct-lora/`). Findings only — no fixes proposed.

---

## CRITICAL (6)

| # | Area | Finding | File:Line |
|---|------|---------|-----------|
| 1 | Backend | Estimator covariance update is mathematically wrong — only uses diagonal of P for scalar updates. `bearing_std_deg` unreliable if estimator graduates to control mode | `estimator.py:429-432` |
| 2 | Backend | `compute_roi_crop` can produce undersized/inverted crops near frame edges — one-sided fix doesn't handle both directions, risks empty tensor → GPU crash | `pipeline.py:53-61` |
| 3 | Backend | `kill()` races with run loop — `_send_zoom` path unguarded for one tick after KILL, can restart zoom after stop | `pipeline.py:180-197` |
| 4 | Backend | `_maybe_init_estimator` only retries on frames 0, 120, 240… — misses init if FOV curve populated mid-session, estimator never starts | `pipeline.py:498` |
| 5 | iOS | Auth token sent cleartext over HTTP on every API call + MJPEG frame chunk; token visible to anyone on same subnet | `WaveCamClient.swift:5-7` |
| 6 | iOS | Bump detection has ~50ms measurement lag — `Date()` on next callback, not when acceleration dropped; can miss hard-tap bumps (the one safety-relevant sensor path) | `PhoneSensorPublisher.swift:146-158` |

---

## HIGH (13)

| # | Area | Finding | File:Line |
|---|------|---------|-----------|
| 7 | Backend | `PtzDispatcher.claim_manual(takeover=True)` TOCTOU race — autonomous owner can re-acquire between release and request, leaving owner=idle when caller expected manual | `control_ptz.py:49-57` |
| 8 | Backend | `DirectRadioGps` clears entire cached fix on a single bad remote line — erases last-known-good position on transient GPS dropout | `gps_direct_lora.py:199` |
| 9 | Backend | `_scalar_update` covariance can go negative under large innovations — no Joseph stabilization; `bearing_std_deg` can return NaN | `estimator.py:414-432` |
| 10 | Backend | Estimator shadow exception permanently disables estimator for rest of session — one corrupt GPS fix kills the entire path, no retry timer | `pipeline.py:311-315` |
| 11 | Backend | Visual servo lead compensator stores biased error — `_last` is set AFTER `_lead()` returns, compounding lead over successive frames | `controller.py:69` |
| 12 | Backend | Fusion match distance drops to 40px on degenerate (height=0) YOLO box — unreasonably tight, causes real confirmed matches to be missed | `fusion.py:129-131` |
| 13 | Backend | `_gps_pointing_cmd` sends STOP on any transient compute failure — no hold-last-command fallback; camera drops aim on target while still owning `gps_tracker` | `pipeline.py:618-624` |
| 14 | iOS | Watch holds auth token as bare `String?` in memory, never written to Keychain — extractable via memory dump or backup | `WatchConnectionStore.swift:12` |
| 15 | iOS | Bump flag never resets to false when no new callback fires — sticky flag can report seconds-old bump as current telemetry | `PhoneSensorPublisher.swift:185-186` |
| 16 | iOS | Kill-in-flight guard can deadlock — `killInFlight` stays set permanently if backend never confirms kill with a non-2xx response | `WaveCamClient.swift:967-976` |
| 17 | Firmware | ADC reconfigured on every beacon cycle — `analogReference()` + `analogReadResolution()` in hot path every 500ms; ADC glitch → spurious battery reading propagating to base | `tracker/main.cpp:50-58` |
| 18 | Firmware | `millis()` wrap after ~50 days triggers false tracker-reboot detection — `0 + 1000 < UINT32_MAX` evaluates true, resetting loss counters | `base/main.cpp:158` |
| 19 | Firmware | Stale PMTK/57600 comment referencing baud rate negotiation; actual code uses PCAS at 9600 via `l76k_init()` — misleading for maintainers | `tracker/main.cpp:65-67` |

---

## MEDIUM (10)

| # | Area | Finding | File:Line |
|---|------|---------|-----------|
| 20 | Backend | `PtzOwner`: CALIBRATE in OWNERS but not in AUTONOMOUS — `can_autonomous_start("calibrate")` always returns False, semantics inconsistent with how calibrate actually owns the camera | `ptz_owner.py:15-16` |
| 21 | Backend | `config.py` silently ignores unknown/misspelled YAML keys — typo produces no warning, default silently applies; overlay path has same blind spot | `config.py:288-298` |
| 22 | Backend | `build_gps` fetches reader health twice non-atomically across a lock boundary — can return diverging `reader_alive` / `last_poll_age_sec` values | `control_snapshots.py:223-249` |
| 23 | Backend | `MeshtasticGps.close()` can abandon reader thread if `join(timeout=3.0)` expires — acknowledged in comment but leaves a race window | `gps_meshtastic.py:276-285` |
| 24 | iOS | `refreshAfterLegacyResponse` fire-and-forget unstructured Task with no cancellation/priority — stale status risk when operator issues rapid commands | `WaveCamClient.swift:1868` |
| 25 | iOS | Phone vs watch POST failover diverge on `.timedOut` — phone excludes it (avoids double-apply), watch includes it (for GET); inconsistent behavior | `WaveCamClient.swift:1942 / WatchClient.swift:159` |
| 26 | iOS | `live` mode commands silently no-op if client mode dynamically switched to mock mid-flight — operator gets zero feedback that PTZ command was dropped | `WaveCamClient.swift:1092+` |
| 27 | iOS | Hardcoded LAN IPs (`172.20.10.8` tether, `192.168.1.155` Wi-Fi) with no user-facing hint to change defaults — new user sees OFFLINE with no guidance | `WaveCamClient.swift:5-6` |
| 28 | Firmware | Dead airtime guard shadowed by beacon interval check that always fires first — checks ordered so airtime floor is unreachable at current settings | `tracker/main.cpp:172-173` |
| 29 | Firmware | `GPS_BAUDRATE` never `#define`d in any project source — relies on board variant headers resolving to 9600; implicit dependency | `tracker/main.cpp:70` |

---

## LOW / TECH DEBT (8)

| # | Area | Finding | File:Line |
|---|------|---------|-----------|
| 30 | Backend | 7 modules with **zero dedicated tests**: `controller.py` (137L), `control_ptz.py` (241L), `control_calibration.py` (~900L), `control_config.py` (~500L), `capture.py` (78L), `web.py` (~900L), `detector.py` (67L) | — |
| 31 | iOS | **Zero test files** anywhere under `ios/` — `WaveCamClient` (~1960L) contains complex failover, calibration, and state-machine logic with no unit tests | — |
| 32 | Firmware | Pure functions (`pkt_crc16`, `pkt_seal`, `pkt_valid`, seq-gap/loss-accounting logic) have **zero unit tests** despite being host-testable with no hardware | `packet.h` |
| 33 | iOS | `URL` force-unwraps in production paths on both phone and watch (`WatchConnectionStore` falls back to bang on fallback URL) | `WaveCamClient.swift:22,26` |
| 34 | iOS | `MJPEGPreviewView` mixes GCD manual queue with async/await structured concurrency; O(n) `Data.range(of:)` buffer scanning per frame boundary up to 2MB | `FeedComponents.swift:39,166-188` |
| 35 | iOS | `#Preview` in `SessionLogView.swift` instantiates wrong type (`ToolsView` instead of `SessionLogView`) — preview won't render correctly | `SessionLogView.swift:174-178` |
| 36 | Firmware | No range validation on battery ADC — disconnected VBAT pin reads 0 indistinguishable from a valid reading, no sentinel or sanity clamp | `tracker/main.cpp:57` |
| 37 | Firmware | Tiny race window between packet read and RX re-arm — DIO1 flag set between `rx_flag=false` and `startReceive()` discards packet; invisible at 2 Hz but latent | `base/main.cpp:139-145` |

---

## Summary

| Severity | Backend | iOS/watchOS | Firmware | Total |
|----------|---------|-------------|----------|-------|
| Critical | 4 | 2 | 0 | **6** |
| High | 7 | 3 | 2 | **12** |
| Medium | 4 | 4 | 2 | **10** |
| Low / Debt | 2 | 4 | 3 | **9** |
| **Total** | **17** | **13** | **7** | **37** |

**Top 3 by impact:**
1. **Estimator covariance math** (C1) — silently wrong; `bearing_std_deg` is unreliable; gates the COMMAND mode
2. **ROI crop edge case** (C2) — can produce empty tensors → GPU crash (rare but real near frame edges)
3. **Kill/zoom race** (C3) — zoom can restart one tick after KILL stops it; safety-latch breach
