#!/usr/bin/env python3
"""Pointing-loop demo: drive the camera from a synthetic foiling track.

Generates a target sweeping in bearing around a fixed base, builds a synthetic
camera_pose calibration mapping that bearing range into a safe encoder window,
and runs the PointingController so the camera follows.

  python3 scripts/test_pointing.py --mock            # offline (MockCameraAdapter)
  python3 scripts/test_pointing.py --live --secs 15  # REAL camera physically follows

Live mode restores the camera to its starting position when done.
"""

import argparse
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gps_fusion.geo_calc import GeoPoint  # noqa: E402
from gps_fusion.camera_pose import CameraPose  # noqa: E402
from gps_fusion.pointing_controller import PointingController  # noqa: E402
from camera_control.camera_adapter import MockCameraAdapter  # noqa: E402
from camera_control.visca_backend import ViscaBackend  # noqa: E402

EARTH_R = 6_371_000.0
BASE = GeoPoint(lat=21.2760, lon=-157.8270, alt=2.0)


def offset(lat, lon, bearing_deg, dist_m):
    br = math.radians(bearing_deg)
    lat1, lon1 = math.radians(lat), math.radians(lon)
    dr = dist_m / EARTH_R
    lat2 = math.asin(math.sin(lat1) * math.cos(dr) + math.cos(lat1) * math.sin(dr) * math.cos(br))
    lon2 = lon1 + math.atan2(math.sin(br) * math.sin(dr) * math.cos(lat1),
                             math.cos(dr) - math.sin(lat1) * math.sin(lat2))
    return math.degrees(lat2), math.degrees(lon2)


def make_target(t):
    """Subject sweeps bearing ~ +-15 deg around 200, ~120 m offshore."""
    bearing = 200.0 + 15.0 * math.sin(t / 4.0)
    la, lo = offset(BASE.lat, BASE.lon, bearing, 120.0)
    return GeoPoint(lat=la, lon=lo, alt=0.5, speed=4.0,
                    course=(bearing + 90) % 360, timestamp=time.time())


def build_sim_pose(cam_start_pan):
    """Map bearing 185..215 deg onto +-1500 encoder counts around the camera start."""
    pose = CameraPose(lat=BASE.lat, lon=BASE.lon, alt_m=BASE.alt)
    pose.calibrate_pan_two_point(enc1=cam_start_pan - 1500, bearing1=185.0,
                                 enc2=cam_start_pan + 1500, bearing2=215.0)
    pose.tilt_anchor_enc = 305.0
    return pose


def run(cam, secs, is_mock):
    start = cam.get_position()
    if start is None:
        print("  no camera position")
        return False
    pose = build_sim_pose(start.pan)
    pc = PointingController(pose, cam, track_tilt=False)
    print(f"  start pan={start.pan} ; sweeping target bearing 185..215 for {secs:.0f}s")
    t0, last, last_err = time.time(), 0.0, None
    while time.time() - t0 < secs:
        t = time.time() - t0
        st = pc.point_at(BASE, make_target(t))
        if is_mock:
            cam.step(0.05, scale=8000.0)
        if st:
            last_err = st.pan_err
            if time.time() - last > 1.0:
                last = time.time()
                print(f"  t={t:4.1f} brg={st.bearing:5.1f} "
                      f"target={st.pan_target_enc:8.0f} cur={st.pan_cur_enc:8.0f} "
                      f"err={st.pan_err:7.0f} [{st.action}]")
        time.sleep(0.05)
    pc.stop()
    cam.move_absolute(start.pan, start.tilt, 0.5, 0.5)
    if not is_mock:
        time.sleep(1.2)
    p = cam.get_position()
    print(f"  restored pan={p.pan if p else '??'}")
    tracking_ok = last_err is not None and abs(last_err) < 400
    print(f"  tracking error within tolerance: {tracking_ok} (last err={last_err})")
    return tracking_ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--host", default="192.168.100.88")
    ap.add_argument("--secs", type=float, default=15.0)
    args = ap.parse_args()

    if args.mock or not args.live:
        print("=== mock pointing loop ===")
        cam = MockCameraAdapter(pan=0.0, tilt=305.0, zoom=0.0)
        cam.connect()
        ok = run(cam, args.secs if args.live else 8.0, is_mock=True)
        print(f"mock pointing: {'PASS' if ok else 'FAIL'}")
        if not args.live:
            sys.exit(0 if ok else 1)

    if args.live:
        print("=== LIVE pointing (camera WILL move) ===")
        cam = ViscaBackend(args.host)
        if not cam.connect():
            print("camera connect failed")
            sys.exit(1)
        ok = run(cam, args.secs, is_mock=False)
        cam.close()
        print(f"LIVE pointing: {'PASS' if ok else 'FAIL'}")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
