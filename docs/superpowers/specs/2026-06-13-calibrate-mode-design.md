# CALIBRATE Mode — Heading + Location Lock (design spec v3, final after 3-peer review)

## Reframe (drives everything)
GPS pointing is **coarse** — its job is to put the subject **inside the FOV** so vision takes over, NOT to frame tightly. Target heading accuracy: **~1–2° is sufficient for the hand-off**; the realistic *achievable* floor with non-RTK GNSS is **~0.5–1°** (landmark/averaging) — comfortably good enough. **Sub-degree is neither required nor reliably achievable. Don't claim it.**

## Goal
Establish camera **location** + **heading** (pan-encoder ↔ true bearing), no camera magnetometer, with the autonomous tracker locked out, to ~1–2° heading + a stated location error radius, with an **honest error budget, refusal model, and post-cal validation**.

---

## 1. Mode gating (validated)
CALIBRATE fully suppresses the arbiter; a calibration controller owns PTZ; arbiter can't reclaim. Define the **takeover/displace path** for entering CALIBRATE mid-move. **KILL always works**; restore prior mode on exit/KILL; test restart-state consistency.
**Persistence:** calibration is **session-scoped** — auto-invalidate on service restart, power-cycle, or base-position change (the Wio config itself reverts on cold boot). Show a **visible "calibration age + VALID/INVALID" banner**.

## 2. LOCATION lock
- Average base-Wio fixes (median/trimmed ENU, outlier reject, gate sats/HDOP/fix-age, exclude the **warm-up ~first 60 s**). **Averaging improves precision, not absolute accuracy** (beach multipath / USB-RF bias is NOT removed).
- **Report the error radius from a model (HDOP × UERE), NOT the sample std-dev** — consecutive fixes from a stationary receiver are correlated, so std-dev reads ~0.2 m while true error is 2–5 m. The radius is an **estimate, never a bound.**
- **Lever arm:** camera origin = pan axis / lens entrance pupil; subtract the base-antenna→origin offset.
- **Overrides/cross-check:** base-Wio vs **iPhone GPS** with a realistic difference gate (warn = wrong-site/multipath); allow a **manual map-pin / pre-surveyed tripod mark**.
- **Movement guard:** drive it from **base-Wio position drift + vision background-feature shift** (both rigidly tied to the camera) — NOT the iPhone IMU unless the phone is rigidly mounted. (iPhone IMU is fine for measuring static **tilt** via gravity — see §3.)

## 3. HEADING reference (heavily revised)

