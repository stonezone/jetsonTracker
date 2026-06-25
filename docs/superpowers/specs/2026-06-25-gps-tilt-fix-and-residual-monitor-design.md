# GPS-only down-tilt fix + GPS↔vision residual monitor — design

- **Created:** 2026-06-25
- **Status:** DESIGN — awaiting user review, then implementation
- **Owner / lane:** Claude (iOS `ios/WaveCam` + backend `orin/wavecam`)
- **Origin:** field test 2026-06-24. GPS-only lead/speed felt good; the one defect was **insufficient down-tilt when close** (camera ~15 ft above the tracker) in GPS-only mode, while GPS+vision tilted down correctly. Full analysis in this session's report.

This spec covers the two agreed first steps of the GPS/vision improvement sequence:
- **Part A** — fix the GPS-only down-tilt by fixing the *calibration height input* (the math is correct; the input is the trap).
- **Part B** — add an observe-only GPS↔vision bearing-residual monitor so later fusion tuning is driven by data, not feel.

Parts C+ (GPS sanity-gate on vision lock, corroboration stickiness, bearing-cue enable, seeded re-aim) are **out of scope here** — they come after Part B gives us measurements.

---

## Part A — GPS-only down-tilt: fix the height input

### Problem (grounded)
GPS-only tilt depression = `atan2(subject_alt_m − alt_m, dist)` (`pipeline.py:553` target alt = `subject_alt_m`, `:540` base alt = `alt_m`; `gps_pointing.py:66-70`; `camera_pose.py:133-139`). The **math is right and distance-dependent** — it steepens downward as the subject nears. The failure is the **input**:

- The v3 heights step (`CalibrateScreenV3.swift:19-21,140-157,266-269`) presents a **datum picker** plus, in base-relative mode, a single **"Tracker vs base (m)"** field defaulting to **−1**. To encode a 15 ft (4.57 m) camera-above-water you must enter `−4.57` — backwards from how the operator thinks ("my camera is 15 ft *up*"), sign-inverted, and metric.
- Result in the field: the entered Δh was nowhere near −4.57 m (default −1, or a stale pose left `subject_alt_m=1.0` → +1 m, "subject *above* camera"), so the commanded tilt was ~flat/slightly-up at every distance. GPS+vision masked it (vision servo centers the person → tilts down regardless of pose).
- Δh matters most up close (−16.9° @15 m, −29.7° @8 m for 4.57 m) and is nearly nil offshore (−0.9° @300 m) — exactly why it showed when close and not at range.

### Design (iOS-only; backend already accepts the fields)
Replace the datum picker + base/tracker height fields with **one intuitive field**:

> **Camera height above the water:** `[ 15 ] ft`

- The foiler is **always at the water surface**, so the only unknown is the camera's height above it. This removes the datum choice, the sign, and the "tracker vs base" framing — the three things that made it a trap.
- Mapping: `alt_m = feet × 0.3048`, `subject_alt_m = 0.0`. Δh = `0 − alt_m = −alt_m` → `atan2(−alt_m, dist)` → correct down-tilt. (Datum-consistent: camera and subject both measured from the water = 0.)
- The **pose model is unchanged** — `alt_m`/`subject_alt_m` and the backend tilt math stay exactly as they are. We only constrain and simplify the *capture UI* to the foiler-on-water case.
- Keep the existing live preview (`CalibrateScreenV3.swift:237-243`) but show it at a **near distance where it matters**, e.g. "≈17° down @15 m" (and optionally @100 m), so a wrong entry is visible before committing.
- `commitLocation` sends `calibrateLocationManual(... altM: <feet·0.3048>, subjectAltM: 0.0)` (`WaveCamClient.swift:1505`, unchanged signature).

**Decisions flagged for review:**
1. **Units = feet** (matches "15 ft"). Stored internally as meters. (Easy to switch to meters or add a ft/m toggle.)
2. **Drop the datum picker entirely** (vs hide it behind an "advanced" disclosure). Recommendation: drop it — the foiler-on-water model is fully general for the mission; re-add later only if a non-water reference target is ever needed. YAGNI.

### Files
- `ios/WaveCam/Sources/CalibrateScreenV3.swift` — the "1 · Location + height" card: remove `datumSeaLevel` picker + `baseHeight`/dual fields; add one "Camera height above water (ft)" field; update `downTiltPreview` to a near distance; `commitLocation` → `altM = feet·0.3048, subjectAltM = 0`.
- No backend change. No `xcodegen` (no files added/removed).

