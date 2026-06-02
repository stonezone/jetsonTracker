#!/usr/bin/env python3
"""Vision-follow service (yard MVP) — YOLO person + orange color -> PTZ center + zoom.

No GPS. RTSP /2 -> detect -> pick target -> pan/tilt to center it + zoom so the
target holds a target fraction of frame height. The orange rashguard is the primary
cue; YOLO confirms it is a person. Target priority:

  1. color-confirmed person  (orange blob inside a YOLO person box)  — best
  2. largest YOLO person      (you, no orange visible)
  3. largest orange/red blob  (YOLO missed — far / prone / occluded)
  4. none -> hold (stop the camera)

Control is the proven proportional + feed-forward pan/tilt + zoom-to-frame loop from
the bench follow demo. SIGTERM -> graceful stop + restore camera home, so the
dashboard can start/stop it cleanly. Prints one status line ~2 Hz for the UI readout.

  python3 vision/vision_follow.py [--secs 0] [--no-yolo] [--no-color] [--target-frac 0.55]
"""

import argparse
import os
import signal
import sys
import time
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _inside(px, py, pcx, pcy, pw, ph):
    return abs(px - pcx) <= pw / 2.0 and abs(py - pcy) <= ph / 2.0


def _choose(items, center_of, area_of, last_center):
    """Within a tier, prefer the candidate nearest last frame's target (continuity,
    avoids flipping between objects); fall back to the largest on fresh acquisition."""
    if last_center is not None:
        lx, ly = last_center
        return min(items, key=lambda it: (center_of(it)[0] - lx) ** 2 + (center_of(it)[1] - ly) ** 2)
    return max(items, key=area_of)


def pick_target(persons, colors, last_center=None):
    """persons: list of (cx,cy,w,h) YOLO person boxes. colors: list of ColorBox.
    last_center: previous target (cx,cy) for temporal continuity, or None.
    Priority: color-confirmed person > person > color blob > None."""
    pc, pa = (lambda p: (p[0], p[1])), (lambda p: p[2] * p[3])
    cc, ca = (lambda c: (c.cx, c.cy)), (lambda c: c.area)
    confirmed = [p for p in persons
                 if any(_inside(cb.cx, cb.cy, p[0], p[1], p[2], p[3]) for cb in colors)]
    if confirmed:
        t = _choose(confirmed, pc, pa, last_center)
        return (t[0], t[1], t[2], t[3], "both")
    if persons:
        t = _choose(persons, pc, pa, last_center)
        return (t[0], t[1], t[2], t[3], "yolo")
    if colors:
        c = _choose(colors, cc, ca, last_center)
        return (c.cx, c.cy, c.w, c.h, "color")
    return None


@dataclass
class FollowControlState:
    prev_ox: float | None = None
    prev_oy: float | None = None
    prev_t: float | None = None


def _axis_velocity(err, prev, dt, prop_gain, ff_sign, args):
    prop = 0.0 if abs(err) < args.deadband else prop_gain * err
    if args.no_ff or prev is None or dt <= 1e-3 or abs(err - prev) >= 0.45:
        return prop

    near_band = args.deadband * max(1.0, args.ff_deadband_mult)
    if abs(err) <= near_band or abs(prev) <= near_band:
        return prop
    return prop + ff_sign * args.ff_gain * (err - prev) / dt


def compute_follow_velocity(offx, offy, state, args, now=None):
    """Compute pan/tilt velocity while suppressing feed-forward near deadband."""
    now = time.time() if now is None else now
    dt = 0.0 if state.prev_t is None else now - state.prev_t

    pv = _axis_velocity(offx, state.prev_ox, dt, args.kp_pan, 1.0, args)
    tv = _axis_velocity(offy, state.prev_oy, dt, -args.kp_tilt, -1.0, args)

    state.prev_ox, state.prev_oy, state.prev_t = offx, offy, now
    return clamp(pv, -args.max_vel, args.max_vel), clamp(tv, -args.max_vel, args.max_vel)


def find_model():
    for p in ("/data/projects/gimbal/models/yolov8n.engine",
              "/data/projects/gimbal/models/yolov8n.pt"):
        if os.path.exists(p):
            return p
    return "yolov8n.pt"


