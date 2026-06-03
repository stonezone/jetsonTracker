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

_modules_before_fusion = set(sys.modules)
from wavecam.fusion import Fusion

assert "cv2" not in (
    set(sys.modules) - _modules_before_fusion
), "fusion must import cv2-free (type-only imports)"


def _cfg():
    return SimpleNamespace(match_dist=120, require_person=False, lock_threshold=0.60,
                           unlock_threshold=0.35, ema_alpha=0.5, lost_grace_sec=0.8,
                           person_aim_x=0.5, person_aim_y=0.5)


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
check(r.person_bbox == (302, 137, 40, 90), "selected person bbox is exposed separately for zoom")

r2 = f.update([_blob(40, 40, area=12000)], [_person(320, 180)])
check(r2.target_xy[0] > 250 and r2.locked, "locked near-person not stolen by a far blob")

r3 = f.update([_blob(322, 182)], [])
check(r3.target_xy[0] > 250 and r3.locked, "color-only carries lock through YOLO dropout")
check(r3.person_bbox is None, "color-only lock carry does not expose color bbox as person bbox")

g = Fusion(_cfg())
ro = g.update([], [_person(100, 100, 0.9)])
check(ro.conf == 0.2 and not ro.locked, "person-only stays weak (no lock)")

head_cfg = _cfg()
head_cfg.person_aim_y = 0.25
h = Fusion(head_cfg)
rh = h.update([_blob(320, 180)], [_person(320, 180)])
check(rh.target_xy[1] < 170, "person aim y=0.25 targets upper body/head instead of box center")


def test_require_person_rejects_single_source_until_color_and_person_match():
    cfg = _cfg()
    cfg.require_person = True
    f = Fusion(cfg)

    color_only = f.update([_blob(320, 180)], [])
    person_only = f.update([], [_person(320, 180)])
    confirmed = f.update([_blob(320, 180)], [_person(322, 182)])

    assert color_only.state == "SEARCHING"
    assert color_only.target_xy is None
    assert color_only.person_bbox is None
    assert person_only.state == "SEARCHING"
    assert person_only.target_xy is None
    assert confirmed.state == "TRACKING"
    assert confirmed.matched is True
    assert confirmed.person_bbox == (302, 137, 40, 90)


print("\nALL %d CHECKS PASSED (cv2-free)" % _n)
