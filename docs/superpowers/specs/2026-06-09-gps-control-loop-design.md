# GPS-in-the-Control-Loop — Design (2026-06-09)

## Goal

Make the LoRa GPS remote position a **coarse-pointing input in the PTZ control loop**, blended with the existing YOLO + orange-color vision, and surface GPS data in the **web UI** (`:8088`) and the iOS app.

**Operator decisions (locked):**
- **Blend = coarse-point → vision-refine handoff.** GPS aims pan/tilt/zoom when the subject is far or vision hasn't locked; YOLO+color take over fine framing once they resolve the orange rashguard.
- **Heading calibration = aim-at-remote GPS capture.** No magnetometer.
- **Scope = full pan + tilt + zoom.**

## Current state (verified)

- **Vision control loop** is a *relative visual servo*: `fusion.Fusion` blends YOLO person + orange blob into an in-frame `target_xy`; `controller.VisualServo.compute(target_xy)` emits a **velocity** PtzCommand to center it (+ `compute_zoom` holds subject box size). No absolute angles.
- **GPS ingest is deployed** (`MeshtasticGps`, off-thread, in `/status.gps`) but is a **data feed only**: `controller`/`fusion`/`pipeline` contain **zero** GPS references, and `gps_fix_snapshot` returns `distance_m=None`, `base_age_sec=None`, and the remote's *course* in `bearing_deg` (not camera→target bearing), `stale` hardcoded `False`.
- **Live-validated pointing math exists** in legacy `orin/gps_fusion/` but is unwired: `geo_calc.haversine_distance`, `camera_pose.bearing_to_pan_encoder` + `lock_base_position`, `pointing_controller.point_at(base, target)`.

## Architecture

```
MeshtasticGps (remote fix + base fix)  ── off-thread, done
        │
        ▼
geo + camera_pose + gps_pointing   ◄── PORT from legacy gps_fusion (pure, tested)
   (distance, bearing, pan/tilt/zoom target in camera frame)
        │
        ▼
TrackingArbiter (handoff)           ◄── NEW: vision-locked → vision servo;
   owner = vision_follow | gps_tracker     else GPS coarse-point; hysteresis
        │
        ▼
controller (velocity servo  +  NEW absolute-position path for GPS mode)
        │
        ▼
PTZ (VISCA)        ── gated by ptz.enabled + Emergency Stop, conservative speeds
```

Status/telemetry: `control_api` computes the real GPS geometry → `/status.gps` → web UI panel + iOS card. Calibration: aim-at-remote endpoint → `camera_pose` heading reference.

## Components (each one job, testable in isolation)

1. **`gps_geo.py`** — port `geo_calc`: `haversine_m`, `bearing_deg` on lat/lon. Pure. (We already have these in `gps_meshtastic`; consolidate to one module both import.)
2. **`camera_pose.py`** — the pan-home→compass reference + `bearing_to_pan(bearing)→pan_target`; `lock_base_position(fixes)`; holds the calibration (`home_heading`, base position). Pure given inputs.
3. **`gps_pointing.py`** — `point_at(base, target, pose) → PointingTarget(pan, tilt, zoom)`. Distance→zoom curve; tilt from camera-height/distance geometry (≈horizon at 50–300 m).
4. **`tracking_arbiter.py`** — the **handoff state machine**: inputs = (vision FusionResult, GPS PointingTarget, freshness/calibration flags); output = which source drives + the resulting command, with hysteresis so the two don't fight. Sets PTZ `owner` (`vision_follow` ↔ `gps_tracker`).
5. **`controller.py` extension** — an **absolute-position** command path (`point_to(pan,tilt,zoom)`) for GPS mode, alongside the existing velocity servo for vision mode. (Confirm Prisual VISCA absolute pan/tilt/zoom; legacy used encoder positioning, so likely supported.)
6. **`control_api` GPS snapshot** — compute real `distance_m`, `bearing_deg` (camera→target), `base_age_sec`, and derive `stale` from age vs a threshold; add `mode`/`owner`. (Fixes the half-wired feed.)
7. **Calibration endpoint** — aim-at-remote capture: record (current pan, base→remote bearing) → `home_heading`. Reuses the existing calibration store (`WCCalibrationState` already has heading/tilt/zoom).
8. **Web UI GPS panel** (`:8088`) + **iOS GPS card** (Live screen) + **iOS aim-at-remote flow** (CalibrateView already scaffolds "Base lock (GPS)" / "Exercise GPS pointing").

