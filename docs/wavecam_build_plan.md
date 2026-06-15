# WAVECAM v2 — Reconciled Build Plan (Yard-MVP first, LoRa GPS)

## Context

The engineering spec from the planning pass is sound and reconciled against the overnight build.
Two pivots from Zack (2026-05-31) reshape the sequencing:

1. **Watch → LoRa/Meshtastic for GPS.** Removes the #1 blocker (Watch LTE stability) and the
   entire iOS/watch LTE-tunnel relay path. GPS becomes a small Orin-side Meshtastic serial ingest,
   added when the LoRa hardware arrives.
2. **Yard-testable vision-only build FIRST.** Build everything except GPS now — PTZ loop, YOLO,
   color tracker, web UI, with an agent CLI on the Orin as the between-events supervisor — and test it
   in the yard. Wire GPS in later.

Intended outcome: an autonomous **vision-follow** rig you can test in the yard this week; GPS-cued
acquisition + wave-state classification layer in once LoRa lands.

This file is the canonical in-repo plan. Agent side notes can exist outside the repo, but this file
is the shared source of truth for phase order and current status.

## Current implementation status (2026-06-01)

- **Phase 1 yard MVP is built and deployed to the Orin.** It still needs Zack's live yard test.
- **Vision follow exists:** YOLO person + HSV orange/red color cue + temporal target selection +
  PTZ pan/tilt centering + zoom-to-frame.
- **Dashboard control exists:** Vision Follow start/stop/status, live preview, PTZ controls,
  health/network/media panels.
- **Safety guard added:** dashboard now rejects starting GPS tracking while Vision Follow is running,
  rejects starting Vision Follow while GPS tracking is running, and blocks manual PTZ movement while
  an autonomous PTZ loop owns the camera.
- **GPS is not part of Phase 1.** Watch/iPhone GPS is deprecated for this project path; LoRa/
  Meshtastic ingest starts when hardware arrives.
- **Known yard-test limitation:** stop restores pan/tilt to the starting pose and widens zoom by timed
  velocity. It is not an exact full pose restore until absolute zoom restore is added.

## Layer model (Zack's breakdown)

| Layer | Runs it | Timing | Status for yard MVP |
|---|---|---|---|
| PTZ control loop | Python service | 10–30 Hz | exists (`visca_backend` + follow loop) |
| YOLO inference | Jetson TensorRT | 10–60 FPS | exists (`vision_tracker`, yolov8n.engine) |
| Color tracker | OpenCV | 10–60 FPS | built (`vision/color_detector.py`) |
| GPS / Meshtastic ingest | Python service | 0.2–2 Hz | **deferred — LoRa not here yet** |
| Web control UI | dashboard (→ FastAPI) | human | exists (stdlib) |
| Agent supervisor | agent | sec–min | agent CLI on Orin; manual tuning for MVP |

## What already exists (reconciliation)

| Yard-MVP need | Status | File(s) |
|---|---|---|
| Vision-follow loop (YOLO person/color → PTZ center + zoom-to-frame, prop + feed-forward) | DONE — dashboard-managed service script | `vision/vision_follow.py`, `dashboard/follow_runner.py` |
| YOLO detection (person) | DONE — **YOLOv8n.engine** baseline | `vision/vision_tracker.py` |
| PTZ control + velocity + encoder readback | DONE (VISCA, bench-validated, no auth) | `camera_control/visca_backend.py` |
| RTSP ingest (latest-frame) | DONE | `vision/frame_source.py` |
| Web UI: preview /2, PTZ, tracking/follow start/stop, record, health, session | DONE (stdlib) | `dashboard/dashboard.py` |
| **Color tracker (HSV orange + red wrap)** | DONE | `vision/color_detector.py` |
| Normalized vision offset + size-gate | DONE (reusable for target pick) | `gps_fusion/vision_assist.py` |
| GPS pointing (bearing→encoder, calibration) | EXISTS but GPS-path → **deferred to LoRa** | `pointing_controller.py`, `camera_pose.py` |
| Wave-state FSM, additive fusion, pre-roll, config-first | MISSING → later phases | — |

## Addendum to the spec (unchanged design deltas)

1. **Two modes (winging / tow_boogie), session toggle.** Orange rashguard = the **universal primary
   cue** (the yard MVP uses it directly); wing is the mode-2 occlusion case (track wing when orange
   hidden). Orange HSV calibrated once.
2. **Riding cone = a per-spot calibration parameter** (south↔north shore differ), captured by aiming
   the camera down the line and reading pan→bearing. *Deferred to the GPS phase.*
3. **RETURNING disambiguation:** sustained out-of-cone **and** speed below riding threshold (cutback-
   safe). *GPS phase.*
4. **YOLO26** is `Unvalidated` from my knowledge (post-cutoff); **YOLOv8n is the working baseline** and
   is what the yard MVP runs. YOLO26-vs-YOLO11 is a benchmarked decision later.

## GPS lane — direct-LoRa (Watch deprecated as GPS source)

- **Watch/iPhone LTE relay path is deprecated.** The Apple Watch now records offline sessions only and
  provides safety/record remote controls; it does **not** feed GPS to the Orin.
- **Hardware:** 2x Seeed Studio **Wio Tracker L1 Lite**. Nordic nRF52840 + SX1262 LoRa + L76K GNSS.
- **Physical split:** one tracker rides with Zack in a waterproof case; one tracker stays with the
  Orin/camera as the base node over USB serial, ideally with an elevated external antenna. The base Wio
  now has a battery installed; acquire the fix on battery power, then connect USB data to avoid host USB RF noise.
