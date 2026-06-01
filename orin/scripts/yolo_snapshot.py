#!/usr/bin/env python3
"""Grab a frame from RTSP /2, run YOLOv8 person detection, draw boxes, save.

Verification artifact: confirms the vision pipeline on the live feed + gives a
visual (camera view + detection boxes). No camera movement.

  python3 scripts/yolo_snapshot.py [out.jpg]
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2  # noqa: E402

from vision.frame_source import RtspFrameSource  # noqa: E402


def find_model():
    for p in ("/data/projects/gimbal/models/yolov8n.engine",
              "/data/projects/gimbal/models/yolov8n.pt"):
        if os.path.exists(p):
            return p
    return "yolov8n.pt"


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/yolo_snap.jpg"
    from ultralytics import YOLO
    yolo = YOLO(find_model())
    src = RtspFrameSource("rtsp://192.168.100.88:554/2")
    t0 = time.time()
    while src.read()[1] is None and time.time() - t0 < 10:
        time.sleep(0.1)

    best, best_n = None, -1
    for _ in range(15):
        ok, frame = src.read()
        if frame is None:
            time.sleep(0.1)
            continue
        r = yolo(frame, classes=[0], conf=0.4, verbose=False)[0]
        n = len(r.boxes) if r.boxes is not None else 0
        if n > best_n:
            best_n, best = n, (frame.copy(), r)
        if n > 0:
            break
        time.sleep(0.1)
    src.release()

    if best is None:
        print("no frame")
        sys.exit(1)
    frame, r = best
    if r.boxes is not None:
        for b in r.boxes:
            x1, y1, x2, y2 = map(int, b.xyxy[0].cpu().numpy())
            conf = float(b.conf[0])
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(frame, f"person {conf:.2f}", (x1, max(12, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
    cv2.imwrite(out, frame)
    print(f"saved {out} detections={best_n}")


if __name__ == "__main__":
    main()
