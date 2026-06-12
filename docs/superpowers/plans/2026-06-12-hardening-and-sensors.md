# Plan: Hardening & New Sensors — closing the gap to the clean-sheet design

**Date:** 2026-06-12 · **Status:** APPROVED scope, phased
**Origin:** the "what are we doing different/wrong" review (2026-06-11 evening) + Zack's
phone-on-camera and watch-on-subject observations.

**Ordering principle (binding):** guardrails → measurements → estimator upgrades
(shadow-safe) → new sensors (observe-only) → flip-dependent work. Nothing in this
plan moves the camera in a new way before the estimator flip; every new input is an
*observation* first and a *driver* never (pre-flip) — the supervise-only doctrine
extends to sensors.

**Standing guardrails (every phase):**
- Suite green + new tests per task; no rig deploys without Zack; merged via PR.
- From Phase 0 on: the mypy gate is part of "green."
- Estimator changes ship behind config flags defaulting OFF, enabled via the
  `config.local.yaml` overlay after a shadow session shows no degradation.
- Contract snapshot regeneration is allowed ONLY in a PR that deliberately adds a
  route, named in that PR's description.
- Every measured constant lands with provenance (date, method, raw numbers) in a
  comment and a pinning test — the 4.47 rule.

---

## Phase 0 — Type safety on the pointing seam (code only, no rig)

The worst bug of 2026-06-11 (missing `CameraPose.pan_encoder_to_bearing` zombied the
rig) was a type error our hand-rolled test fakes actively concealed. This phase makes
that bug class impossible *before* the phases below add more seams.

- **T0.1** `wavecam/protocols.py`: `typing.Protocol` classes for the seams —
  `PoseLike` (bearing↔encoder, lat/lon, calibrated), `PtzLike` (pan_tilt, absolute,
  zoom, inquiries, stop), `PtzStateLike` (latest, is_alive), `GpsFixLike`. Annotate
  `estimator.py`, `pointing_verifier.py`, `ptz_state.py`, `tracking_arbiter.py`,
  `control_calibration.py` parameters against them.
- **T0.2** `mypy` scoped strictly to those modules + `camera_pose.py` + `gps_geo.py`
  (`mypy.ini` with explicit file list — NOT the whole package; widening scope later
  is a separate decision). CI step after compileall.
- **T0.3** Test fakes for these seams must be declared as implementing the Protocol
  (a one-line `_: PoseLike = fake` assignment in the test makes mypy check the fake).
  Grep-audit existing fakes; fix or annotate.

**Guardrails:** scope-capped to the listed modules (drift = STOP); zero behavior
change (annotations only — suite count must not move except added type-conformance
tests). **Gate G0:** CI fails on mypy error from this PR onward.
**Effort:** one session. **Feasibility 8/10.**

## Phase 1 — Finish camera characterization (bench, rig powered, ~1 evening)

Pointing math is only as true as its weakest constant (proven at 3.2× cost). Three
constants remain unmeasured or thin.

- **T1.1 Tilt scale + range:** clamped-absolute drive to both tilt hard stops (the
  pan method, 2026-06-11), derive `tilt_enc_per_deg` + limits. Store as
  `PRISUAL_TILT_ENC_PER_DEG` with provenance; `CameraPose.tilt` paths stop treating
  0.0-uncalibrated as the permanent state.
- **T1.2 Zoom→FOV curve:** step zoom through 6–8 encoder stops (inquire_zoom at
  each), run the FOV pan-sweep per stop → full `fov_curve` replacing the single
  63.7° point. Target: color-matched object 15–20 m out (mid/tele frames must not
  overfill).
- **T1.3 Absolute-move dynamics:** scripted slews (50/200/600/1500/3000 counts),
  trajectory-log each (the existing settle-until-stable harness): settle time vs
  distance, overshoot curve, hunt amplitude. Output: a constants table feeding
  verify-and-resend tolerances now and the Phase-5 servo later.
- **T1.4 Plumb real `zoom_enc`:** PtzState gains a slow zoom poll (1–2 Hz —
  `inquire_zoom` exists, unused); shadow tick passes it instead of the hardcoded 0;
  estimator's zoom-dependent covariance becomes real.

