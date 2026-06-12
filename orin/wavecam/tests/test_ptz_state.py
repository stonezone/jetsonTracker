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


def test_zoom_polled_every_nth_cycle_and_cached():
    from wavecam.ptz_state import ZOOM_POLL_EVERY_N
    zooms = [4100, 4200]
    ptz = types.SimpleNamespace(
        inquire_pan_tilt=lambda: (100, 0),
        inquire_zoom=lambda: zooms.pop(0) if zooms else None,
    )
    ps = PtzState(ptz, poll_hz=1000)
    assert ps.latest_zoom() == (None, None)
    for _ in range(ZOOM_POLL_EVERY_N):          # one full cycle worth
        ps._poll_once()
        ps._cycle += 1
        if ps._cycle % ZOOM_POLL_EVERY_N == 0:
            ps._poll_zoom_once()
    z, age = ps.latest_zoom()
    assert z == 4100 and age is not None and age < 1.0
