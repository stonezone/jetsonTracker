# Persist (or safely restore) calibration_valid across a service restart

- **Created:** 2026-06-22
- **Status:** PLANNED
- **Owner / lane:** Claude (backend — Claude primary; rig verify needs a deploy)
- **Severity / why:** MEDIUM — every `wavecam.service` restart resets session-scoped `calibration_valid`, silently destroying the operator's hard-won VALID mid-field-test. Recurring friction across multiple sessions.

## Problem

`calibration_valid` lives in the in-memory `self._session` dict ([control_calibration.py](../../orin/wavecam/wavecam/control_calibration.py)) and is reset on every restart. A `deploy.sh` during field calibration drops VALID → GPS tracking stops (the `gps_viable` chain needs `calibration_valid`) until the operator re-validates — often without realizing why the camera stopped pointing. This has bitten repeatedly (see [[calibration-field-lessons-20260621]], the "session-scoped" gotcha).

Key nuance: the **pose** (location + heading + tilt anchors) already persists via `calibration_store` → `camera_pose.json`, and `confirm_validation` even persists the validation record via `_persist_step("validation", …)` ([control_calibration.py:849](../../orin/wavecam/wavecam/control_calibration.py)). So the data is on disk — only the live `valid` flag is dropped on reload.

## Goal / done-when

After a `wavecam.service` restart with an intact persisted calibration, the rig **either**:
- **(a)** restores `calibration_valid=True` from disk — *but only if the pose is unchanged since the validation* (don't auto-trust a stale validation); **or**
- **(b)** exposes a clear "calibration present but unvalidated since restart — re-confirm" state in `/status.authority` + the iOS banner, **and** `deploy.sh` warns before restarting when a calibration session is active.

Decide (a) vs (b). (a) is better field UX; (b) is strictly safe. Likely: (a) guarded by a pose-match check, with (b)'s deploy warning as a backstop.

## Plan

1. **Confirm what's on disk:** trace `calibration_store` load on startup — is the confirmed `validation` record read back, and is `valid` derivable from it? (`confirm_validation` writes it; check the loader.)
2. **Option A:** on startup, if the store has a confirmed validation **and** the loaded pose anchors match the validation's pose (compare a stored pose fingerprint / timestamp — invalidate if the pose was re-locked after the validation), set `self._session` `valid=True` + banner `VALID`.
3. **Option B / backstop:** `deploy.sh` — before restart, `curl /status` to a file; if `session.mode==calibrate` or `authority.calibration_valid==true`, print a loud warning and require an explicit flag to proceed.
4. Surface "valid (restored)" vs "needs re-confirm" in `/status.authority` + the iOS Calibrate banner.

## Test / verification

Unit: persist a confirmed validation, simulate restart (reload the store), assert `calibration_valid` is **restored when the pose is unchanged** and **dropped when the pose changed after validation**. Rig: `deploy.sh`, then `GET /api/v1/status` → `authority.calibration_valid` survives (option A) or the deploy warns (option B).

## Risks / out of scope

- **Must NOT** auto-restore VALID if the pose changed since validation — that would re-enable GPS pointing on a stale calibration (dangerous, points the camera wrong). The pose-match guard is the safety crux.
- KILL / supervise-only rails unchanged. Don't make `valid` so sticky that a genuinely-bad calibration can't be cleared (re-locking location/heading must invalidate it).

## Worklog

- _2026-06-22_ — created. Motivated by the deploy this session (`0dd4669`) resetting `calibration_valid` again, and the repeated field friction it causes.
