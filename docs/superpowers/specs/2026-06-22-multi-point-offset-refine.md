# Multi-point offset refine — optional, repeatable calibration tuning

- **Created:** 2026-06-22
- **Status:** SPEC (not built) — design only
- **Lane:** Backend (Claude primary) + iOS
- **Why:** A single offset aim carries the full per-aim GPS-bearing error (large at close range — a 23 m aim has ~tens of degrees of geometric error). The operator wants to *keep improving* accuracy by aiming at the tracker from several positions/distances, each refining the calibration. Never required — a strict enhancement over the one-shot offset.

## Goal / done-when

An **optional, repeatable** refine flow: the operator aims the camera at the tracker (any bearing/distance), captures, and each capture adds a sample to a least-squares fit of the pan (and tilt) anchor. The displayed RMS residual drops as samples accumulate across varied geometry. Done-when: from ≥2 aims at different bearings, the fitted pan anchor is provably closer to truth than any single aim, the residual is shown, and the result persists like today's offset. The single-aim and manual-heading paths are unchanged and still sufficient on their own.

## Background / current behavior

`offset_calibrate` ([control_calibration.py:645](../../../orin/wavecam/wavecam/control_calibration.py)) is a **single** aim: it reads the live `(pan_enc, tilt_enc)`, derives bearing+distance from base→tracker GPS, and **replaces** the pan anchor (`calibrate_pan_aim`) and tilt anchor — `pan_enc_per_deg`/`tilt_enc_per_deg` fixed at the **measured 14.4** (`PRISUAL_PAN_ENC_PER_DEG`, hard-stop-calibrated; see the 4.47-rule gotcha). Related: the planned vision-assisted "walk-in-color → least-squares pan→north fit" ([[vision-gps-walk-heading-calibration]]) — this is the operator-driven manual version of the same idea.

## Design

### Backend — sample buffer + fit
- **Sample:** on each refine capture, store `{pan_enc, bearing_deg, tilt_enc, elev_deg, distance_m, ts}` where `bearing_deg`/`elev_deg` come from base→tracker GPS (the same resolution `offset_calibrate` already does, incl. the live-fix fallback).
- **Pan fit (scale FIXED at 14.4):** for each sample, `anchor_i = pan_enc_i − bearing_i · s` (s = 14.4). Fitted `pan_anchor = circular-mean(anchor_i)` — averaging cancels per-aim GPS error. **Keep s fixed by default** — the hard-stop scale is authoritative; fitting scale from noisy close-range GPS would re-introduce the 4.47-class error. (Optional advanced toggle: regress `pan_enc` vs `bearing` for a *bounded* scale correction only when ≥3 well-separated bearings — off by default.)
- **Tilt fit:** average `tilt_anchor_elev`/`enc` across samples (tilt scale fixed too). Tilt is less GPS-sensitive (depends on the operator height datum), so this mostly de-noises.
- **Residual:** per-sample `residual_deg_i = (pan_enc_i − (anchor + bearing_i·s)) / s` and report **RMS residual (deg)** + worst sample, so the operator sees accuracy improve and can spot a bad aim.
- **Wrap handling:** bearings near 0/360 must be unwrapped relative to the running anchor before the mean (pan is modular).
- **Outliers:** allow "discard last sample"; optionally flag samples with residual > threshold rather than auto-dropping (operator is the authority).
- **Persistence:** fitted anchors persist via `calibration_store` → `camera_pose.json`, exactly like the single-aim offset (`_persist_step`).

### API
Extend `POST /api/v1/calibration/offset` with `mode: "replace" | "accumulate"`:
- `replace` (default) = today's single-aim behavior (back-compat).
- `accumulate` = append a sample + refit; response adds `sample_count`, `rms_residual_deg`, `worst_residual_deg`, and the new `offset_deg`.
- `POST /api/v1/calibration/offset/reset` clears the sample buffer (start over).
Owner gating mirrors `offset_calibrate` (operator-aimed; works at owner∈{calibrate, manual} per the COR2 sequence fix).

