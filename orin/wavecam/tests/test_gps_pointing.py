"""Unit tests for gps_pointing (pure pointing-target computation). No hardware."""
import pytest

from wavecam.camera_pose import CameraPose
from wavecam.gps_geo import GeoPoint, haversine_m
from wavecam.gps_pointing import ZoomCurve, compute_target, distance_to_zoom_encoder


CURVE = ZoomCurve(near_m=40.0, far_m=250.0, max_enc=16384.0, max_frac=0.85)


def test_zoom_curve_edges_and_clamp():
    assert distance_to_zoom_encoder(40.0, CURVE) == 0.0                  # near -> wide
    assert abs(distance_to_zoom_encoder(250.0, CURVE) - 0.85 * 16384) < 1e-6   # far -> tele cap
    assert abs(distance_to_zoom_encoder(145.0, CURVE) - 0.5 * 0.85 * 16384) < 1e-6  # midpoint
    assert distance_to_zoom_encoder(20.0, CURVE) == 0.0                  # below near -> clamp 0
    assert abs(distance_to_zoom_encoder(400.0, CURVE) - 0.85 * 16384) < 1e-6   # beyond far -> clamp


def _pose():
    p = CameraPose(lat=21.6, lon=-158.0, alt_m=2.0)
    p.calibrate_pan_aim(enc=1000.0, bearing_deg=90.0, enc_per_deg=4.47)  # bearing 90 -> enc 1000
    return p


def test_compute_target_full_chain():
    base = GeoPoint(lat=21.6, lon=-158.0, alt_m=2.0)
    target = GeoPoint(lat=21.6, lon=-157.9990, alt_m=2.0)               # due east, ~level
    t = compute_target(base, target, _pose(), lead_s=0.0, zoom=CURVE)
    assert abs(t.bearing_deg - 90.0) < 1.0                              # east
    assert abs(t.distance_m - haversine_m(21.6, -158.0, 21.6, -157.999)) < 1e-6
    assert abs(t.pan_enc - 1000.0) < 5.0                               # bearing 90 -> ~anchor enc
    assert t.tilt_enc == 0.0                                            # level + uncalibrated tilt
    assert t.zoom_enc == distance_to_zoom_encoder(t.distance_m, CURVE)


def test_zoom_none_leaves_zoom_unset():
    base = GeoPoint(lat=21.6, lon=-158.0)
    target = GeoPoint(lat=21.6, lon=-157.999)
    assert compute_target(base, target, _pose(), lead_s=0.0, zoom=None).zoom_enc is None


def test_lead_shifts_the_aim():
    base = GeoPoint(lat=21.6, lon=-158.0)
    moving = GeoPoint(lat=21.6, lon=-157.999, speed_mps=10.0, course_deg=0.0)  # heading north
    still = compute_target(base, moving, _pose(), lead_s=0.0, zoom=None)
    led = compute_target(base, moving, _pose(), lead_s=2.0, zoom=None)         # ~20 m north
    assert abs(led.bearing_deg - still.bearing_deg) > 0.5               # aim moved
    assert led.distance_m != still.distance_m


def test_uncalibrated_pose_raises():
    base = GeoPoint(lat=21.6, lon=-158.0)
    target = GeoPoint(lat=21.6, lon=-157.999)
    with pytest.raises(RuntimeError):
        compute_target(base, target, CameraPose(), lead_s=0.0)


# --- Task 1: _gps_pointing_cmd uses latched pose position ---------------------

import sys
import types
sys.modules.setdefault("cv2", types.SimpleNamespace())

from wavecam.gps_stub import NormalizedFix
from wavecam.pipeline import Pipeline


def _make_pointing_pipeline(lat=21.6, lon=-158.0, alt_m=2.0,
                             drive_zoom=False, gps=None):
    """Build a minimal Pipeline instance for _gps_pointing_cmd tests."""
    pipe = Pipeline.__new__(Pipeline)
    pipe.cfg = types.SimpleNamespace(
        ptz=types.SimpleNamespace(enabled=False, command_min_interval=0.0),
        gps=types.SimpleNamespace(
            max_pan_speed=4, max_tilt_speed=3, drive_zoom=drive_zoom,
        ),
    )
    from wavecam.camera_pose import CameraPose
    pose = CameraPose(lat=lat, lon=lon, alt_m=alt_m)
    pose.calibrate_pan_aim(enc=1000.0, bearing_deg=90.0, enc_per_deg=4.47)
    pipe.pose = pose
    pipe.gps = gps
    pipe._last_abs_cmd_key = None
    pipe._last_abs_cmd_time = 0.0
    return pipe


def test_gps_pointing_cmd_uses_latched_pose_without_live_gps():
    """With has_base=True and gps=None, _gps_pointing_cmd still produces a command."""
    pipe = _make_pointing_pipeline(lat=21.6, lon=-158.0, alt_m=2.0, gps=None)
    fix = NormalizedFix(lat=21.601, lon=-158.0, course=0.0, speed=0.0,
                        ts=1000.0, age_sec=2.0, src="lora")
    cmd = pipe._gps_pointing_cmd(fix, calibration_valid=True)
    assert cmd is not None
    assert isinstance(cmd.pan_enc, int)


def test_gps_pointing_cmd_base_jitter_does_not_change_bearing():
    """Changing live camera position has no effect once pose is latched."""

    class JitteryGps:
        def __init__(self, lat):
            self._lat = lat

        def get_camera_position(self):
            return (self._lat, -158.0, 2.0)

    pipe = _make_pointing_pipeline(lat=21.6, lon=-158.0, alt_m=2.0)
    fix = NormalizedFix(lat=21.601, lon=-158.0, course=0.0, speed=0.0,
                        ts=1000.0, age_sec=2.0, src="lora")
    cmd1 = pipe._gps_pointing_cmd(fix, calibration_valid=True)
    # Simulate base jitter by wiring a GPS that would return a different position
    pipe.gps = JitteryGps(21.65)
    cmd2 = pipe._gps_pointing_cmd(fix, calibration_valid=True)
    assert cmd1 is not None and cmd2 is not None
    # Latched pose is used — same bearing regardless of live GPS
    assert cmd1.pan_enc == cmd2.pan_enc


# --- Task 3: drive_zoom gate --------------------------------------------------

def test_gps_pointing_cmd_drive_zoom_false_gives_none_zoom():
    pipe = _make_pointing_pipeline(lat=21.6, lon=-158.0, alt_m=2.0, drive_zoom=False)
    fix = NormalizedFix(lat=21.601, lon=-158.0, course=0.0, speed=0.0,
                        ts=1000.0, age_sec=2.0, src="lora")
    cmd = pipe._gps_pointing_cmd(fix, calibration_valid=True)
    assert cmd is not None
    assert cmd.zoom_enc is None


def test_gps_pointing_cmd_drive_zoom_true_gives_zoom_enc():
    pipe = _make_pointing_pipeline(lat=21.6, lon=-158.0, alt_m=2.0, drive_zoom=True)
    fix = NormalizedFix(lat=21.601, lon=-158.0, course=0.0, speed=0.0,
                        ts=1000.0, age_sec=2.0, src="lora")
    cmd = pipe._gps_pointing_cmd(fix, calibration_valid=True)
    assert cmd is not None
    assert cmd.zoom_enc is not None and cmd.zoom_enc >= 0
