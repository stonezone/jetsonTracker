"""Phase 3b wiring tests: Pipeline._gps_cue — bearing-projected cue when enabled +
inputs present, legacy frame-center cue (byte-identical) when off or inputs missing,
None when the target is off-frame. Drives the method directly with stubs.
"""
from __future__ import annotations
import sys
import types

sys.modules.setdefault("cv2", types.SimpleNamespace())

from wavecam.camera_pose import CameraPose
from wavecam.pipeline import Pipeline


def _pipe(enabled, fov_curve=None, fix=(2.0, 1.0)):
    p = Pipeline.__new__(Pipeline)
    p.cfg = types.SimpleNamespace(fusion=types.SimpleNamespace(
        gps_boost_radius_frac=0.25,
        gps_bearing_cue_enabled=enabled,
        gps_bearing_cue_uncertainty_deg=5.0,
        gps_bearing_cue_max_offscreen_deg=10.0,
    ))
    pose = CameraPose(lat=1.0, lon=1.0)
    pose.calibrate_pan_aim(enc=0.0, bearing_deg=0.0, enc_per_deg=14.4)  # enc 0 -> bearing 0
    p.pose = pose
    p.gps = types.SimpleNamespace(get_fix=lambda: types.SimpleNamespace(lat=fix[0], lon=fix[1]))
    p._store = types.SimpleNamespace(
        fov_curve=[(0, 60.0), (16384, 5.0)] if fov_curve is None else fov_curve)
    p.ptz_state = types.SimpleNamespace(
        latest=lambda: ((0, 0), 0.0),       # pan enc 0 -> current bearing 0 (north)
        latest_zoom=lambda: (0, 0.0))       # widest -> hfov 60
    p._last_gps_cue = None
    return p


W, H = 640, 480
CENTER = (W / 2.0, H / 2.0, 0.25 * min(W, H))  # (320, 240, 120)


def test_disabled_returns_center_cue():
    cue = _pipe(enabled=False)._gps_cue(W, H)
    assert cue == CENTER  # byte-identical legacy frame-center cue


def test_missing_fov_curve_falls_back_to_center():
    cue = _pipe(enabled=True, fov_curve=[])._gps_cue(W, H)
    assert cue == CENTER


def test_north_target_centers_cue():
    # base (1,1) -> target due north (2,1): bearing ~0, current 0 -> error ~0
    cue = _pipe(enabled=True, fix=(2.0, 1.0))._gps_cue(W, H)
    assert cue is not None
    assert abs(cue[0] - W / 2.0) < 2.0  # cx ~ center
    assert cue[2] < CENTER[2]           # bearing-cue radius (uncertainty-scaled) < center radius


def test_east_of_aim_shifts_cue_right():
    # target north-east of base -> bearing a few deg east -> cue shifts right
    cue = _pipe(enabled=True, fix=(2.0, 1.1))._gps_cue(W, H)
    assert cue is not None
    assert cue[0] > W / 2.0


def test_offscreen_target_returns_none():
    # target due east (1,2): bearing ~90, current 0 -> error ~90 > hfov/2+tol -> no cue
    cue = _pipe(enabled=True, fix=(1.0, 2.0))._gps_cue(W, H)
    assert cue is None


if __name__ == "__main__":
    test_disabled_returns_center_cue()
    test_missing_fov_curve_falls_back_to_center()
    test_north_target_centers_cue()
    test_east_of_aim_shifts_cue_right()
    test_offscreen_target_returns_none()
    print("GPS BEARING CUE WIRING TESTS PASSED")