**Guardrails:** bench scripts reuse the leash + return-to-anchor pattern; service
manual-held or stopped during runs; KILL reachable (Zack present); every constant
lands with a pinning test. **Gate G-CHAR:** T1.1–T1.3 measured before Phase 2 merges
(the range model in Phase 2 consumes the zoom curve).
**Effort:** one bench evening + small PRs. **Feasibility 9/10.**

## Phase 2 — Vision range observation (estimator upgrade, shadow-safe)

Bearing-only vision leaves range entirely to GPS. The subject is a known-size
object: person-bbox height + FOV ⇒ distance. Noisy, but useful precisely when GPS
goes quiet — and free.

- **T2.1** `estimator.update_vision_range(bbox_h_px, frame_h, zoom_enc, now)`:
  `range_m ≈ subject_height_m / (2·tan(vfov(zoom)·(bbox_h/frame_h)/2))` with
  `estimator.subject_height_m` (default 1.0 — torso-on-board stance, tunable) and a
  measurement variance scaled by bbox jitter and zoom (R from
  `estimator.r_range_frac`, default 0.3·range — wide, honest).
- **T2.2** Pipeline shadow tick wires it for **person boxes only** (`has_person`;
  never the color blob — blob size is lighting-dependent).
- **T2.3** Sim harness scenario: same trajectory scored with range-obs on vs off;
  JSONL gains `range_obs_m` / `range_r` fields for field comparison.

**Guardrails:** flag `estimator.use_vision_range` default **false**; enable via
overlay only after one shadow session shows divergence stats ≤ the no-range
baseline. Shadow-only — the flip criterion is unchanged. **Gate G2-R:** the on/off
sim comparison is in the PR description with numbers.
**Effort:** ~1 day. **Feasibility 7/10** *(Unvalidated until the noise model meets
real surf bobbing; confidence 0.7).*

## Phase 3 — Phone-on-camera as a sensor (observe-only)

