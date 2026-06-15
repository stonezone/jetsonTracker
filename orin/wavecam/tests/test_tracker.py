"""Persistent track ID + fusion identity tests — no torch required (Plan v3 Phase 2).

Core identity-preference cases adapted from Kimi's Phase-B draft; extended with the
FusionResult.track_id + /status observability and the flag-off byte-identical guard.
"""
import sys
import types
from types import SimpleNamespace

sys.modules.setdefault("cv2", types.SimpleNamespace())

from wavecam.detector import PersonBox
from wavecam.fusion import Fusion


def _cfg():
    return SimpleNamespace(
        match_dist=120,
        require_person=False,
        lock_threshold=0.60,
        unlock_threshold=0.35,
        ema_alpha=0.5,
        lost_grace_sec=0.8,
        person_aim_x=0.5,
        person_aim_y=0.5,
        gps_boost=0.2,
        gps_boost_radius_frac=0.25,
    )


def _blob(cx, cy):
    return SimpleNamespace(cx=cx, cy=cy, area=5000,
                           bbox=(int(cx - 20), int(cy - 40), 40, 80), fill=0.9)


def _person(cx, cy, conf=0.85, track_id=None):
    return PersonBox(x1=cx - 20, y1=cy - 45, x2=cx + 20, y2=cy + 45,
                     conf=conf, track_id=track_id)


# --- track_id plumbing -------------------------------------------------------

def test_personbox_track_id_defaults_none():
    assert PersonBox(0, 0, 10, 10, 0.9).track_id is None


def test_personbox_track_id_preserved():
    assert PersonBox(0, 0, 10, 10, 0.9, track_id=7).track_id == 7


# --- fusion identity preference ---------------------------------------------

def test_fusion_prefers_same_track_id_over_nearer_person():
    f = Fusion(_cfg())
    r1 = f.update([_blob(320, 180)], [_person(320, 180, track_id=5)])
    assert r1.locked and r1.person_bbox is not None
    nearer = _person(200, 200, track_id=99)
    same = _person(322, 182, track_id=5)
    r2 = f.update([_blob(322, 182)], [nearer, same])
    assert r2.locked
    assert r2.person_bbox == same.xywh  # picked same id, not the nearer one


def test_fusion_continuity_without_track_id():
    f = Fusion(_cfg())
    assert f.update([_blob(320, 180)], [_person(320, 180)]).locked
    assert f.update([_blob(322, 182)], [_person(322, 182)]).locked


def test_fusion_stores_last_track_id():
    f = Fusion(_cfg())
    f.update([_blob(320, 180)], [_person(320, 180, track_id=5)])
    assert f._last_track_id == 5


def test_fusion_color_only_does_not_clear_track_id():
    f = Fusion(_cfg())
    f.update([_blob(320, 180)], [_person(320, 180, track_id=5)])
    f.update([_blob(322, 182)], [])  # YOLO dropout: color-only frame
    assert f._last_track_id == 5


# --- FusionResult + /status observability (added for v3) ---------------------

def test_fusion_result_exposes_track_id_when_tracking():
    f = Fusion(_cfg())
    r = f.update([_blob(320, 180)], [_person(320, 180, track_id=5)])
    assert r.locked and r.track_id == 5


def test_fusion_result_track_id_none_without_tracker():
    f = Fusion(_cfg())
    r = f.update([_blob(320, 180)], [_person(320, 180)])
    assert r.locked and r.track_id is None  # byte-identical path: no id surfaced


def test_build_tracking_exposes_track_id():
    from wavecam.control_snapshots import build_tracking
    assert build_tracking({"track_id": 7}).get("track_id") == 7
    assert build_tracking({}).get("track_id") is None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print("TRACKER TESTS PASSED (%d)" % len(fns))
