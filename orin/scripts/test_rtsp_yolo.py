#!/usr/bin/env python3
"""Smoke test: pull frames from the camera RTSP substream and run YOLOv8 person detection.

Confirms the vision input works end to end and reports sustained inference FPS
(the budget for the control loop). Run from the Orin.

    python3 scripts/test_rtsp_yolo.py [--url rtsp://192.168.100.88:554/2] [--frames 30]
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vision.frame_source import RtspFrameSource  # noqa: E402


def find_model() -> str:
    for p in ("/data/projects/gimbal/models/yolov8n.engine",
              "/data/projects/gimbal/models/yolov8n.pt"):
        if os.path.exists(p):
            return p
    return "yolov8n.pt"  # ultralytics will fetch if missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="rtsp://192.168.100.88:554/2")
    ap.add_argument("--frames", type=int, default=30)
    args = ap.parse_args()

    from ultralytics import YOLO

    model_path = find_model()
    print(f"model: {model_path}")
    yolo = YOLO(model_path)

    print(f"opening RTSP {args.url} ...")
    src = RtspFrameSource(args.url)
    t0 = time.time()
    while src.read()[1] is None and time.time() - t0 < 12:
        time.sleep(0.1)
    ok, frame = src.read()
    if frame is None:
        print("FAIL: no RTSP frame within 12s")
        src.release()
        sys.exit(1)
    print(f"first frame: {frame.shape}")

    got, t_inf, max_persons = 0, 0.0, 0
    for _ in range(args.frames):
        ok, frame = src.read()
        if frame is None:
            time.sleep(0.03)
            continue
        got += 1
        t = time.time()
        res = yolo(frame, classes=[0], verbose=False)  # class 0 = person
        t_inf += time.time() - t
        max_persons = max(max_persons, len(res[0].boxes))
    src.release()

    fps = got / t_inf if t_inf > 0 else 0.0
    print(f"frames inferred: {got} | infer FPS: {fps:.1f} | max persons in a frame: {max_persons}")
    print("RTSP+YOLO smoke: PASS" if got > 0 else "RTSP+YOLO smoke: FAIL")
    sys.exit(0 if got > 0 else 1)


if __name__ == "__main__":
    main()
