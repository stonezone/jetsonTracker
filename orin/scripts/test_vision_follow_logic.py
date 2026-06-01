#!/usr/bin/env python3
"""Offline test for vision_follow.pick_target — target fusion priority logic.

Pure logic (no camera/YOLO). Verifies the orange-cue priority:
color-confirmed person > largest person > largest color blob > none.
    python3 scripts/test_vision_follow_logic.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vision.vision_follow import pick_target  # noqa: E402
from vision.color_detector import ColorBox  # noqa: E402

_n = 0


def check(c, m):
    global _n
    _n += 1
    if not c:
        print("FAIL:", m)
        sys.exit(1)
    print("  ok:", m)


# person = (cx, cy, w, h)
P_you = (320, 240, 60, 160)         # centered
P_big = (500, 240, 100, 200)        # larger bystander, right side
CB_on_you = ColorBox(cx=320, cy=220, w=40, h=60, area=2000, fill=0.9)   # orange inside P_you
CB_off = ColorBox(cx=50, cy=50, w=30, h=40, area=900, fill=0.8)         # orange in a corner

t = pick_target([P_you], [CB_on_you])
check(t and t[4] == "both" and abs(t[0] - 320) < 1, "orange inside person -> source 'both'")

t = pick_target([P_you], [CB_off])
check(t and t[4] == "yolo", "orange outside any person -> source 'yolo'")

t = pick_target([P_big, P_you], [CB_on_you])
check(t and t[4] == "both" and abs(t[0] - 320) < 1,
      "color-confirmed YOU beats the larger un-confirmed bystander")

t = pick_target([], [CB_off])
check(t and t[4] == "color" and abs(t[0] - 50) < 1, "no person, orange blob -> source 'color'")

check(pick_target([], []) is None, "nothing detected -> None")

t = pick_target([P_you, P_big], [])
check(t and t[4] == "yolo" and abs(t[0] - 500) < 1, "persons only -> largest person, 'yolo'")

# temporal continuity: nearest-to-last beats largest within a tier (anti-flip)
CB_a = ColorBox(cx=100, cy=100, w=40, h=40, area=1600, fill=0.9)        # smaller, left
CB_b = ColorBox(cx=500, cy=300, w=120, h=120, area=14400, fill=0.9)     # larger, right
t = pick_target([], [CB_a, CB_b])
check(t and abs(t[0] - 500) < 1, "fresh acquisition -> largest color blob (B)")
t = pick_target([], [CB_a, CB_b], last_center=(110, 110))
check(t and abs(t[0] - 100) < 1, "continuity: nearest-to-last picks A over larger B")
t = pick_target([P_you, P_big], [], last_center=(320, 240))
check(t and abs(t[0] - 320) < 1, "continuity: nearest-to-last picks P_you over larger P_big")

print("\nALL %d CHECKS PASSED" % _n)