- **New ingest:** custom direct-LoRa firmware on the Wios; base emits JSONL over USB serial →
  `DirectRadioGps` → normalized fix. Tracker sends 32-byte LoRa packets; base emits its own position at
  1 Hz plus relayed tracker packets.
- **Validated:** outdoor fixes, base→remote distance/bearing, coarse-pointing arbiter, and tracking.mode
  (`auto`/`gps_only`/`vision_only`).
- **Still unvalidated:** long-range over-water link budget, on-body antenna orientation, wet-case
  packet loss, and external 10 Hz GNSS (future option if L76K becomes the binding constraint).

## PTZ primary protocol — RESOLVED (backend-agnostic + P0 bake-off)

Protocol decision: ONVIF absolute/readback was validated, and VISCA velocity/readback was bench-
validated. `ptz_adapter` stays **backend-agnostic**; a bake-off names the primary from measured
latency/readback/rate/reliability (+ ONVIF credential check).

---

## Build sequence

### Phase 1 — Yard Test MVP (vision-only, no GPS)  ← BUILT; YARD TEST PENDING
Dashboard-controllable service with an orange color cue. Testable in the yard now
(you in the orange rashguard).

- **1a. `vision/color_detector.py` (DONE):** HSV orange band + red hue-wraparound (two bands) + blob
  pre-filter → candidate boxes. Offline-testable on sample frames; orange HSV tuned in the yard.
- **1b. `vision/vision_follow.py` (DONE):** frame(/2) →
  YOLO person **+ color** → pick target (color-confirmed person; largest/nearest-to-center) → PTZ
  pan/tilt center + zoom-to-frame (reuse the proven prop + feed-forward + zoom logic). Vision-only.
  Graceful stop restores camera home. Tunable gains via config/env.
- **1c. Dashboard (DONE):** **Vision Follow** start/stop/status mode (subprocess like
  `TrackerRunner`) + live numeric readout (offset, size, target source: yolo/color/both).
  Manual PTZ is blocked while an autonomous loop owns the camera.
- **1d. FastAPI:** *(surfacing a reversal)* — you earlier chose to migrate the dashboard to FastAPI.
  For fastest time-to-yard-test I recommend **reusing the working stdlib dashboard now and migrating
  to FastAPI immediately after** (Phase 1.5), so the yard test isn't blocked by a rewrite. Say the
  word to do FastAPI-first instead.
- **Exit:** in the yard, the camera autonomously centers + frames you (orange) through a sustained
  walk/jog-around; start/stop + live readout from your phone; camera returns home on stop.

### Phase 1.5 — FastAPI migration of the dashboard
Port the ~30 endpoints 1:1 to FastAPI + Pydantic, behavior verified identical, then build later
endpoints (config, agent) on the typed boundary. (Sequencing per 1d.)

### Phase 2 — LoRa/Meshtastic GPS ingest  (hardware-gated)
`gps_ingest/` Meshtastic serial → normalized DTO (course/speed from position deltas). Replaces the
Watch path. Stub the interface now so Phase 3 develops against replayed tracks.

### Phase 3 — Wave-state classifier + per-spot cone calibration  (needs GPS)
`gps_fusion/wave_state.py`: RIDING / RETURNING / IDLE from course+speed+calibrated cone, cutback-safe
hysteresis. Offline unit test with synthetic tracks. Cone set at calibration (aim camera down the line).

### Phase 4 — GPS-cued acquisition + additive fusion + two-mode
Coarse GPS point → vision lock; extend `vision_assist` → additive scoring (gps+color+yolo+motion,
per-mode weights, lock/unlock hysteresis); winging/tow mode toggle. Field test.

### Phase 5 — Pre-roll recorder
Ring buffer (continuous short `-c copy` segments + concat-last-N on trigger) to catch the takeoff.

### Phase 6 — Config-first foundation + Orin agent supervisor
`active.yaml`/`last_good.yaml`/`limits.yaml` + validator + rollback + hot-reload (spec §13). Then the
between-events agent loop (spec §11/§15) — **hard-gated** behind a working deterministic core.

---

## Critical files

- **New (Phase 1):** `vision/color_detector.py`, `vision/vision_follow.py`,
  `scripts/test_color_detector.py` (offline).
- **Extend (Phase 1):** `dashboard/dashboard.py` (vision-follow mode + readout); reuse the
  `TrackerRunner` subprocess pattern.
- **Reuse as-is:** `visca_backend.py`, `frame_source.py`, `vision_tracker.py`, `vision_assist.py`.
- **Later:** `gps_ingest/` (Meshtastic), `gps_fusion/wave_state.py`, config-first files;
  `pointing_controller.py` + `camera_pose.py` return to use in the GPS phases.

## Verification

- **Phase 1 (yard):** `test_color_detector.py` passes offline on sample orange frames; then live —
  camera holds you centered + framed through a walk-around, start/stop + readout from the phone,
  returns home on stop. Verify on Orin (camera live), capture a clip to `docs/verification/`.
- **PTZ bake-off (with the camera):** VISCA vs ONVIF-absolute+CGI-velocity — latency, readback, rate.
- **Replay-first (spec §17):** record raw RTSP every session; validate vision/FSM changes against
  recordings before live runs.
- **GPS (Phase 2+):** liveness gate — fixes arrive at cadence, counters advance, last-fix age shown.
- Keep the GPS-lane pivot (Watch→LoRa) reflected in the shared plan before doing more iOS GPS work.
