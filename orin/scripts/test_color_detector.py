#!/usr/bin/env python3
"""Offline test for vision/color_detector.py — synthetic frames, no camera.

Builds BGR frames with orange/red rectangles on a teal (water-like) background and
asserts the detector finds them, locates them, and rejects specks / glare-wash.
Needs cv2 + numpy (present on the Orin). Run:
    python3 scripts/test_color_detector.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from vision.color_detector import detect, build_mask, ColorConfig  # noqa: E402

W, H = 640, 360
TEAL = (128, 128, 0)      # BGR — turquoise-ish, hue far from orange/red
ORANGE = (0, 165, 255)    # BGR for RGB(255,165,0) -> HSV H~19 (orange band)
RED = (0, 0, 255)         # BGR pure red -> HSV H~0 (red band 1)

_n = 0


def check(c, m):
    global _n
    _n += 1
    if not c:
        print("FAIL:", m)
        sys.exit(1)
    print("  ok:", m)


def frame(bg=TEAL):
    f = np.zeros((H, W, 3), dtype=np.uint8)
    f[:] = bg
    return f


def near(a, b, tol):
    return abs(a - b) <= tol


print("[orange blob on teal]")
f = frame()
cv2.rectangle(f, (300, 150), (360, 250), ORANGE, -1)   # 60x100 orange, center (330,200)
boxes, mask = detect(f)
check(len(boxes) >= 1, "orange rectangle detected (%d box)" % len(boxes))
b = boxes[0]
check(near(b.cx, 330, 12) and near(b.cy, 200, 12), "blob center ~ (330,200) (got %.0f,%.0f)" % (b.cx, b.cy))
check(near(b.w, 60, 14) and near(b.h, 100, 14), "blob size ~ 60x100 (got %.0fx%.0f)" % (b.w, b.h))
check(b.fill > 0.8, "solid blob fill > 0.8 (got %.2f)" % b.fill)

print("[red wraparound]")
f = frame()
cv2.rectangle(f, (100, 100), (160, 200), RED, -1)
boxes, _ = detect(f)
check(len(boxes) >= 1, "red rectangle detected via hue-wraparound")
check(near(boxes[0].cx, 130, 12), "red blob center x ~130 (got %.0f)" % boxes[0].cx)

print("[red disabled -> orange only]")
f = frame()
cv2.rectangle(f, (100, 100), (160, 200), RED, -1)
boxes, _ = detect(f, ColorConfig(use_red=False))
check(len(boxes) == 0, "use_red=False ignores the red blob (got %d)" % len(boxes))

print("[speck rejected]")
f = frame()
cv2.rectangle(f, (320, 180), (325, 185), ORANGE, -1)   # 5x5 = 25px < min_area 80
boxes, _ = detect(f)
check(len(boxes) == 0, "tiny speck below min_area rejected")

print("[glare wash rejected]")
f = frame()
f[:] = ORANGE                                           # whole frame orange (sun wash)
boxes, _ = detect(f)
check(all(b.area <= 0.5 * W * H for b in boxes), "full-frame wash exceeds max_area_frac -> not returned")
check(len(boxes) == 0, "glare-wash frame yields no usable blob (got %d)" % len(boxes))

print("[two blobs sorted largest-first]")
f = frame()
cv2.rectangle(f, (60, 60), (110, 160), ORANGE, -1)      # 50x100 = 5000
cv2.rectangle(f, (400, 200), (520, 320), ORANGE, -1)    # 120x120 = 14400 (bigger)
boxes, _ = detect(f)
check(len(boxes) == 2, "two blobs found (got %d)" % len(boxes))
check(boxes[0].area > boxes[1].area and boxes[0].cx > 380, "largest blob first (the 120x120)")

print("[empty frame]")
boxes, _ = detect(frame())
check(len(boxes) == 0, "teal-only frame -> no detections")

print("[mask isolates orange]")
f = frame()
cv2.rectangle(f, (300, 150), (360, 250), ORANGE, -1)
mask = build_mask(f, ColorConfig())
on = int((mask > 0).sum())
check(4000 < on < 8000, "mask ~ the 6000px orange region, not the background (got %d)" % on)

print("\nALL %d CHECKS PASSED" % _n)