The MagSafe-mounted iPhone is a GPS + compass + IMU package rigidly attached to the
camera. This attacks the heading-anchor fragility (the one stored value the math
can't self-check) — *if* the magnetics cooperate.

- **T3.0 RESOLVED 2026-06-12 (by inspection + research): Outcome B.** The phone
  mounts on the static plate (photos confirm; it is also the operator console).
  Research verdicts (primary sources, workflow 2026-06-12):
  - iOS heading calibration filters "only those magnetic fields that move with the
    device" (Apple, CLLocationManagerDelegate docs) → the MagSafe ring (phone-fixed)
    is calibratable hard iron; the PTZ motors (moving relative to the phone) are
    not — pan-angle-correlated error is expected. Fine for **detection**, never
    correction. `headingAccuracy < 0` = invalid; the
    `shouldDisplayHeadingCalibration` callback doubles as a free interference alarm.
  - **Permanent head-mounting the phone is REJECTED**: no conference-class PTZ
    vendor supports ANY head payload (manuals: "Do not turn the camera head by
    hand… may result in mechanical damage"); ~220 g at 5–8 cm roughly DOUBLES
    pan-axis inertia (estimate; no vendor torque specs exist); and the class uses
    **open-loop steppers** — a stalled/missed step is a SILENT position error.
  - **⚠ Open-loop implication for the whole stack:** our `pan_enc` may be
    commanded-step dead reckoning, not measured truth. A missed step (gust, snag,
    bump) silently shifts the anchor until re-home. This RAISES the value of
    drift detection here and motivates a periodic home-reindex strategy (track as
    a Phase-5 servo consideration + verify on the bench: stall the head gently
    by hand at low speed and see whether inquiry counts diverge from reality).
- **T3.4 Anchor Ritual (NEW — replaces manual heading capture):** a transient
  head-mount for a 60–90 s guided setup sweep. **Prefer the WATCH** (30–45 g near
  the axis ≈ 1–3 % inertia — low-risk gray zone vs the phone's ~2×; Ultra has a
  compass; WaveCamWatch exists): small printed clip/ring on the head side; app
  drives the pan through 8–12 stops at low speed; sample `CLHeading` only while
  stationary at each stop (coil fields de-energized); least-squares fit
  heading = anchor + bias; **self-validating** — residual structure > ~3° ⇒
  reject and fall back to manual capture. Requires an active workout session for
  background sensor flow (Apple-documented) or simply foreground the watch app
  during the ritual. Phone-on-side-ring is the fallback dock if watch sampling
  disappoints (same ritual, slower speeds, accepted transient load).
  - Gate **G-PH** now means: first ritual's fit residuals documented here before
    any heading observation feeds the estimator (T3.3).
- **T3.1 iOS publisher:** while the app is foregrounded on Live, POST
  `/api/v1/sensors/phone` at 1–2 Hz: `{heading_deg, heading_acc, lat, lon, h_acc,
  bump}` — `bump` = accelerometer spike above threshold (tripod knock). Piggybacks
  the existing poll loop; location-while-using permission only.
- **T3.2 Backend SensorHub:** new route (deliberate contract addition) → cached
  snapshot (the GPS-reader pattern: background-thread-free, lock-guarded). Consumers:
  (a) heading-drift monitor — phone-heading vs pose-anchor disagreement beyond
  threshold ⇒ `anchor_suspect` event + amber **RE-CAPTURE HEADING** chip in the app;
  (b) base-position cross-check — phone GPS vs base Wio > 30 m ⇒ health warning.
- **T3.3 (Outcome A only, post-G-PH):** feed bias-corrected phone heading to the
  estimator as a low-trust heading observation (R from the measured σ, floored
  at 3°).

**Guardrails:** magnetometer data is *low-trust by decree* (MagSafe + motor fields):
it may **alert** from day one but may not **correct** anything until the T3.0 numbers
justify it; nothing here drives pointing pre-flip; all flagged
(`sensors.phone_enabled` default false); snapshot regen sanctioned in the T3.2 PR
only. **Effort:** experiment 10 min; T3.1–T3.2 ~1 day. **Feasibility 8/10 for
detection, 5/10 for heading-correction (magnetics unknown until measured).**

## Phase 4 — Watch as offline validator + backup position (lowest risk, anytime)

The watch rides the subject with exactly the sensors the estimator wants to be
judged against. Its connectivity is the historically unreliable part — so use it
where connectivity doesn't matter.

- **T4.1 WaveCamWatch recorder:** HealthKit workout session capturing GPS track +
  CoreMotion (accel/gyro/heading) at max sustainable rates to a local file;
  share-sheet export post-session.
- **T4.2 Replay scorer:** offline tool aligning the watch track (GPS-time-synced)
  against the rig's shadow JSONL — per-second position error of the estimator vs an
  independent track. This becomes **the estimator's report card** and feeds the flip
  review with evidence no amount of rig-side logging can fake.
- **T4.3 (later, optional):** opportunistic LTE position POST when the watch has
  signal — a third position source with seconds of latency, redundancy only, never
  primary.

**Guardrails:** the watch is never in the control path; the scorer is offline-only;
T4.3 deferred until T4.2 proves the data is worth the plumbing.
**Effort:** T4.1–T4.2 ~1–2 days. **Feasibility 8/10** (workout APIs are
well-trodden; time-sync is the only fiddly part).

## Phase 5 — Post-flip (gated on the estimator flip itself)

- **T5.1 Heading self-calibration:** the persistent GPS-vs-vision bearing residual
  *is* the anchor error once both observe the same subject. Alert-first (corroborates
  T3.2's monitor); auto-correct only after N≥3 sessions of agreement on the sign and
  size. Supersedes manual recapture eventually; the manual step stays in the
  checklist until then.
- **T5.2 Unified velocity servo:** one FOV-aware rate controller pointing at the
  estimate (gains in deg/s scheduled by the Phase-1 zoom curve; feed-forward from
  estimated velocity; the Phase-1 absolute-dynamics table bounds the step responses).
  Retires the vision-steps/absolute-jumps duality and, with it, the arbiter.

**Guardrails:** both items require the flip review (G3: ≥2 shadow sessions + the
T4.2 report card) to have happened. Until then they are design notes, not work.

---

## Sequence rationale

Phase 0 protects every later phase at commit time. Phase 1 must precede Phase 2
because the range model consumes the zoom curve, and precede Phase 5 because the
servo consumes the dynamics table. Phases 2–4 are mutually independent and can
interleave with field sessions; none of them changes camera behavior, so none of
them can cost a filming day. Phase 5 exists only on the other side of the flip
evidence that Phases 2–4 strengthen.
