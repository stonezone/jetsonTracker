"""Capture-path tests (audit 2026-07-01 M22 + C1).

capture.py is the exact component whose silent death produced two zombie-rig
field incidents, yet it had zero direct coverage. These tests drive the real
FrameGrabber thread against a fake cv2.VideoCapture (monkeypatched at the
module seam) — open failure, reconnect_sec, disconnect drop, and the
frame-freeze signature (frames counter stalls while read() stays non-None).

Plus the C1 regression: a video dropout mid-slew must stop the camera ONCE
per dropout instead of leaving it running at its last VISCA velocity.
"""
from __future__ import annotations

import threading
import time
import types

import numpy as np

import wavecam.capture as capture_mod
from wavecam.capture import FrameGrabber

FRAME = np.zeros((4, 4, 3), dtype=np.uint8)


def _cfg(reconnect_sec=0.01):
    return types.SimpleNamespace(source="rtsp://fake", use_gstreamer=False,
                                 codec="h264", reconnect_sec=reconnect_sec)


class _FakeCap:
    """Scripted cv2.VideoCapture: read() pops (ok, frame) results, then fails."""

    def __init__(self, reads=None, opened=True):
        self.opened = opened
        self.reads = list(reads or [])
        self.released = False

    def isOpened(self):
        return self.opened

    def set(self, *a):
        pass

    def read(self):
        if self.reads:
            return self.reads.pop(0)
        return (False, None)

    def release(self):
        self.released = True


def _patch_cv2(monkeypatch, factory):
    fake = types.SimpleNamespace(VideoCapture=factory,
                                 CAP_PROP_BUFFERSIZE=38,
                                 CAP_GSTREAMER=object())
    monkeypatch.setattr(capture_mod, "cv2", fake)


def _wait_for(cond, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.005)
    return False


def _run_grabber(g):
    g.start()
    return g


def test_open_failure_reports_disconnected_and_retries(monkeypatch):
    """A source that never opens: connected stays False, read() is None, and
    the grabber keeps retrying at reconnect_sec."""
    opens = []

    def factory(src, *a, **k):
        opens.append(src)
        return _FakeCap(opened=False)

    _patch_cv2(monkeypatch, factory)
    g = _run_grabber(FrameGrabber(_cfg(reconnect_sec=0.01)))
    try:
        assert _wait_for(lambda: len(opens) >= 3), "reconnect_sec retry loop never ran"
        assert g.connected is False
        assert g.read() is None
        assert g.frames == 0
    finally:
        g.stop()
        g.join(timeout=2)


def test_read_failure_drops_stale_frame_and_reconnects(monkeypatch):
    """CAP-1: on disconnect the stale frame must be dropped (read() -> None so
    the pipeline goes NO_VIDEO) and the source reopened after reconnect_sec."""
    caps = []

    def factory(src, *a, **k):
        # first cap: two good frames then dies; later caps: healthy
        if not caps:
            cap = _FakeCap(reads=[(True, FRAME), (True, FRAME)])
        else:
            cap = _FakeCap(reads=[(True, FRAME)] * 10_000)
        caps.append(cap)
        return cap

    _patch_cv2(monkeypatch, factory)
    g = _run_grabber(FrameGrabber(_cfg(reconnect_sec=0.01)))
    try:
        assert _wait_for(lambda: len(caps) >= 2), "grabber never reconnected"
        assert caps[0].released is True, "dead capture must be released"
        assert _wait_for(lambda: g.connected and g.read() is not None), \
            "no frames after reconnect"
        assert g.frames >= 2
    finally:
        g.stop()
        g.join(timeout=2)


def test_frame_freeze_stalls_frames_counter_while_read_stays_non_none(monkeypatch):
    """ZOMBIE-1 signature: a wedged capture (read() blocking) keeps handing the
    pipeline the same non-None frame while the frames counter stops advancing —
    the counter is the staleness signal, so it MUST stall."""
    release = threading.Event()

    class _FreezingCap(_FakeCap):
        def __init__(self):
            super().__init__()
            self.n = 0

        def read(self):
            self.n += 1
            if self.n <= 3:
                return (True, FRAME)
            release.wait(timeout=5.0)   # wedge until the test releases us
            return (False, None)

    _patch_cv2(monkeypatch, lambda *a, **k: _FreezingCap())
    g = _run_grabber(FrameGrabber(_cfg(reconnect_sec=0.01)))
    try:
        assert _wait_for(lambda: g.frames == 3), "never captured the initial frames"
        n0 = g.frames
        assert g.read() is not None, "wedged grabber still serves the last frame"
        time.sleep(0.05)
        assert g.frames == n0, "frames counter must stall while capture is wedged"
    finally:
        release.set()
        g.stop()
        g.join(timeout=2)