### iOS
In the calibrate aim step (or a dedicated "Refine" disclosure): after the first offset, show **"Refine (optional) — aim from a new spot, then Capture"**. Each capture posts `mode:accumulate` and shows **`samples: N · residual: X.X°`**. Buttons: `Refine` (repeatable), `Discard last`, `Reset`, `Done`. Aiming reuses the Live tab (per the [2026-06-22 aim-on-Live design](#)) — feed + zoom + joystick. Never blocks; the operator can stop after 1.

## Test / verification
- Unit (no rig): synthetic samples with a known anchor + injected per-aim noise at varied bearings → fitted anchor within tolerance of truth; **RMS residual strictly decreases** as samples are added; wrap-around bearings (350°, 10°) fit correctly; "discard last" reverts; scale stays 14.4 with the default (fixed-scale) fit.
- Integration: extend `test_calibrate_e2e_pointing.py` — 3 accumulate aims → tighter pan anchor than the single aim → arbiter still selects `gps_tracker`.

## Risks / out of scope
- **Do NOT let GPS noise move the measured 14.4 scale** by default — fixed-scale fit only; scale correction is an explicit, bounded, ≥3-sample opt-in.
- Optional + additive: must not change the single-aim/manual-heading/validate/confirm paths or the KILL/owner rails.
- Not auto-collecting (that's the separate vision-assisted walk-calibration); this is operator-driven, one aim per tap.

## Open questions — RESOLVED 2026-06-22
1. Scale correction → **fixed-only** (the 14.4 is hard-stop-measured; don't let GPS noise move it). No scale-regression toggle in v1.
2. Sample cap → **keep all session samples** (a Reset clears). Soft cap deferred.
3. UI home → **inline in the aim step** (keeps the single-screen flow; no separate tab).
4. Outliers → **reset-only** in v1 (discard-last deferred); the residual readout surfaces a bad aim.

## v1 delivery — approved UX design (2026-06-22)

**Aiming = the Live tab (full feed + 20× zoom + joystick), not an embedded view.** The
calibrate aim step (step 3) drops the embedded viewless joystick. To aim, the operator
opens the **Live** tab (the best surface for spotting a distant tracker), frames it,
returns to Calibrate, and captures. While a calibrate session is active, the Live tab
shows a compact banner — *"CALIBRATE — aim at the tracker, then return to Calibrate to
Capture"* — read from `status.calibration.active` (no tab-selection plumbing; guidance-only
in v1, auto-switch is a trivial follow-up).

**Aim-step controls (the core interaction):**
- **Capture offset** — single baseline aim (`mode=replace`; clears refine samples). First aim or "start over."
- **Refine +1** — adds the current aim as a sample (`mode=accumulate`); aim again from a different spot/distance to tighten. Shows **"samples N · residual X.X°"**.
- **Reset refine** — clears samples.

**Backend zoom-takeover (TDD):** mirror the COR2 velocity fix on `/api/v1/ptz/zoom` — when
`owner==calibrate` and `takeover`, route through `claim_manual_from_calibrate` so zoom works
during calibrate (today refused); KILL still blocks; releases back to calibrate. Endpoint
test + API-contract snapshot update.

**iOS client:** `calibrateOffset` gains a `mode` param; add `calibrateOffsetReset()`.

**Out of scope (still required to prove sufficiency, tracked separately):** tab auto-switch,
discard-last, and — critically — **lead/latency tuning + a framing success metric + a
measurement run**. This UI *enables* refinement; a field measurement confirms it actually
closes the ~3° FOV gap at range.

**Follow-up (separate pass):** calibrate-tab density/simplification (fewer words, smaller
buttons, free space) via swiftui-expert + frontend-design + anti-vibe, done after this
rework so the final layout is what gets simplified.
