#!/usr/bin/env python3
"""Test the camera adapter.

  python3 scripts/test_camera.py            # offline mock interface test
  python3 scripts/test_camera.py --live     # + SAFE live VISCA nudge/restore

Live test: reads position, nudges pan briefly at low speed, confirms it moved,
then restores to the original position via an absolute move. Small and safe.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camera_control.camera_adapter import MockCameraAdapter  # noqa: E402
from camera_control.visca_backend import ViscaBackend  # noqa: E402


def test_mock() -> bool:
    print("=== mock interface ===")
    m = MockCameraAdapter()
    m.connect()
    ok = True
    ok &= m.get_position().pan == 0
    m.move_absolute(100, 200)
    ok &= m.get_position().pan == 100 and m.get_position().tilt == 200
    m.pan_tilt_velocity(0.5, 0.0)
    m.step(0.1)
    ok &= m.get_position().pan > 100
    m.stop()
    m.home()
    ok &= m.get_position().pan == 0
    print(f"  mock interface: {'OK' if ok else 'FAIL'}")
    return ok


def test_live(host: str) -> bool:
    print(f"=== live VISCA @ {host}:1259 ===")
    cam = ViscaBackend(host)
    if not cam.connect():
        print("  connect/readback FAILED (no VISCA reply)")
        return False
    p0 = cam.get_position()
    print(f"  start:       pan={p0.pan} tilt={p0.tilt} zoom={p0.zoom}")

    cam.pan_tilt_velocity(0.25, 0.0)   # gentle pan-right
    time.sleep(0.4)
    cam.stop()
    time.sleep(0.4)
    p1 = cam.get_position()
    if p1 is None:
        print("  readback after nudge FAILED")
        cam.stop()
        cam.close()
        return False
    print(f"  after nudge: pan={p1.pan} tilt={p1.tilt}")
    moved = abs(p1.pan - p0.pan) > 2

    cam.move_absolute(p0.pan, p0.tilt, pan_speed=0.4, tilt_speed=0.4)
    time.sleep(1.2)
    p2 = cam.get_position()
    if p2 is None:
        print("  readback after restore FAILED")
        cam.close()
        return False
    print(f"  restored:    pan={p2.pan} tilt={p2.tilt}")
    restored = abs(p2.pan - p0.pan) <= 3 and abs(p2.tilt - p0.tilt) <= 3
    cam.close()

    print(f"  pan moved on velocity cmd: {moved}")
    print(f"  restored via absolute cmd: {restored}")
    return moved and restored


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--host", default="192.168.100.88")
    args = ap.parse_args()
    ok = test_mock()
    if args.live:
        live = test_live(args.host)
        print(f"\nLIVE PTZ: {'PASS' if live else 'FAIL'}")
        ok &= live
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
