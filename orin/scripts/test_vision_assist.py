#!/usr/bin/env python3
"""Offline unit test for gps_fusion/vision_assist.py — synthetic detections only.

Pure logic, no Orin/camera/GPS/YOLO needed. Run anywhere:
    python3 orin/scripts/test_vision_assist.py
Exit 0 = all pass. Zero deps (no pytest).
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gps_fusion"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gps_fusion.vision_assist import (  # noqa: E402
    Detection, VisionAssist, GPS_PRIMARY, GPS_ASSISTED,
    estimate_target_size_pixels, normalized_offset, select_by_gps_gate,
    recommend_gate_px, prisual_vfov_deg,
)

_n = 0


def check(cond, msg):
    global _n
    _n += 1
    if not cond:
        print("FAIL:", msg)
        sys.exit(1)
    print("  ok:", msg)


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


# ---------------------------------------------------------------- geometry ----
print("[estimate_target_size_pixels]")
# 1.7 m person, 100 m, 3 deg vfov, 1080 px frame -> hand-computed ~350 px.
px = estimate_target_size_pixels(100.0, 3.0, 1080.0, 1.7)
check(345.0 < px < 356.0, "person@100m,3deg,1080 -> ~350px (got %.1f)" % px)

# Wider FOV (zoomed out) => same subject much smaller.
px_wide = estimate_target_size_pixels(100.0, 60.0, 1080.0, 1.7)
check(px_wide < px / 10.0, "same subject at 60deg is >10x smaller (%.1f)" % px_wide)

# Closer => bigger, monotonic.
check(estimate_target_size_pixels(50.0, 3.0, 1080.0) >
      estimate_target_size_pixels(200.0, 3.0, 1080.0),
      "closer subject => larger pixel height")

# Bad input guarded.
check(estimate_target_size_pixels(0, 3, 1080) == 0.0, "distance<=0 -> 0")
check(estimate_target_size_pixels(100, 0, 1080) == 0.0, "vfov<=0 -> 0")

# ------------------------------------------------------------- offset math ----
print("[normalized_offset]")
center = Detection(cx=960, cy=540, w=40, h=80)
ox, oy = normalized_offset(center, 1920, 1080)
check(approx(ox, 0.0) and approx(oy, 0.0), "center box -> (0,0)")
right = Detection(cx=1920, cy=540, w=40, h=80)
ox, oy = normalized_offset(right, 1920, 1080)
check(approx(ox, 1.0), "far-right box -> dx=+1")
top = Detection(cx=960, cy=0, w=40, h=80)
_, oy = normalized_offset(top, 1920, 1080)
check(approx(oy, -1.0), "top box -> dy=-1")

# --------------------------------------------------------- GPS gate select ----
print("[select_by_gps_gate]")
near = Detection(cx=1000, cy=560, w=40, h=80)     # close to prediction
far = Detection(cx=300, cy=200, w=40, h=80)       # far from prediction
pred_x, pred_y = 960, 540
got = select_by_gps_gate([far, near], pred_x, pred_y, gate_px=120)
check(got is near, "picks detection nearest GPS prediction")

# Detection outside the gate radius is rejected (returns None).
only_far = select_by_gps_gate([far], pred_x, pred_y, gate_px=120)
check(only_far is None, "lone far detection outside gate -> None")

# Size-implausible box rejected even if spatially nearest.
huge = Detection(cx=965, cy=545, w=400, h=900)    # way too big for expected
small_ok = Detection(cx=1010, cy=560, w=30, h=70)
got = select_by_gps_gate([huge, small_ok], pred_x, pred_y,
                         gate_px=200, expected_h_px=80.0)
check(got is small_ok, "rejects size-implausible box, keeps plausible one")

# Empty list -> None.
check(select_by_gps_gate([], pred_x, pred_y, 120) is None, "no detections -> None")

# --------------------------------------------------------------- hysteresis ---
print("[VisionAssist hysteresis latch]")
va = VisionAssist(enter_px=60.0, exit_px=40.0, gate_px=150.0)
fw, fh = 1920, 1080
# Geometry chosen so expected height ~= 58px: every synthetic box (30..70px)
# stays inside the size-plausibility band [0.35,3.0]x, so this section drives
# the gate purely via observed height (isolates the hysteresis mechanism).
dist, vfov = 200.0, 9.0

def step(h):
    d = Detection(cx=960, cy=540, w=h * 0.4, h=h)
    return va.evaluate([d], 960, 540, dist, vfov, fw, fh)

# Start primary. Mid-band box (45px, between exit40 and enter60) stays primary.
r = step(45)
check(r.mode == GPS_PRIMARY and r.vision_offset is None,
      "start primary; 45px (<enter) stays GPS_PRIMARY, offset None")

# Cross enter threshold -> assisted, offset present.
r = step(70)
check(r.mode == GPS_ASSISTED and r.vision_offset is not None,
      "70px (>=enter) -> GPS_ASSISTED, offset present")

# Drop back into the band (45px) -> STILL assisted (hysteresis holds).
r = step(45)
check(r.mode == GPS_ASSISTED, "45px while assisted stays GPS_ASSISTED (hysteresis)")

# Drop below exit -> back to primary, offset None.
r = step(30)
check(r.mode == GPS_PRIMARY and r.vision_offset is None,
      "30px (<exit) -> GPS_PRIMARY, offset None")

# No detection at all -> primary, None.
r = va.evaluate([], 960, 540, dist, vfov, fw, fh)
check(r.mode == GPS_PRIMARY and r.chosen is None, "empty frame -> GPS_PRIMARY, no chosen")

# enter<=exit is rejected at construction.
try:
    VisionAssist(enter_px=40, exit_px=40)
    check(False, "enter<=exit should raise")
except ValueError:
    check(True, "enter_px<=exit_px raises ValueError")

# ------------------------------------------------- GPS gate picks subject -----
print("[GPS-gated subject pick among multiple people]")
va2 = VisionAssist(enter_px=60, exit_px=40, gate_px=120)
# Two surfers: a big near-center one (a bystander) and the real subject where
# GPS predicts (off to the right). GPS gate must pick the predicted one.
bystander = Detection(cx=900, cy=540, w=50, h=100)
subject = Detection(cx=1300, cy=560, w=45, h=85)
res = va2.evaluate([bystander, subject], pred_x=1300, pred_y=560,
                   distance_m=200, vfov_deg=9, frame_w=fw, frame_h=fh)
check(res.chosen is subject, "GPS prediction selects the subject, not the nearer bystander")
check(res.mode == GPS_ASSISTED, "subject big enough -> GPS_ASSISTED")
check(res.vision_offset[0] > 0, "subject right of center -> dx>0")

# ------------------------------------------------------------- helpers --------
print("[helpers]")
check(approx(recommend_gate_px(1920), 288.0), "recommend_gate_px = 15% width")
check(prisual_vfov_deg(1.0) > prisual_vfov_deg(20.0), "vfov decreases with zoom")
check(prisual_vfov_deg(1.0) > 55 and prisual_vfov_deg(20.0) < 4,
      "vfov endpoints ~60deg @1x, ~3deg @20x")

print("\nALL %d CHECKS PASSED" % _n)