def main():
    from camera_control.visca_backend import ViscaBackend  # noqa: WPS433
    from vision.color_detector import ColorConfig, detect as color_detect  # noqa: WPS433
    from vision.frame_source import RtspFrameSource  # noqa: WPS433

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.100.88")
    ap.add_argument("--url", default="rtsp://192.168.100.88:554/2")
    ap.add_argument("--secs", type=float, default=0.0, help="0 = run until SIGTERM")
    ap.add_argument("--conf", type=float, default=0.5)
    ap.add_argument("--no-yolo", action="store_true", help="color-only")
    ap.add_argument("--no-color", action="store_true", help="YOLO-only")
    ap.add_argument("--kp-pan", type=float, default=0.85)
    ap.add_argument("--kp-tilt", type=float, default=0.7)
    ap.add_argument("--deadband", type=float, default=0.07)
    ap.add_argument("--max-vel", type=float, default=1.0)
    ap.add_argument("--ff-gain", type=float, default=0.4)
    ap.add_argument("--ff-deadband-mult", type=float, default=1.5)
    ap.add_argument("--no-ff", action="store_true")
    ap.add_argument("--target-frac", type=float, default=0.55)
    ap.add_argument("--kp-zoom", type=float, default=2.0)
    ap.add_argument("--zoom-deadband", type=float, default=0.06)
    ap.add_argument("--zoom-max-vel", type=float, default=0.5)
    ap.add_argument("--no-zoom", action="store_true")
    ap.add_argument("--no-restore", action="store_true")
    args = ap.parse_args()

    stop = {"f": False}
    signal.signal(signal.SIGTERM, lambda *a: stop.update(f=True))

    yolo = None
    if not args.no_yolo:
        try:
            from ultralytics import YOLO
            m = find_model()
            print(f"[follow] yolo model: {m}", flush=True)
            yolo = YOLO(m)
        except Exception as e:
            print(f"[follow] YOLO unavailable ({e}); color-only", flush=True)
    ccfg = None if args.no_color else ColorConfig()

    cam = ViscaBackend(args.host)
    if not cam.connect():
        print("[follow] camera connect failed", flush=True)
        sys.exit(1)
    start = cam.get_position()

    src = RtspFrameSource(args.url)
    t0 = time.time()
    while src.read()[1] is None and time.time() - t0 < 10:
        time.sleep(0.1)
    print("[follow] tracking (yolo+color -> pan/tilt + zoom). Ctrl-C/SIGTERM to stop.", flush=True)

    last_log, t0 = 0.0, time.time()
    control_state = FollowControlState()
    last_center = None
    frames = 0
    try:
        while not stop["f"] and (args.secs == 0.0 or time.time() - t0 < args.secs):
            ok, frame = src.read()
            if frame is None:
                time.sleep(0.03)
                continue
            frames += 1
            h, w = frame.shape[:2]

            persons = []
            if yolo is not None:
                res = yolo(frame, classes=[0], conf=args.conf, verbose=False)
                b = res[0].boxes
                if b is not None and len(b) > 0:
                    persons = [tuple(map(float, xywh)) for xywh in b.xywh.cpu().numpy()]
            colors = []
            if ccfg is not None:
                colors, _ = color_detect(frame, ccfg)

            tgt = pick_target(persons, colors, last_center)
            if tgt is None:
                cam.stop()
                control_state = FollowControlState()
                last_center = None
                if time.time() - last_log > 1.0:
                    last_log = time.time()
                    print("[follow] no target; holding", flush=True)
                time.sleep(0.04)
                continue

            cx, cy, bw, bh, srcname = tgt
            last_center = (cx, cy)
            offx = (cx - w / 2.0) / (w / 2.0)
            offy = (cy - h / 2.0) / (h / 2.0)
            pv, tv = compute_follow_velocity(offx, offy, control_state, args)
            cam.pan_tilt_velocity(pv, tv)

            frac = bh / h
            zv = 0.0
            if not args.no_zoom:
                # Zoom only off a person box (yolo/both). A color-patch height is
                # not a reliable framing reference — driving zoom from it makes the
                # zoom oscillate. On color-only frames, hold zoom (zv=0).
                if srcname in ("yolo", "both"):
                    zerr = args.target_frac - frac
                    zv = 0.0 if abs(zerr) < args.zoom_deadband else clamp(
                        args.kp_zoom * zerr, -args.zoom_max_vel, args.zoom_max_vel)
                cam.zoom_velocity(zv)

            if time.time() - last_log > 0.5:
                fps = frames / (time.time() - t0) if time.time() > t0 else 0.0
                last_log = time.time()
                print(f"[follow] src={srcname} off=({offx:+.2f},{offy:+.2f}) "
                      f"size={frac:.2f}/{args.target_frac:.2f} pan={pv:+.2f} tilt={tv:+.2f} "
                      f"zoom={zv:+.2f} fps={fps:.0f}", flush=True)
            time.sleep(0.04)
    finally:
        cam.stop()
        src.release()
        if start is not None and not args.no_restore:
            cam.move_absolute(start.pan, start.tilt, 0.5, 0.5)
            cam.zoom_velocity(-0.8)
            time.sleep(6.0)
            cam.zoom_velocity(0.0)
            time.sleep(0.5)
        cam.close()
    print("[follow] done", flush=True)


if __name__ == "__main__":
    main()
