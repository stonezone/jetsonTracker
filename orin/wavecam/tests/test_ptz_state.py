"""Tests for the PtzState background poller.

Uses a fake transport: a callable that returns controlled values on demand,
without any real socket. The fake is deliberately simple — it only needs to
exercise the cache, staleness logic, and interleave/lost-reply handling.
"""
import threading
import time
import types

from wavecam.ptz_state import PtzState


def _make_fake_ptz(replies):
    """Returns a NullPtz-compatible object whose inquire_pan_tilt pops from
    the replies list. None = simulated timeout/loss."""
    q = list(replies)
    obj = types.SimpleNamespace(
        inquire_pan_tilt=lambda: q.pop(0) if q else None,
        inquire_zoom=lambda: None,
    )
    return obj


def test_cache_is_none_before_first_reply():
    ptz = _make_fake_ptz([None])
    ps = PtzState(ptz, poll_hz=100)
    enc, age = ps.latest()
    assert enc is None and age is None


def test_cache_holds_latest_valid_reply():
    ptz = _make_fake_ptz([(1000, -200), (1500, -250)])
    ps = PtzState(ptz, poll_hz=100)
    ps._poll_once()
    enc, age = ps.latest()
    assert enc == (1000, -200)
    assert age is not None and age < 0.5


def test_lost_reply_leaves_cache_intact():
    ptz = _make_fake_ptz([(500, 100), None])
    ps = PtzState(ptz, poll_hz=100)
    ps._poll_once()       # caches (500, 100)
    ps._poll_once()       # reply lost — cache must survive
    enc, age = ps.latest()
    assert enc == (500, 100)


def test_age_grows_after_last_valid():
    ptz = _make_fake_ptz([(0, 0)])
    ps = PtzState(ptz, poll_hz=100)
    ps._poll_once()
    time.sleep(0.05)
    _, age = ps.latest()
    assert age is not None and age >= 0.04


def test_start_stop_thread():
    ptz = _make_fake_ptz([(0, 0)] * 200)
    ps = PtzState(ptz, poll_hz=50)
    ps.start()
    time.sleep(0.1)
    ps.stop()
    enc, age = ps.latest()
    assert enc is not None