### Test / verification
- **On-device (required — this is the whole point):** recalibrate with the new field (enter the real camera height), then in GPS-only at a *close* range confirm the camera now tilts **down** appropriately (≈ `atan2(−H, dist)`); confirm it steepens as the subject nears. "Done" = observed pointing down on the rig, not "it builds."
- iOS BUILD SUCCEEDED (device destination; sim is blocked).
- Sanity check the committed pose via `/status.calibration.gps_pose` (`alt_m ≈ H`, `subject_alt_m = 0`).

### Out of scope
Changing the `CameraPose.subject_alt_m` default (1.0) — the v3 flow now always sends 0; leaving the dataclass default avoids touching back-compat. The −30° mechanical down-stop (can't frame closer than ~8 m from 15 ft) is a documented physical limit, not a bug.

---

## Part B — GPS↔vision bearing-residual monitor (observe-only)

### Problem
Today vision and GPS are a **handoff**, never fused: when vision is locked, GPS is ignored entirely (`pipeline.py:683-689` only feeds the GPS cue when `arbiter_state == "gps_tracker"`, which only happens when vision is *not* locked). Before adding any fusion behavior (sanity-gate, corroboration), we must **measure** how well GPS and vision actually agree — i.e. the real cal-vs-FOV gap — so tuning is data-driven (the project's "measure before building" rule).

### Design (backend; pure observability, zero behavior change)
Each frame, when a calibrated pose + fresh GPS fix + a locked vision target all exist, compute and expose the angular disagreement between where GPS says the subject is and where vision locked:

- `gps_bearing = compute_target(base, target, pose, lead_s).bearing_deg` (already computed for pointing; reuse, don't recompute the lead).
- `cam_bearing = pose.pan_encoder_to_bearing(current_pan_enc)` (`camera_pose.py:117`).
- `vision_bearing = cam_bearing + (vision.target_xy.x − 0.5) × hfov_deg` (hfov from the live zoom/FOV curve).
- `residual_deg = normalize_180(vision_bearing − gps_bearing)`; also `residual_px ≈ (vision.target_xy.x − gps_cue.cx)` when a cue exists.

Expose in `/status` (e.g. `tracking.gps_vision_residual_deg`, plus a rolling `mean`/`max`/`n` over the session) so the live page, the iOS app, and my on-rig `/status` poll-trace all see it. Optionally append per-sample to the events ring for offline review.

**Hard constraints:** this **must not** change pointing, fusion, or the arbiter. It only reads existing state and publishes a number. It is gated on `gps_fresh ∧ pose.calibrated ∧ vision.locked` (both signals present) — null/absent otherwise.

### Files
- `orin/wavecam/wavecam/pipeline.py` — compute `residual_deg`/`residual_px` in the frame loop where both signals are available; stash on a field (e.g. `self._gps_vision_residual`).
- `orin/wavecam/wavecam/control_snapshots.py` — surface it under `tracking` in the status payload.
- (Optional) a tiny helper for the bearing math if it clarifies testing.

### Test / verification
- **Unit (TDD):** given a stub pose + a fix at a known bearing + a vision target at a known screen-x, assert `residual_deg` equals the hand-computed value; assert it's `None`/absent when GPS isn't fresh or vision isn't locked; assert it is **not** wired into any pointing/fusion path (the value is published only).
- Backend pytest + mypy gate green; deploy via `deploy.sh`; verify `/version` + `fps>0`.
- **On-rig:** with GPS live and a vision lock, confirm `/status` reports a sane `gps_vision_residual_deg` (and that pointing/fusion behavior is unchanged). This is the measurement primitive for the field run.

### Out of scope
Any *use* of the residual to alter behavior (sanity-gate, corroboration, seeded re-aim) — those are later parts, intentionally gated behind this measurement.

---

## Sequencing & risk
- Part A (iOS) and Part B (backend) are independent and ship/commit separately. Part A needs an iOS build + an on-device recal; Part B needs a backend deploy (resets `calibration_valid` — do it **before** the recal, or accept a re-validate). Suggested order on the rig: **deploy Part B first → then install Part A → then recalibrate once (survives, since no further deploys).**
- Both are low-risk: Part A only changes a capture UI and the values it sends; Part B is read-only telemetry. KILL/supervise-only/arbiter rails untouched.
