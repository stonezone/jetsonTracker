#!/usr/bin/env python3
"""Vision-follow demo: YOLO detects the largest person; the camera centers AND
frames them by apparent size (zoom).

RTSP /2 -> YOLOv8 person detection -> pan/tilt to center + zoom so the person's
bbox height holds a target fraction of the frame (zoom IN when you're small/far,
OUT when you're close). Vision-only (no GPS): follows whoever is biggest in frame.

  python3 scripts/test_vision_follow.py [--secs 30] [--target-frac 0.55] [--no-restore]
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vision.frame_source import RtspFrameSource  # noqa: E402
from camera_control.visca_backend import ViscaBackend  # noqa: E402


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def find_model():
    for p in ("/data/projects/gimbal/models/yolov8n.engine",
              "/data/projects/gimbal/models/yolov8n.pt"):
        if os.path.exists(p):
            return p
    return "yolov8n.pt"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.100.88")
    ap.add_argument("--url", default="rtsp://192.168.100.88:554/2")
    ap.add_argument("--secs", type=float, default=30.0)
    ap.add_argument("--conf", type=float, default=0.5)
    ap.add_argument("--kp-pan", type=float, default=0.85)
    ap.add_argument("--kp-tilt", type=float, default=0.7)
    ap.add_argument("--deadband", type=float, default=0.07)
    ap.add_argument("--max-vel", type=float, default=1.0)
    ap.add_argument("--ff-gain", type=float, default=0.4, help="feed-forward gain on offset rate")
    ap.add_argument("--no-ff", action="store_true")
    # zoom-by-apparent-size
    ap.add_argument("--target-frac", type=float, default=0.55,
                    help="desired person bbox height as fraction of frame height")
    ap.add_argument("--kp-zoom", type=float, default=2.0)
    ap.add_argument("--zoom-deadband", type=float, default=0.06)
    ap.add_argument("--zoom-max-vel", type=float, default=0.5)
    ap.add_argument("--no-zoom", action="store_true")
    ap.add_argument("--no-restore", action="store_true")
    args = ap.parse_args()

    from ultralytics import YOLO
    model = find_model()
    print(f"model: {model}")
    yolo = YOLO(model)

    cam = ViscaBackend(args.host)
    if not cam.connect():
        print("camera connect failed")
        sys.exit(1)
    start = cam.get_position()

    src = RtspFrameSource(args.url)
    t0 = time.time()
    while src.read()[1] is None and time.time() - t0 < 10:
        time.sleep(0.1)
    print("tracking the largest person (pan/tilt + zoom-to-frame); STAND IN FRONT...")

    last, t0 = 0.0, time.time()
    prev_offx = prev_offy = prev_t = None
    try:
        while time.time() - t0 < args.secs:
            ok, frame = src.read()
            if frame is None:
                time.sleep(0.03)
                continue
            h, w = frame.shape[:2]
            res = yolo(frame, classes=[0], conf=args.conf, verbose=False)
            boxes = res[0].boxes
            if boxes is None or len(boxes) == 0:
                cam.stop()
                if time.time() - last > 1.0:
                    last = time.time()
                    print("  no person; holding")
                continue
            xywh = boxes.xywh.cpu().numpy()
            i = (xywh[:, 2] * xywh[:, 3]).argmax()
            cx, cy, bw, bh = xywh[i]
            offx = (cx - w / 2) / (w / 2)
            offy = (cy - h / 2) / (h / 2)
            now = time.time()
            ffx = ffy = 0.0
            if not args.no_ff and prev_offx is not None:
                dt = now - prev_t
                # Feed-forward only on smooth motion; a big jump is a detection
                # switch (e.g. between people), not real movement.
                if dt > 1e-3 and abs(offx - prev_offx) < 0.45 and abs(offy - prev_offy) < 0.45:
                    ffx = args.ff_gain * (offx - prev_offx) / dt
                    ffy = -args.ff_gain * (offy - prev_offy) / dt
            prev_offx, prev_offy, prev_t = offx, offy, now
            prop_x = 0.0 if abs(offx) < args.deadband else args.kp_pan * offx
            prop_y = 0.0 if abs(offy) < args.deadband else -args.kp_tilt * offy
            pv = clamp(prop_x + ffx, -args.max_vel, args.max_vel)
            tv = clamp(prop_y + ffy, -args.max_vel, args.max_vel)
            cam.pan_tilt_velocity(pv, tv)

            frac = bh / h
            zv = 0.0
            if not args.no_zoom:
                zerr = args.target_frac - frac        # +: person too small -> zoom IN (tele)
                zv = 0.0 if abs(zerr) < args.zoom_deadband else clamp(args.kp_zoom * zerr,
                                                                      -args.zoom_max_vel, args.zoom_max_vel)
                cam.zoom_velocity(zv)

            if time.time() - last > 0.5:
                last = time.time()
                print(f"  offx={offx:+.2f} offy={offy:+.2f} size={frac:.2f}/{args.target_frac:.2f} "
                      f"-> pan={pv:+.2f} tilt={tv:+.2f} zoom={zv:+.2f}")
            time.sleep(0.05)
    finally:
        cam.stop()
        src.release()
        if start is not None and not args.no_restore:
            cam.move_absolute(start.pan, start.tilt, 0.5, 0.5)
            cam.zoom_velocity(-0.8)   # back to wide
            time.sleep(6.0)
            cam.zoom_velocity(0.0)
            time.sleep(0.5)
        cam.close()
    print("done")


if __name__ == "__main__":
    main()
