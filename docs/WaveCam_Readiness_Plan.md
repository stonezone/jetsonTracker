# WaveCam Readiness Plan

**Goal:** camera follows me automatically and keeps me framed while foil-surfing 50–300 m offshore.

**Updated assumptions (2026-06-23):**
- Base Wio has a battery installed; acquire fix on battery power, then connect USB data.
- Tracker Wio rides on the subject in a waterproof case.
- GPS source is custom direct-LoRa firmware (`firmware/direct-lora/`); Meshtastic is dropped.
- Detector is YOLO11n TensorRT (swapped from yolov8n on 2026-06-15).
- Orin boots pure NVMe via systemd-boot (the microSD died 2026-06-11) — relevant for field troubleshooting and deploys.

**Current operator app (5 tabs):** Live / Calibrate / Tools / Connect / Media. The old separate "PTZ" tab is merged into **Live** (manual pan/tilt/zoom now lives there). A watchOS companion (WaveCamWatch) logs GPS + IMU. Build/install on-device via `build-device.sh` (simulator builds are blocked by a watch AppIcon quirk).

**Agent advisor (built-in):** an on-demand assistant (`POST /api/v1/agent/{chat,summon}`) that shells out to the Claude Code CLI (`claude -p`) on the operator's Claude subscription (default provider; alternates: DeepSeek / GLM / Kimi). Reachable from the live web page ("ASK CLAUDE") and the iOS app. By default it is **supervise-only** — it can inspect status and advise but cannot move the camera. An operator-only **ARM toggle** (default OFF, 600 s TTL, KILL-disarmed) grants it a control shell for attended operation only.

---

## Priority 1 — Must fix before first surf session

| # | Item | Why it blocks go-live | Owner | Est. time |
|---|------|------------------------|-------|-----------|
| 1.1 | **Rotate exposed Google OAuth client secret** | Anyone with the old secret can access `wavecam.freddieland.com`. | Zack (Google Cloud Console) + Claude | 30 min |
| 1.2 | **Validate direct-LoRa over water at 50 / 150 / 300 m** | Body shadowing, case losses, and water surface are unvalidated. If packets drop, GPS pointing fails. | Zack (field) + Claude (log analysis); Kimi optional review | 1 session |
| 1.3 | **Run the calibration v3 flow + validate at ≥50 m on the actual beach** | Calibration v3 is a single-screen flow: location + height (datum = base-relative or sea-level) → heading (operator-set; the phone magnetometer is unusable near the motor) → aim on the Live tab (feed + zoom) → Capture / multi-point Refine (least-squares offset) → Validate → Confirm. Heading capture needs clear ≥50 m line-of-sight; bad calibration → wrong bearing → camera misses subject. Pan/tilt scale is fixed at **14.4 counts/deg** (measured). | Zack | 1 session |
| 1.4 | **Confirm base-Wio battery-acquire workflow in the field** | With battery installed, verify it gets a fix on battery and stays stable after USB data is connected. Note: `calibration_valid` is **session-scoped** — any `wavecam.service` restart resets it, so re-validate after every boot and minimize restarts mid-session. | Zack | part of 1.2/1.3 |
| 1.5 | **Verify iOS + watchOS builds are current on devices** | A stale app against a new backend can fail silently. | Claude | 1–2 hrs |

**Stop/go:** Do not proceed to an auto-framing session until 1.1–1.5 are done.

---

## Priority 2 — Will determine if it actually keeps you framed