# ---------------------------------------------------------------------------
# C1 regression: video dropout must not leave the camera slewing
# ---------------------------------------------------------------------------

class _DummyPtz:
    def __init__(self):
        self.calls = []

    def stop(self):
        self.calls.append("stop")

    def zoom(self, direction, speed=0):
        self.calls.append(("zoom", direction, speed))

    def pan_tilt(self, *a):
        self.calls.append(("pan_tilt",) + a)


def _dropout_pipeline(ptz_enabled=True, owner="vision_follow"):
    from wavecam.events import EventRing
    from wavecam.pipeline import Pipeline, SharedState
    from wavecam.ptz_owner import PtzOwner

    pipe = Pipeline.__new__(Pipeline)
    pipe.cfg = types.SimpleNamespace(
        ptz=types.SimpleNamespace(enabled=ptz_enabled, command_min_interval=0.0,
                                  stop_resend_interval=0.0),
    )
    pipe.ptz = _DummyPtz()
    pipe.state = SharedState()
    pipe.owner = PtzOwner()
    if owner != "idle":
        pipe.owner.request(owner)
    pipe.events = EventRing()
    pipe._no_video_stopped = False
    # mid-slew: a moving velocity command is the last thing we sent
    pipe._last_cmd_key = (8, 4, 0x02, 0x01)
    pipe._last_cmd_time = time.time()
    pipe._last_zoom_key = ("tele", 3)
    pipe._last_zoom_time = time.time()
    return pipe


def test_no_video_stop_fires_once_per_dropout():
    pipe = _dropout_pipeline()
    for _ in range(5):                      # NO_VIDEO branch runs at 10 Hz
        pipe._stop_for_no_video()
    assert pipe.ptz.calls == ["stop", ("zoom", "stop", 0)], \
        "stop must be sent exactly once per dropout, not spammed"
    assert pipe._last_cmd_key is None       # next post-recovery cmd always sends
    events = [e["kind"] for e in pipe.events.since(0)]
    assert "no_video_stop" in events


def test_no_video_stop_rearms_after_recovery():
    pipe = _dropout_pipeline()
    pipe._stop_for_no_video()
    pipe._no_video_stopped = False          # what the loop does on a good frame
    pipe._stop_for_no_video()
    assert pipe.ptz.calls.count("stop") == 2


def test_no_video_stop_skips_manual_owner_and_disabled_ptz():
    manual = _dropout_pipeline(owner="idle")
    manual.owner.request("manual")
    manual._stop_for_no_video()
    assert manual.ptz.calls == [], "a manual aim is not ours to stop"

    off = _dropout_pipeline(ptz_enabled=False)
    off._stop_for_no_video()
    assert off.ptz.calls == []


def test_run_loop_stops_camera_on_none_frames():
    """End-to-end: the real _run loop with a grabber returning None while a
    velocity command was active sends exactly one stop pair."""
    pipe = _dropout_pipeline(owner="idle")   # _run claims testbed itself
    pipe.cfg.loop = types.SimpleNamespace(target_fps=30, log_every_sec=10)
    pipe.grab = types.SimpleNamespace(start=lambda: None, read=lambda: None,
                                      connected=False, stop=lambda: None)
    pipe.ptz_state = types.SimpleNamespace(start=lambda: None, stop=lambda: None,
                                           latest=lambda: (None, None),
                                           latest_zoom=lambda: (None, None),
                                           is_alive=lambda: False)
    pipe.start_paused = False
    pipe.estimator = None
    pipe._shadow_writer = None
    pipe._stop = threading.Event()

    t = threading.Thread(target=pipe.run, daemon=True)
    t.start()
    time.sleep(0.35)                         # several NO_VIDEO iterations
    pipe.stop()
    t.join(timeout=2)

    # one dropout stop (+ the _shutdown stop when the thread exits)
    zoom_stops = [c for c in pipe.ptz.calls if c == ("zoom", "stop", 0)]
    assert len(zoom_stops) == 1, "zoom stop must not be re-sent at 10 Hz"
    assert pipe.state.get_status()["state"] == "NO_VIDEO"
