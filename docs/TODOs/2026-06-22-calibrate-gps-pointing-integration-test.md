# End-to-end calibrate → GPS-pointing integration test

- **Created:** 2026-06-22
- **Status:** PLANNED
- **Owner / lane:** Claude (backend tests — Claude primary; no rig needed)
- **Severity / why:** HIGH — two shipped bugs (COR2 dead-aim, TECH5 tilt-bias) slipped past 600 unit tests because *nothing* exercises the full calibrate→pointing happy path. The next integration-level regression will ship silently too.

## Problem

Unit tests cover the pieces in isolation — `gps_pointing`, `calibration_alt`, the endpoint owner gates — but no test walks the whole chain:

`session/start → location(manual, subject_alt_m) → heading-lock → aim(ptz/velocity takeover) → release → offset → validation → validation/confirm → calibration_valid=True → arbiter selects owner=gps_tracker → pipeline emits a pan/tilt command`.

Both bugs found 2026-06-22 lived in the *seams* between steps, not in any one unit:
- **COR2** — the aim takeover left `owner=manual`, so `_require_active()` ([control_calibration.py:219](../../orin/wavecam/wavecam/control_calibration.py)) refused every subsequent step with `calibrate_owner_lost`, and `exit_session` stranded `owner=manual`.
- **TECH5** — `offset_calibrate` anchored tilt at a hardcoded `subject_alt_m=1.0` instead of `pose.subject_alt_m`, biasing every commanded tilt.

The sequence regression tests added with those fixes (`test_calibrate_aim_release_restores_calibrate_so_capture_continues`, `test_calibrate_exit_releases_stranded_manual_owner` in `tests/test_control_api.py`) cover the *ownership transitions* but stop short of the full happy path through to an actual `gps_tracker` pointing command. See [[calibration-field-lessons-20260621]].

## Goal / done-when

One integration test (FastAPI `TestClient` + stubbed GPS/encoder, **no live rig**) that walks the entire happy path and asserts:
- each step returns 200 / `ok:true`;
- owner transitions are correct (`calibrate → manual` on aim, `→ calibrate` on release, `→ idle/prior` on exit);
- `calibration_valid` is True + banner `VALID` after confirm;
- given a viable GPS fix, `arbiter.decide()` selects `owner=gps_tracker` (not idle) and `pipeline._gps_pointing_cmd` produces a `tilt_enc` consistent with the operator's `subject_alt_m`;
- a `subject_alt_m ≠ 1` variant changes the commanded tilt accordingly (locks the TECH5 class).

## Plan

1. **Harness:** extend `DummyPipeline` in `tests/test_control_api.py` (or a local one) so `offset_calibrate` has a fresh encoder (`_current_encoder` via `ptz_state.latest()` / `inquire_pan_tilt`) and a stub GPS (`get_fix()` / `get_camera_position()`) so location-bearing + offset resolve. Reuse the `make_client` pattern.
2. **Happy path:** new `tests/test_calibrate_e2e_pointing.py` — walk start → location (`method=map_manual`, `alt_m`, `subject_alt_m=-1`) → heading-lock (`operator_accepted=true`) → `ptz/velocity{takeover}` → `ptz/stop{hold:false}` → `calibration/offset{operator_accepted, target_lat/lon}` → validation → validation/confirm. Assert status + owner at each step + `calibration_valid` + banner.
3. **Drive the arbiter:** with a viable fix (`gps_fresh ∧ gps_calibrated ∧ base_locked ∧ calibration_valid`) + `tracking.enabled` + mode `auto`, assert `arbiter.decide()` → `gps_tracker`, and `_gps_pointing_cmd` `tilt_enc` matches `atan2(subject_alt_m − alt_m, dist)` (guards TECH5).
4. **TECH5 variant:** same flow with a different `subject_alt_m`; assert the commanded tilt changes.

## Test / verification

`cd orin/wavecam && python3 -m pytest tests/test_calibrate_e2e_pointing.py -q && python3 -m mypy` green. The file must fail if any sequence link breaks (the COR2/TECH5 class).

## Risks / out of scope

- Must run **fully stubbed** — no live rig dependency. Keep the GPS/encoder stubs faithful to the real contracts or the test gives false confidence.
- Does **not** cover the iOS side (the `onStop` release choice) — that's verified by build + on-device. Note the iOS test target is currently unrunnable locally (watch `AppIcon` simulator `actool` quirk) — a separate gap worth its own item.

## Worklog

- _2026-06-22_ — created. Motivated by the COR2/TECH5 sequence bugs found while verifying the v3 calibrate workflow; the fixes landed with transition-level regression tests but no full-path integration test.