## Calibration flow (aim-at-remote)

1. Base Wio gets sky-view + GPS lock → `lock_base_position` averages N fixes → camera reference position.
2. Operator places the remote at a clearly visible spot, manually (or vision-assisted) centers the camera on it, taps **Capture heading**.
3. System reads the current pan encoder + computes base→remote bearing (from the two GPS positions) → `home_heading = bearing − pan`. Stored.
4. Thereafter `pan_target = bearing_to_pan(camera→target bearing)`.

A single landmark gives pan; tilt-at-distance and the zoom-vs-distance curve get a default + an optional second capture for refinement.

## Handoff state machine (coarse → fine)

- **VISION drives** when `FusionResult.locked` (orange-confirmed person, confidence ≥ lock threshold): existing visual servo. `owner=vision_follow`.
- **GPS drives** when *not* vision-locked AND GPS is fresh (`age < max_age`) AND calibrated AND base-locked: absolute coarse-point pan to bearing, zoom by distance, tilt to horizon. `owner=gps_tracker`.
- **Hysteresis**: require K consecutive locked frames to hand control to vision, and a grace window before falling back to GPS, so control doesn't flap at the boundary.
- **Neither** (no lock, GPS stale/uncalibrated): existing SEARCH behavior. Never a hard stop unless E-Stop.

## Safety (non-negotiable)

- GPS pointing runs as the **`gps_tracker`** PTZ owner — the **same category as `vision_follow`** (the tracking pipeline aims the camera, **not** the supervise-only agent). It is gated by the existing `ptz.enabled` and the **Emergency Stop / KILL latch stays reachable at all times**.
- GPS commands the PTZ **only** when calibrated + base-locked + GPS fresh; otherwise it yields to vision/search.
- Conservative coarse-point speeds; **on-rig validation before any field test**; vision FPS must stay **30+**.
- The agent/supervisor rule is unchanged — it never autonomously moves the camera.

## Data feed + displays

- `/status.gps`: `source`, `distance_m`, `bearing_deg` (camera→target), `target_age_sec`, `base_age_sec`, `stale` (age-derived), `owner`/`mode`.
- **Web UI** (`:8088`): a GPS panel — source, distance, bearing, ages, base-lock, mode (GPS vs VISION), stale flag.
- **iOS**: a GPS card on the Live screen (feature-detected on `supported.gps`) + fix CalibrateView's GPS metric (now real distance) + the aim-at-remote capture flow.

## Testing

- **Unit**: `gps_geo` (assert against legacy-validated distances/bearings), `camera_pose` mapping round-trips, `gps_pointing` distance→zoom + tilt, the **arbiter state machine** (lock→vision, unlock+fresh→gps, hysteresis, stale→search), the snapshot computation. Keep the existing vision/fusion tests green.
- **On-rig (Codex/Zack)**: pointing accuracy vs a known remote position; handoff smoothness; E-Stop interrupts GPS mode instantly; FPS ≥ 30.
- **Field**: aim-at-remote calibration, then a 50–300 m coarse-point→vision-lock run.

## Phased build + lane split (collaborative)

- **P0 — data correct + visible** (no camera motion yet): port `gps_geo`/`camera_pose`/`gps_pointing` + unit tests; real `gps_fix_snapshot`; web UI + iOS GPS readout. Low risk, immediately testable.
- **P1 — GPS aims (pan)**: arbiter handoff + controller absolute path + `gps_tracker` owner + aim-at-remote calibration endpoint/flow. On-rig.
- **P2 — full**: zoom-by-distance + tilt + tuning. On-rig → field.
- **Lane split** (Zack wants Claude + DeepSeek): **DeepSeek leads the backend control-loop** integration (arbiter, controller absolute path, `control_api` snapshot — its lane + control_api expertise); **Claude leads** the `gps_geo`/`camera_pose`/`gps_pointing` port (I built the ingest) + the iOS/web-UI displays + the calibration flow. **Cross-review every PR** (anti-vibe). **Zack** does on-rig + field calibration. Deploy stays Zack/Codex.

## Open items to confirm during P0/P1

- Prisual VISCA **absolute** pan/tilt/zoom positioning (legacy used encoder values — confirm on the live camera).
- The **zoom-vs-distance** curve (default + field-calibrate to hold subject size at 50–300 m).
- Base GPS accuracy at the tripod (a few metres of base error → bearing error that vision must absorb at short range).
