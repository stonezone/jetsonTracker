#!/usr/bin/env python3
"""Production pointing loop: gps_server (via GPSClient) -> PointingController -> camera.

Consumes the Watch (target) from gps_server and points the camera. The camera's
own position comes from the calibrated CameraPose (the beach iPhone 'base' is used
once to lock it, not per tick). Test the full path without the watch by driving
gps_server with scripts/gps_replay.py.

  # field (after calibration):
  python3 run_tracker.py --pose config/camera_pose.json
  # pipeline test (camera follows a replayed track):
  python3 scripts/gps_replay.py --hz 2 --duration 26 &
  python3 run_tracker.py --sim-cal --secs 20
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gps_fusion.gps_client import GPSClient  # noqa: E402
from gps_fusion.geo_calc import GeoPoint  # noqa: E402
from gps_fusion.camera_pose import CameraPose  # noqa: E402
from gps_fusion.pointing_controller import PointingController  # noqa: E402
from camera_control.visca_backend import ViscaBackend  # noqa: E402
from camera_control.camera_adapter import MockCameraAdapter  # noqa: E402

SIM_BASE = (21.2760, -157.8270, 2.0)  # must match gps_replay.py base


def sim_pose(cam_start_pan: float) -> CameraPose:
    """Synthetic calibration for pipeline tests (no field calibration needed)."""
    pose = CameraPose(lat=SIM_BASE[0], lon=SIM_BASE[1], alt_m=SIM_BASE[2])
    pose.calibrate_pan_two_point(cam_start_pan - 2500, 160.0, cam_start_pan + 2500, 240.0)
    pose.tilt_anchor_enc = 305.0
    return pose


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pose", help="camera_pose.json (field calibration)")
    ap.add_argument("--sim-cal", action="store_true", help="synthetic calibration for testing")
    ap.add_argument("--gps-uri", default="ws://localhost:8765")
    ap.add_argument("--host", default="192.168.100.88")
    ap.add_argument("--mock-camera", action="store_true")
    ap.add_argument("--zoom", action="store_true", help="enable distance->zoom")
    ap.add_argument("--rate", type=float, default=20.0)
    ap.add_argument("--secs", type=float, default=0.0, help="0 = run forever")
    ap.add_argument("--gps-timeout", type=float, default=5.0)
    args = ap.parse_args()

    cam = MockCameraAdapter(tilt=305.0) if args.mock_camera else ViscaBackend(args.host)
    if not cam.connect():
        print("camera connect failed")
        sys.exit(1)
    start = cam.get_position()

    if args.pose:
        pose = CameraPose.load(args.pose)
    elif args.sim_cal:
        pose = sim_pose(start.pan if start else 0.0)
        print(f"[sim-cal] base={SIM_BASE[:2]} pan_enc_per_deg={pose.pan_enc_per_deg:.1f}")
    else:
        print("need --pose or --sim-cal")
        sys.exit(1)

    base = GeoPoint(lat=pose.lat, lon=pose.lon, alt=pose.alt_m)
    pc = PointingController(pose, cam, track_tilt=False, zoom_enabled=args.zoom)
    gps = GPSClient(uri=args.gps_uri)
    gps.start()
    print(f"[run_tracker] gps={args.gps_uri} rate={args.rate}Hz")

    period = 1.0 / args.rate
    t0, last_log = time.time(), 0.0
    try:
        while args.secs == 0.0 or time.time() - t0 < args.secs:
            st = gps.get_state()
            target = st.target
            fresh = target is not None and (time.time() - st.target_updated) < args.gps_timeout
            if fresh:
                status = pc.point_at(base, target)
                if status and time.time() - last_log > 1.0:
                    last_log = time.time()
                    print(f"  brg={status.bearing:5.1f} dist={status.distance:5.0f}m "
                          f"pan={status.pan_cur_enc:8.0f}/{status.pan_target_enc:8.0f} "
                          f"err={status.pan_err:7.0f} "
                          f"zoom={status.zoom_cur_enc:6.0f}/{status.zoom_target_enc:6.0f} "
                          f"[{status.action}]")
            else:
                pc.stop()
                if time.time() - last_log > 2.0:
                    last_log = time.time()
                    print("  (no fresh target; holding)")
            time.sleep(period)
    finally:
        pc.stop()
        gps.stop()
        if start is not None and not args.mock_camera:
            cam.move_absolute(start.pan, start.tilt, 0.5, 0.5)
            time.sleep(1.5)
        cam.close()
    print("[run_tracker] done")


if __name__ == "__main__":
    main()
