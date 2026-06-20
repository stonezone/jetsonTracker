"""Unit tests for camera_pose (calibration + bearing→encoder + base lock). No hardware."""
import pytest

from wavecam.camera_pose import CameraPose, lock_base_position


def test_uncalibrated_pan_raises():
    with pytest.raises(RuntimeError):
        CameraPose().bearing_to_pan_encoder(90.0)
    assert CameraPose().calibrated is False


def test_aim_at_remote_single_point_round_trip():
    p = CameraPose()
    p.calibrate_pan_aim(enc=1000.0, bearing_deg=90.0, enc_per_deg=4.47)
    assert p.calibrated is True
    assert abs(p.bearing_to_pan_encoder(90.0) - 1000.0) < 1e-6        # anchor returns anchor
    assert abs(p.bearing_to_pan_encoder(91.0) - 1004.47) < 1e-6       # +1 deg -> +scale
    assert abs(p.bearing_to_pan_encoder(80.0) - 955.30) < 1e-6        # -10 deg


def test_pan_mapping_wraps_across_north():
    # Anchor at 350 deg; a target at 10 deg is +20 deg away, NOT -340.
    p = CameraPose()
    p.calibrate_pan_aim(enc=0.0, bearing_deg=350.0, enc_per_deg=4.47)
    assert abs(p.bearing_to_pan_encoder(10.0) - (20.0 * 4.47)) < 1e-6


def test_two_point_pan_derives_scale():
    p = CameraPose()
    p.calibrate_pan_two_point(enc1=1000.0, bearing1=90.0, enc2=1447.0, bearing2=190.0)
    assert abs(p.pan_enc_per_deg - 4.47) < 1e-6
    assert abs(p.bearing_to_pan_encoder(90.0) - 1000.0) < 1e-6


def test_pan_two_point_rejects_equal_bearings():
    with pytest.raises(ValueError):
        CameraPose().calibrate_pan_two_point(0.0, 90.0, 100.0, 90.0)


def test_pan_two_point_rejects_negative_scale():
    # GLM A4: encoder DECREASING while bearing increases → negative enc/deg, which
    # would slew every GPS aim backwards. Reject at capture time.
    with pytest.raises(ValueError, match="non-positive"):
        CameraPose().calibrate_pan_two_point(enc1=1447.0, bearing1=90.0,
                                             enc2=1000.0, bearing2=190.0)


def test_tilt_uncalibrated_holds_anchor_calibrated_maps():
    p = CameraPose(tilt_anchor_enc=500.0)
    assert p.elevation_to_tilt_encoder(5.0) == 500.0                  # uncalibrated -> hold
    p.calibrate_tilt_two_point(enc1=500.0, elev1=0.0, enc2=480.0, elev2=10.0)
    assert abs(p.tilt_enc_per_deg - (-2.0)) < 1e-6
    assert abs(p.elevation_to_tilt_encoder(5.0) - 490.0) < 1e-6


def test_lock_base_position_averages_good_rejects_poor_falls_back():
    fixes = [
        (21.60, -158.00, 2.0, 3.0),
        (21.62, -158.02, 2.0, 4.0),
        (10.00, -10.00, 0.0, 50.0),   # poor accuracy -> rejected
    ]
    lat, lon, alt = lock_base_position(fixes, max_h_acc_m=5.0)
    assert abs(lat - 21.61) < 1e-6 and abs(lon - (-158.01)) < 1e-6   # only the two good ones
    # all poor -> fall back to all rather than None
    poor = [(1.0, 2.0, 0.0, 99.0), (3.0, 4.0, 0.0, 99.0)]
    assert lock_base_position(poor, max_h_acc_m=5.0) == (2.0, 3.0, 0.0)
    assert lock_base_position([]) is None


def test_save_load_round_trip(tmp_path):
    p = CameraPose(lat=21.6, lon=-158.0, alt_m=2.0)
    p.calibrate_pan_aim(enc=1000.0, bearing_deg=90.0, enc_per_deg=4.47)
    path = str(tmp_path / "pose.json")
    p.save(path)
    q = CameraPose.load(path)
    assert q.calibrated and abs(q.bearing_to_pan_encoder(91.0) - 1004.47) < 1e-6
    assert (q.lat, q.lon, q.alt_m) == (21.6, -158.0, 2.0)


# --- has_base property (Task 1) -----------------------------------------------

def test_has_base_false_when_default():
    assert CameraPose().has_base is False


def test_has_base_true_when_lat_set():
    assert CameraPose(lat=21.6).has_base is True


def test_has_base_true_when_lon_set():
    assert CameraPose(lon=-158.0).has_base is True


def test_has_base_true_when_both_set():
    assert CameraPose(lat=21.6, lon=-158.0, alt_m=2.0).has_base is True


def test_tilt_capture_constants_are_the_measured_truth():
    """Bench 2026-06-12: tilt hard stops at -432/+1296 counts over -30..+90 deg
    => 14.4 counts/deg (same scale as pan), encoder zero = horizontal."""
    from wavecam.camera_pose import (PRISUAL_TILT_ENC_MAX, PRISUAL_TILT_ENC_MIN,
                                     PRISUAL_TILT_ENC_PER_DEG, CameraPose)
    assert abs(PRISUAL_TILT_ENC_PER_DEG - 14.4) < 1e-9
    assert PRISUAL_TILT_ENC_MIN / PRISUAL_TILT_ENC_PER_DEG == -30.0
    assert PRISUAL_TILT_ENC_MAX / PRISUAL_TILT_ENC_PER_DEG == 90.0
    p = CameraPose()
    p.tilt_anchor_enc = 0.0
    p.tilt_anchor_elev = 0.0
    p.tilt_enc_per_deg = PRISUAL_TILT_ENC_PER_DEG
    assert abs(p.elevation_to_tilt_encoder(-30.0) - PRISUAL_TILT_ENC_MIN) < 1e-6
    assert abs(p.elevation_to_tilt_encoder(90.0) - PRISUAL_TILT_ENC_MAX) < 1e-6
