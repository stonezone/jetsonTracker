"""Durable fusion regression — cv2-free (SimpleNamespace blobs + persons).

Locks down the priority + no-steal behavior Codex flagged:
  - a color-confirmed person locks
  - a locked near-person is NOT stolen by a far unmatched blob (the jump bug)
  - color-only carries the lock through a YOLO dropout
  - person-only (no orange) stays weak (no lock)

Imports only wavecam.fusion (type-only color/detector imports are behind
TYPE_CHECKING), so this runs without cv2/torch — incl. on the reviewer's machine.

    cd ~/Downloads/wavecam-testbed && python -m tests.test_fusion
"""
from __future__ import annotations
import sys
from types import SimpleNamespace

from wavecam.fusion import Fusion

assert "cv2" not in sys.modules, "fusion must import cv2-free (type-only imports)"


def _cfg():
    return SimpleNamespace(match_dist=120, require_person=False, lock_threshold=0.60,
                           unlock_threshold=0.35, ema_alpha=0.5, lost_grace_sec=0.8)


def _blob(cx, cy, h=80, area=5000):
    return SimpleNamespace(cx=cx, cy=cy, area=area,
                           bbox=(int(cx - 20), int(cy - h / 2), 40, h), fill=0.9)


def _person(cx, cy, conf=0.85):
    return SimpleNamespace(center=(cx, cy), xywh=(int(cx - 20), int(cy - 45), 40, 90), conf=conf)


_n = 0


def check(c, m):
    global _n
    _n += 1
    if not c:
        print("FAIL:", m)
        sys.exit(1)
    print("  ok:", m)


f = Fusion(_cfg())
r = f.update([_blob(320, 180)], [_person(322, 182)])
check(r.matched and r.locked and r.conf >= 0.6, "color-confirmed person locks")

r2 = f.update([_blob(40, 40, area=12000)], [_person(320, 180)])
check(r2.target_xy[0] > 250 and r2.locked, "locked near-person not stolen by a far blob")

r3 = f.update([_blob(322, 182)], [])
check(r3.target_xy[0] > 250 and r3.locked, "color-only carries lock through YOLO dropout")

g = Fusion(_cfg())
ro = g.update([], [_person(100, 100, 0.9)])
check(ro.conf == 0.2 and not ro.locked, "person-only stays weak (no lock)")

print("\nALL %d CHECKS PASSED (cv2-free)" % _n)
