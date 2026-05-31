#!/usr/bin/env python3
"""Unit tests for camera_pose calibration + conversions (synthetic, no hardware)."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gps_fusion.camera_pose import CameraPose, ang_diff, lock_base_position  # noqa: E402


def test_pan_two_point():
    p = CameraPose()
    # Encoder increases 100 counts per degree of bearing.
    p.calibrate_pan_two_point(enc1=0, bearing1=100.0, enc2=1000, bearing2=110.0)
    assert abs(p.pan_enc_per_deg - 100.0) < 1e-6
    assert abs(p.bearing_to_pan_encoder(105.0) - 500.0) < 1e-6
    assert abs(p.bearing_to_pan_encoder(100.0) - 0.0) < 1e-6
    assert p.calibrated


def test_pan_wraparound():
    p = CameraPose()
    # 20 deg of bearing spanning the 360/0 wrap, 200 counts -> 10 counts/deg.
    p.calibrate_pan_two_point(0, 350.0, 200, 10.0)
    assert abs(p.pan_enc_per_deg - 10.0) < 1e-6
    assert abs(p.bearing_to_pan_encoder(0.0) - 100.0) < 1e-6  # 10 deg from 350


def test_tilt_two_point():
    p = CameraPose()
    p.calibrate_tilt_two_point(enc1=0, elev1=0.0, enc2=-50, elev2=5.0)  # -10 counts/deg
    assert abs(p.elevation_to_tilt_encoder(2.0) - (-20.0)) < 1e-6


def test_uncalibrated_tilt_holds():
    p = CameraPose(tilt_anchor_enc=305.0)
    assert p.elevation_to_tilt_encoder(3.0) == 305.0  # no tilt cal -> hold


def test_base_lock_rejects_outlier():
    fixes = [(21.0, -157.0, 2.0, 3.0),
             (21.0002, -157.0002, 2.0, 4.0),
             (99.0, 99.0, 0.0, 50.0)]  # 50 m accuracy -> rejected
    la, lo, al = lock_base_position(fixes, max_h_acc_m=5.0)
    assert abs(la - 21.0001) < 1e-4 and abs(lo - (-157.0001)) < 1e-4


def test_save_load_roundtrip():
    p = CameraPose(lat=21.0, lon=-157.0, alt_m=2.0)
    p.calibrate_pan_two_point(0, 100, 1000, 110)
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        p.save(path)
        q = CameraPose.load(path)
        assert q.lat == 21.0 and abs(q.bearing_to_pan_encoder(105) - 500) < 1e-6
    finally:
        os.unlink(path)


def test_ang_diff():
    assert abs(ang_diff(10, 350) - 20) < 1e-9
    assert abs(ang_diff(350, 10) - (-20)) < 1e-9
    assert abs(ang_diff(180, 0) - 180) < 1e-9


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
