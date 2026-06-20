# Vision-GPS walk-around heading auto-calibration

**Date:** 2026-06-17 · **Origin:** Zack's field-session idea (2026-06-17), which independently
re-derived the "YOLO-assisted calibration" already named in the 2026-05-31 calibration spec.
**Status:** design — ~80% of the infrastructure already exists; the fit + walk-mode are not built.

## The idea

Instead of aiming the camera at one fixed distant landmark to set the heading, use the **moving,
GPS-tagged subject as the calibration target**:

1. Operator walks in clear view of the camera, wearing the tracking color and carrying the
   remote tracker GPS, across a **wide arc** of bearings.
2. YOLO + color keeps the subject locked; the camera tracks (subject ~centered).
3. Each frame yields a correspondence:
   `true_bearing(base→tracker, GPS)  ==  pan_enc / enc_per_deg  +  heading_offset  +  pixel_offset_deg`
4. Collect hundreds of these and **least-squares fit** `heading_offset` (and optionally the
   `enc_per_deg` slope and a tilt sinusoid). The fitted offset becomes the live heading calibration.

The coarse phone compass + the GPS bearing are the **bootstrap** (frame-1 "look roughly here");
the walk refines to a precise, self-validated heading.

## Why it's the right method

- **Hundreds of samples** average out GPS noise + YOLO jitter (vs one shaky landmark aim).
- **Self-validating** — fit residuals report the quality directly.
- **Re-derives the 14.4 enc/deg scale** as the fit slope (independent check of the "4.47 rule").
- **Measures the tilt we can't otherwise sense** — a non-level pan axis appears as a *sinusoidal*
  residual vs bearing, so it can be fit/removed instead of needing the (now-removed) level gate.
- Directly serves the product: coarse GPS cue → vision refine.

## What ALREADY exists (per the 2026-06-17 code survey)

- **`estimator.py` (Plan-3 shadow filter)** already computes the vision bearing per frame:
  `pixel_offset_deg = (pixel_cx - frame_w/2)/frame_w * fov; obs_bearing = bearing_enc + pixel_offset_deg`
  (estimator.py:329-331). It fuses GPS + vision but is **observe-only** (never commands).
- **Per-frame correspondences are already logged** to `/data/shadow/session_<ts>.jsonl`
  (pipeline.py:315-362, shadow_writer.py) when `estimator.enabled=true` — t, gps bearing,
  pan_enc, vision_updated/gps_updated flags. A walk's data is captured today.
- **All four inputs are live per frame:** `FusionResult.target_xy` pixel center (fusion.py:64-76),
  `PtzState.latest()` pan_enc + age (ptz_state.py:87-93), `gps_geo.bearing_deg()` (gps_geo.py:36-42),
  FOV-at-zoom interpolation (estimator.py:126-138; fov_curve in calibration_store.py:25).
- **Fit primitive exists:** `CameraPose.calibrate_pan_two_point()` derives offset+scale from two
  (enc, bearing) pairs (camera_pose.py:79-87) — extend to N-point least-squares.
- **Sim/replay harness** (`wavecam/tools/sim/`) can score a fit on synthetic walks.
- **Persistence solved:** reference_heading / pan anchor + scale persist atomically via
  `CalibrationStore` / `camera_pose.json`.

## Capture variants (Zack refinement 2026-06-17)

The fit is agnostic to *what* is detected (the heading offset is purely geometric), so the
calibration target can be a **reliably-detected object** rather than the live person+color path —
this removes false-match / color-lock flakiness from the samples.

- **N static spots (recommended — cleanest + easiest):** place a GPS-tagged, reliably-detected
  target (a person in-color holding the tracker = YOLO's best class + matches the live `person`
  config; or a place-and-leave object like a chair/backpack with the YOLO class switched) at **3–5
  spots across a wide arc, each ≥50 m**. At each spot, hold the camera locked and **average a few
  seconds of frames** (near-zero per-point noise). Fit offset + scale + tilt across the spots.
- **Continuous walk:** hundreds of points across the arc — more data but needs clean *continuous*
  detection; noisier per-point than the held-spot variant.
- **Single spot:** quick, but no scale/tilt and leans on one GPS reading — use ≥2–3.

Key: the GPS must sit at the target's **visual center** (minimise lever-arm error), and the target
must be **≥50 m** out (10 m ≈ 27° GPS-bearing error).

## What's missing (the build)

1. **Calibration-collection mode** — a CALIBRATE sub-mode that collects paired
   `(pan_enc, pixel_offset_deg, gps_bearing, t)` samples while vision is locked, either over a
   continuous walk OR as held bursts at N static spots (above); gate on lock-quality,
   single-target, GPS freshness, and bearing spread.
2. **N-point least-squares fit** — solve `heading_offset` (+ optional `enc_per_deg`, tilt sinusoid)
   from the collected samples; report residual RMS as the confidence/uncertainty.
3. **Commit path** — write the fitted offset into the pan-aim calibration (reuse `calibrate_pan_aim` /
   the two-point machinery) + persist.
4. **Wiring** — expose as a heading method in the CALIBRATE flow (backend endpoint + iOS button:
   "Walk-calibrate heading"), with live residual feedback.

## Caveats

- Needs a populated **FOV curve** (multi-point) for pixel→degree at the working zoom.
- **Single clean lock** during the walk — subject alone, in color, no false matches polluting samples.
- Walk at a **reasonable range** (not <~15 m) across a **wide bearing arc** for a well-conditioned fit.
- GPS↔vision latency aligns fine at walking speed; fast motion would need timestamp alignment.

## Lane / next step

This lives in the **vision/pipeline/estimator core (Codex's lane)**. Build deliberately: claim the
scope on the bus, prototype the fit against existing `/data/shadow/*.jsonl` replays in the sim
harness first (offline, zero rig risk), then add the live walk-mode + commit path. Do NOT modify the
live tracking loop without the sim-validated fit in hand.
