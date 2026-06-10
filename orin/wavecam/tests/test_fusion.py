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


# ---------------------------------------------------------------------------
# P2: GPS-cue confidence boost
# ---------------------------------------------------------------------------

def _cfg_with_boost(gps_boost=0.2, radius_frac=0.25):
    cfg = _cfg()
    cfg.gps_boost = gps_boost
    cfg.gps_boost_radius_frac = radius_frac
    return cfg


def test_gps_cue_center_blob_locks():
    """Color-only blob near cue center: 0.45 + 0.2 = 0.65 >= lock_threshold 0.6."""
    f = Fusion(_cfg_with_boost())
    # Frame 640x480; cue at center (320, 240) radius 120
    cue = (320.0, 240.0, 120.0)
    r = f.update([_blob(320, 240)], [], gps_cue_px=cue)
    assert r.conf >= 0.6, f"expected conf >= 0.6, got {r.conf}"
    assert r.locked, "blob within cue should lock"


def test_gps_cue_off_cue_blob_stays_045():
    """Color-only blob outside cue radius stays at 0.45 (no boost)."""
    f = Fusion(_cfg_with_boost())
    # Blob at (10, 10), cue at (320, 240) radius 120 — clearly outside
    cue = (320.0, 240.0, 120.0)
    r = f.update([_blob(10, 10)], [], gps_cue_px=cue)
    assert abs(r.conf - 0.45) < 1e-6, f"expected 0.45, got {r.conf}"
    assert not r.locked


def test_gps_cue_absent_stays_045():
    """No cue at all: color-only blob stays at 0.45 (no boost)."""
    f = Fusion(_cfg_with_boost())
    r = f.update([_blob(320, 240)], [], gps_cue_px=None)
    assert abs(r.conf - 0.45) < 1e-6, f"expected 0.45, got {r.conf}"
    assert not r.locked


def test_gps_cue_boost_caps_at_095():
    """Boost + base conf cannot exceed 0.95."""
    f = Fusion(_cfg_with_boost(gps_boost=0.9))
    cue = (320.0, 240.0, 120.0)
    r = f.update([_blob(320, 240)], [], gps_cue_px=cue)
    assert r.conf <= 0.95, f"conf must be capped at 0.95, got {r.conf}"


def test_gps_cue_does_not_steal_existing_ema_track():
    """Once an EMA track is established, cue should not hijack it by stealing
    the continuity choice — the existing-EMA blob is chosen (anti-flip rule)."""
    f = Fusion(_cfg_with_boost())
    # Establish a locked track far from center
    locked_blob = _blob(50, 50)
    center_blob = _blob(320, 240)
    # Build up to lock via confirmed person
    p_near = _person(52, 52, 0.9)
    for _ in range(3):
        r = f.update([locked_blob], [p_near])
    assert r.locked, "setup: should be locked on far target"

    # Now drop YOLO; present both blobs with a cue at center
    cue = (320.0, 240.0, 120.0)
    r2 = f.update([locked_blob, center_blob], [], gps_cue_px=cue)
    # EMA is near (50,50), so continuity picks locked_blob, not center_blob
    assert r2.target_xy is not None
    assert r2.target_xy[0] < 200, (
        f"cue must not steal existing track: target_xy={r2.target_xy}"
    )


print("\nALL %d CHECKS PASSED (cv2-free)" % _n)
