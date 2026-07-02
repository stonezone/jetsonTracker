"""H5 (audit 2026-07-01): the dropout grace must actually hold the lock.

The old order applied the unlock threshold BEFORE computing coasting, so one
blank frame (conf==0 < unlock) unlocked instantly and the COASTING state was
dead code — the dominant lock-churn mechanism at range (splash/whitewater
frames where both the blob and the person box are missed).

Semantics locked down here:
  - a single blank frame keeps the lock and yields COASTING
  - the lock survives the whole lost_grace_sec window, then unlocks
  - a present-but-weak CANDIDATE (person-only, 0.2 < unlock) still unlocks
    immediately — grace is only for MISSING candidates
  - M6: a stale tracker id does not outlive the grace expiry
"""
from __future__ import annotations

import types

from wavecam import fusion
from wavecam.fusion import Fusion


def _cfg():
    return types.SimpleNamespace(lock_threshold=0.6, unlock_threshold=0.35,
                                 require_person=False, match_dist=120,
                                 person_aim_x=0.5, person_aim_y=0.5,
                                 ema_alpha=0.5, lost_grace_sec=0.8)


def _blob(cx=320.0, cy=240.0):
    return types.SimpleNamespace(cx=cx, cy=cy,
                                 bbox=(int(cx) - 10, int(cy) - 10, 20, 20))


def _person(cx=320.0, cy=240.0, conf=0.9, track_id=None):
    return types.SimpleNamespace(center=(cx, cy),
                                 xywh=(int(cx) - 20, int(cy) - 45, 40, 90),
                                 conf=conf, track_id=track_id)


def _clock(monkeypatch, start=1000.0):
    t = [start]
    monkeypatch.setattr(fusion, "time", types.SimpleNamespace(time=lambda: t[0]))
    return t


def _locked_fusion(monkeypatch, track_id=None):
    t = _clock(monkeypatch)
    f = Fusion(_cfg())
    r = f.update([_blob()], [_person(track_id=track_id)])
    assert r.locked and r.state == "TRACKING", "setup: matched person must lock"
    return f, t


def test_single_blank_frame_keeps_lock_and_reaches_coasting(monkeypatch):
    f, t = _locked_fusion(monkeypatch)
    t[0] += 0.1
    r = f.update([], [])
    assert r.locked is True, "one blank frame must not unlock"
    assert r.state == "COASTING", "COASTING must be reachable (was dead code)"
    assert r.target_xy is not None, "the servo keeps steering at the EMA"


def test_lock_holds_through_grace_then_unlocks(monkeypatch):
    f, t = _locked_fusion(monkeypatch)
    t[0] += 0.7                      # inside lost_grace_sec=0.8
    r = f.update([], [])
    assert r.locked and r.state == "COASTING"
    t[0] += 0.3                      # 1.0 s since last seen — grace expired
    r = f.update([], [])
    assert r.locked is False
    assert r.state == "SEARCHING"
    assert f._ema is None, "EMA is wiped only on grace expiry, not during it"


def test_weak_candidate_unlocks_immediately(monkeypatch):
    """A candidate BELOW unlock_threshold (person-only, 0.2) is evidence the
    subject is gone — no grace ride for it. (A person NEAR the track sustains
    at 0.45; use a far one so it scores CONF_PERSON_ONLY.)"""
    f, t = _locked_fusion(monkeypatch)
    t[0] += 0.1
    r = f.update([], [_person(cx=600.0, cy=100.0, conf=0.9)])
    assert r.conf == fusion.CONF_PERSON_ONLY
    assert r.locked is False, "a weak candidate must unlock instantly"


def test_ema_preserved_during_grace(monkeypatch):
    f, t = _locked_fusion(monkeypatch)
    ema_before = f._ema
    t[0] += 0.2
    f.update([], [])
    assert f._ema == ema_before, "the grace window must not wipe the EMA"


def test_track_id_cleared_on_grace_expiry(monkeypatch):
    """M6: _last_track_id must reset with the lock, or a recycled ByteTrack id
    (e.g. a beach walker inheriting it after a long dropout) steals the lock."""
    f, t = _locked_fusion(monkeypatch, track_id=7)
    assert f._last_track_id == 7
    t[0] += 1.0                      # past grace
    f.update([], [])
    assert f._last_track_id is None
