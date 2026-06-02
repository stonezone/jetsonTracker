"""Durable controller regression — cv2-free (controller imports only ptz_visca).

Covers the step-4 additions: feed-forward lead, its jump-guard, _last reset on a
None target, and the person-box-gated zoom.

    cd ~/Downloads/wavecam-testbed && python -m tests.test_controller_extra
"""
from __future__ import annotations
import sys
from types import SimpleNamespace

from wavecam.controller import VisualServo

assert "cv2" not in sys.modules, "controller must import cv2-free"

BASE = dict(deadzone=0.10, max_pan_speed=20, max_tilt_speed=20, min_speed=1,
            invert_pan=False, invert_tilt=False)
W, H = 640, 360

_n = 0


def check(c, m):
    global _n
    _n += 1
    if not c:
        print("FAIL:", m)
        sys.exit(1)
    print("  ok:", m)


def _pure_speed(target):
    return VisualServo(SimpleNamespace(**BASE)).compute(target, (W, H)).pan_speed


# feed-forward speeds up a steadily-moving target vs pure-P
s = VisualServo(SimpleNamespace(ff_gain=0.5, **BASE))
s.compute((400, 180), (W, H))                       # prime _last at ex=0.25
ff = s.compute((460, 180), (W, H)).pan_speed        # smooth move -> lead applies
check(ff > _pure_speed((460, 180)), "feed-forward increases speed on steady motion")

# near-center detector jitter must not feed-forward the camera out of the deadzone
n = VisualServo(SimpleNamespace(ff_gain=0.5, ff_deadzone_mult=1.5, **BASE))
n.compute((W / 2 + 0.09 * (W / 2), H / 2), (W, H))  # inside deadzone
near = n.compute((W / 2 - 0.09 * (W / 2), H / 2), (W, H))
check(near.is_stop, "feed-forward stays suppressed inside the near-deadzone band")

# jump-guard: a detection switch (delta > 0.45) must NOT over-lead -> equals pure-P
j = VisualServo(SimpleNamespace(ff_gain=0.5, **BASE))
j.compute((330, 180), (W, H))                       # ex ~ 0.03
jump = j.compute((480, 180), (W, H)).pan_speed      # ex jumps to 0.5 (delta ~0.47)
check(jump == _pure_speed((480, 180)), "jump-guard suppresses the lead (behaves like pure-P)")

# _last resets on a None target (no stale lead after a dropout)
r = VisualServo(SimpleNamespace(ff_gain=0.5, **BASE))
r.compute((460, 180), (W, H))
check(r._last is not None, "_last set after a real target")
r.compute(None, (W, H))
check(r._last is None, "_last resets on None target")

# compute_zoom: person-box-gated
z = VisualServo(SimpleNamespace(target_frac=0.5, zoom_deadband=0.06, zoom_max=5, **BASE))
check(z.compute_zoom((0, 0, 40, 90), H)[0] == "tele", "small person -> tele")
check(z.compute_zoom((0, 0, 40, 300), H)[0] == "wide", "big person -> wide")
check(z.compute_zoom((0, 0, 40, 180), H) == ("stop", 0), "within deadband -> stop")
check(z.compute_zoom(None, H) == ("stop", 0), "no person box -> hold zoom (stop)")

print("\nALL %d CHECKS PASSED (cv2-free)" % _n)
