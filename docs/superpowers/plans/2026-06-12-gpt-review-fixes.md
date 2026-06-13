# GPT review fixes — implementation plan (2026-06-12)

External review (GPT, validated by Claude against the live rig 2026-06-12)
surfaced 8 fixes. Items 1–5 are code, implementable now (dark where behavior
changes); 6–8 are gated on other work and carry NO code in this plan.

**Guardrails (binding):**
- Branch `fix/gpt-review-batch`. One commit per package. NEVER push to main.
- Behavior-changing paths ship FLAG-OFF (config default preserves today's
  behavior) unless the package says otherwise.
- Every new config key must be added in BOTH `wavecam/config.py` (dataclass +
  loader) AND the YAML, or it silently vanishes (loader known-sections gotcha).
- Suite (`python3 -m pytest -q` from `orin/wavecam/`) + mypy
  (`python3 -m mypy --config-file mypy.ini`) green after every package.
- New constants need provenance comments. No unrequested refactors.

## Package 1 — onboard camera AI: force off + surface failure
`config.orin.servo.yaml` `camera_ai.disable_on_start: false` → `true`
(matches the safer default at config.py:80). In the disable path (find it via
`grep -rn disable_on_start wavecam/`): on failure, record an event
`events.record("camera_ai", "disable FAILED — onboard tracker may fight the
loop")` and a health note; on success record one "disabled" event. Test: fake
CGI failure → event recorded, startup continues (must never block boot).

## Package 2 — split display-stale vs drive-stale GPS
New key `gps.drive_stale_sec` (float, default 8.0, hot-tunable — register in
`control_config.py` hot keys, range 1–60). `tracking_arbiter.py` currently
gates GPS viability on `stale_threshold_sec` (45 s — fine for DISPLAY, far
too old to STEER: a 44 s-old fix on an 8 m/s foiler points ~350 m behind).
Arbiter uses `drive_stale_sec`; status/display keeps `stale_threshold_sec`.
Tests: fix aged 10 s → not viable to drive; aged 5 s → viable; display
staleness unchanged at 45 s boundary.

## Package 3 — GPS-cued detector ROI (flag-off)
`tracking_arbiter.py:24` defines `search_roi` but nothing consumes it (P2
plumbing without a consumer). New key `fusion.gps_roi_enabled` (bool, default
false, hot-tunable). When enabled AND arbiter source == gps_tracker AND
search_roi is set: crop the detector input frame to the ROI (clamped to frame,
min 320 px square), map detections back to full-frame coords before fusion.
Color detection stays full-frame. Flag OFF = byte-identical behavior. Tests:
coord mapping round-trip, clamping at frame edges, flag-off no-op.

## Package 4 — scale-aware match_dist (flag-off)
`fusion.py` uses fixed `match_dist` 120 px to associate color blob ↔ person
box. New key `fusion.match_dist_scale` (bool, default false, hot-tunable).
When on: effective radius = `match_dist * (person_bbox_h / 240.0)` clamped to
[40, 240] px (240 px ≈ a near subject at 720p; provenance: review 2026-06-12,
to be field-tuned). Tests: small far box tightens radius, clamps hold,
flag-off identical.

## Package 5 — decode-latency bench tool
New `tools/measure_decode.py` (rig-run later, NOT in CI): for each of
(a) current cv2 VideoCapture path, (b) the GStreamer nvv4l2decoder pipeline
already in `capture.py` — open the RTSP sub-stream, grab 300 frames, report
fps, mean/p95 inter-frame gap, and wall-clock staleness via frame timestamp
overlay if present. Reuse `capture.py` building blocks; do not modify
capture.py itself. Smoke test: module imports + arg parsing only (no network
in CI).

## NOT in scope (gated)
6. `ff_gain`/predictor — post-estimator-flip (the estimator's velocity state
   is the principled version of this).
7. Cinematic zoom enable — field-gated on the zoom/FOV curve (plan T1.2).
8. Watch-scored shadow session — operator-gated (G3), tooling already shipped.