**Primary = SURVEYED LANDMARK** (Opus's call, fits solo deployment):
- Pre-store 1–3 fixed objects per launch site with known lat/lon (charted buoy, jetty, pier piling, channel marker, headland). Bearing = great-circle between two **known** coordinates → **zero remote-GPS error, perfectly stationary, fully SOLO, repeatable session-to-session.** Operator (or vision) centers the landmark; capture pairs the pan-encoder with the computed bearing.
- This eliminates the two largest error sources at once (moving-target latency + un-averaged remote fix) and matches reality: **Zack sets up alone, then surfs — no assistant to stand 150 m offshore.**

**Fallback = STATIONARY remote/assistant** (when no good landmark):
- Assistant stands **still** holding the cue at **~50–100 m** (close enough that vision resolves reliably) and the system **averages the stationary remote fix over 30–60 s** to beat down its (dominant, un-averaged) GPS error.

**Emergency only = moving subject** — and even with timestamp-matching it injects multi-degree error (vision centroid and GPS fix are sampled at different instants; the target translates between them — **timestamp-matching cannot fix this**). Only with a strict near-zero-angular-velocity gate (`|d(bearing)/dt| ≈ 0`).

**Points & fit — corrected (Opus):**
- Scale is **FIXED at 14.4**, so the fit has **exactly one free parameter (offset)**. **One well-placed stationary point with many averaged samples determines it.** → **DROP the ≥30–45° "separation for conditioning" requirement** — that conflates the float-scale case with ours and would force ~115 m of pointless subject travel. Keep a **2nd separated point ONLY as a tilt/scale CONSISTENCY check** (a large residual there flags a tilted pan axis or backlash).
- Robust average + outlier rejection over the samples; correct **0/360 wrap** handling; validate encoder zero + VISCA reported-position semantics. Don't float scale in the field fit.

**Must-haves before a capture counts:**
- **Pan-axis LEVEL:** measure roll/pitch (iPhone IMU on the tripod, or bubble level); **refuse/compensate above ~0.5°** (a 2° tilt makes azimuth a pan-angle-dependent sinusoid a single offset can't represent). Re-check level in the movement guard.
- **Servo-settle + dwell:** read the encoder only after velocity ≈ 0 and the centroid is in-tolerance for N frames (no first-touch trigger). **Backlash:** approach each point from a **consistent pan direction.**
- **Vision = aim-aid only, human-in-the-loop:** frozen **candidate preview** (image, bbox, GPS age, residual contribution) → operator **taps to accept**; can **delete** any sample. No silent auto-capture.
- **Auto-center safety:** enforce pan/tilt limits + max rate; **sun-avoidance** (never slew the lens onto the sun); **abort on LoRa loss / stale remote fix.**
- **Lever arm (remote):** remote antenna ≠ cue/body center (~0.3–0.5 m) — budget it.

**Acceptance = honest error budget + score (not just RMS):**
- Combine in quadrature: base+remote GNSS (remote per-fix ~2.5–5 m, floors at ~1–2 m multipath even averaged), vision angular error (**pixel tol × current zoom/FOV** — capture only above a min zoom, below a max pan rate), timestamp latency, lever arms, tilt residual. Realistic per-point ~1–2°; landmark or 30–60 s-averaged-stationary gets toward ~0.5–1°.
- **Refuse** if estimated uncertainty > budget even when RMS is low. Report **heading confidence**, not just "done."

## 4. iPhone compass = coarse SEED only
±10° to point roughly for first acquisition / solo, with a magnetic-interference caveat. Never final.

## 5. Continuous refinement — CUT as written
The subject is moving during filming, so an EMA of `(gps_bearing − enc/14.4)` re-injects the latency-coupling bias. Keep ONLY a **step-change detector** (flag "tripod moved → re-calibrate") gated on near-zero subject angular velocity + tight GPS age. No online offset auto-update.

## 6. Post-calibration VALIDATION (required)
After the fit, **sight an independent check point** (a second landmark, or the stationary remote at a fresh spot), compare predicted vs actual bearing, show the operator the miss, and **require confirmation before returning to auto.** Best guard against a confidently-wrong calibration.

## 7. Operator UX
CALIBRATE → arbiter off → **Lock position** (average + model radius + cross-check + map-pin override) → **Level check** (refuse if tilt high) → **Capture heading** (pick a surveyed landmark, or guide a static assistant; aim-aid + settle + preview + tap-accept; live budget/confidence + plain-language refuse reasons) → **Validate** (independent check point) → **Confirm & exit** → auto. VALID/age banner + KILL always visible.

## Explicit non-goals
No camera magnetometer; no iPhone compass as final heading; no short non-RTK two-GPS baseline; no remote-IMU as camera heading; no floating the 14.4 scale; no sub-degree claim; no silent auto-capture; no online offset auto-update; no ≥45° separation requirement (artifact of the float-scale case).

## Open decisions for Zack
1. **Solo vs assistant** at setup? (If solo is the norm — likely — **landmark is THE primary method** and the assistant path is rarely used.)
2. **Per-site landmark survey** acceptable? (one-time: mark 1–3 objects' coords per home spot → every future setup is tap-per-landmark.)
3. **Location source:** base-Wio-averaged + iPhone cross-check, vs iPhone-primary + map-pin (settle with one field comparison).
4. Does the LoRa packet carry **GPS timestamp + HDOP**? (needed for latency-matching + the model radius.)
5. Confirm the **~1–2° "in-FOV-for-handoff" accuracy target** (vs chasing tighter → needs RTK).