| # | Item | Why it affects tracking | Owner | Est. time |
|---|------|--------------------------|-------|-----------|
| 2.1 | **P2: GPS→fusion confidence injection** | Color-only confidence (0.45) < lock threshold (0.60), so color cannot *acquire* at distance. GPS must boost vision confidence on blobs near the expected bearing. This is the single biggest enabler for auto framing at 200–300 m. | Claude (backend); Codex/DeepSeek optional review | 1–2 days |
| 2.2 | **Persistent track ID (ByteTrack / BoT-SORT)** | Without it, other surfers, orange buoys, or boats can steal the lock when YOLO drops out. | Claude (backend); Codex/DeepSeek optional review | 1–2 days |
| 2.3 | **Evaluate external 10 Hz GNSS (SparkFun MAX-M10S)** | L76K is capped around 5 Hz and real cadence may be lower. If latency/rate is the binding constraint, upgrade the tracker GNSS. | Zack (hardware) + Claude (firmware); Codex optional review | order + 1 day |
| 2.4 | **Enable GPS-driven zoom (`drive_zoom`)** | Cinematic zoom only works with a YOLO person box. In `gps_only` or far-range mode, zoom stays wide unless GPS drives a distance→zoom curve. | Claude (backend); Codex/DeepSeek optional review | 1 day |
| 2.5 | **Base drift / bump revalidation** | If the tripod gets bumped after `base_lock`, the camera reference is wrong for the rest of the session. Phone IMU drift monitor exists but does not re-latch base. | Claude (backend); Codex/DeepSeek optional review | 1 day |

**Stop/go:** First auto-framing session can happen once 2.1 is prototyped and yard-tested. Full "keeps me framed at 300 m" requires 2.1–2.5 complete.

---

## Priority 3 — Operational polish

| # | Item | Why it matters | Owner | Est. time |
|---|------|----------------|-------|-----------|
| 3.1 | **MJPEG preview through Cloudflare Access** | Remote monitoring may not show video. Controls/API work, but preview is needed to confirm framing remotely. | Claude (Cloudflare config + backend); Codex optional review | 1–2 hrs |
| 3.2 | **Field hardening: wet case, antenna orientation, packet-age histograms** | Waterproof case + body orientation may change link budget dramatically. Need data. | Zack (field) + Claude (analysis); Kimi optional review | 2–3 sessions |
| 3.3 | **Watch session scoring pipeline** | Ingest 1 Hz GPS + 4 Hz IMU JSONL from the WaveCamWatch companion app for offline scoring and debugging; the same scored data feeds the agent advisor when reviewing a session. | Claude (iOS/watch + ingest); Codex optional review | 2 days |
| 3.4 | **Enable shadow Kalman estimator** | Currently observe-only. After validation, enable it for smoother fusion. | Claude (backend); Codex/DeepSeek optional review | 1 day |

---

## Risks if skipped

- **No P2 GPS→fusion:** camera points at the subject via `gps_only`, but vision never locks → no cinematic zoom → subject is a small unframed blob at distance.
- **No track ID:** lock flips to other surfers/orange objects in a lineup.
- **No over-water RF validation:** link may drop unpredictably; you won't know until you're in the water.
- **No OAuth rotation:** remote access is compromised.
- **No base drift check:** a bumped tripod silently ruins GPS pointing for the rest of the session.

---

## Safety & Constraints

These invariants always hold, independent of any item above:

- **KILL is human-only and supreme.** It is never an agent capability; it disarms the agent and stops motion, and must stay reachable in the iOS app at all times.
- **The agent is supervise-only by default.** It only moves the camera while the operator-only ARM toggle is on (default OFF, 600 s TTL, KILL-disarmed); unarmed it can only inspect and advise.
- **Deploy only via `deploy.sh`** (stamps `/version`); never bypass it.
- **`calibration_valid` is session-scoped** — a `wavecam.service` restart resets it; re-validate after each boot.

---

## Suggested first milestones

1. **Milestone A — Safe pointing session (no framing guarantee)**
   - P1 items done.
   - `tracking.mode: gps_only` working over water.
   - Outcome: camera follows bearing, operator manually zooms/records.

2. **Milestone B — Auto-framing session (yard/beach)**
   - P1 + 2.1 (GPS→fusion) + 2.4 (GPS zoom) done.
   - `tracking.mode: auto` acquires and holds lock.
   - Outcome: camera frames subject automatically at moderate range.

3. **Milestone C — Production surf session**
   - P1 + all P2 + 3.2 (field hardening) done.
   - Outcome: reliable auto-framing at 50–300 m in real surf conditions.
