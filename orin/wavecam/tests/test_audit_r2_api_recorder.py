"""Audit round-2 — recorder/media fixes.

R12 — /safety/kill's own response snapshot must not show media.recording=true
while the async teardown thread (M16) is still tearing ffmpeg down.

R13 — Recorder gained a lock around start/stop/status so a quick
RESUME -> media/start within the async-kill teardown window can't race
stop()'s bookkeeping-clear and drop the NEW recording's process handle.

R22 — Recorder.start() used to report {"ok": True, "started": False, ...}
when ffmpeg died instantly; the envelope's own ok field must agree with
started.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import types
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))
from test_control_api import DummyPipeline  # noqa: E402
from test_recorder import FakeProcess, FakePopenFactory, make_recorder  # noqa: E402

from wavecam.web import build_app  # noqa: E402


# ---------------------------------------------------------------------------
# R12 — kill response overlays recording:false + stopping:true
# ---------------------------------------------------------------------------

class _SlowRecorderStillReportingTrue:
    """Recorder-shaped fake whose status() keeps reporting recording=True
    until its (slow) stop() — invoked on the kill-teardown daemon thread —
    finishes. Stands in for the real ffmpeg terminate()+wait()+kill()
    sequence M16 made asynchronous."""

    def __init__(self, block_sec: float = 1.0):
        self._block_sec = block_sec
        self.stop_calls = 0
        self.config = types.SimpleNamespace(rec_dir=Path("/tmp/wavecam-test-recordings"))
        self.media = {
            "recording": True,
            "segment_name": None,
            "current_segment_name": None,
            "segment_pattern": "wavecam_p_%03d.mp4",
            "segment_prefix": "wavecam_p_",
            "free_gb": 100.0,
            "segments": 1,
            "latest": ["wavecam_p_000.mp4"],
        }

    def status(self):
        return dict(self.media)

    def stop(self):
        self.stop_calls += 1
        time.sleep(self._block_sec)
        self.media["recording"] = False
        return {"ok": True, "stopped": True}


def test_kill_response_overlays_recording_false_during_async_teardown():
    pipe = DummyPipeline()
    slow = _SlowRecorderStillReportingTrue(block_sec=1.0)
    pipe.recorder = slow
    client = TestClient(build_app(pipe))

    resp = client.post("/api/v1/safety/kill", json={"reason": "test"})
    body = resp.json()

    assert resp.status_code == 200
    assert body["ok"] is True
    # R12: even though the recorder's own status() still says recording=True
    # (the daemon-thread teardown is still mid-sleep — stop_calls may not
    # even have incremented yet), THIS response must not show it.
    assert body["status"]["media"]["recording"] is False
    assert body["status"]["media"].get("stopping") is True


def test_kill_response_still_shows_true_recording_gone_once_teardown_finishes():
    """Sanity: the overlay is specific to the KILL response itself — a later
    /media/status poll still reflects the recorder's real (by-then-converged)
    state, i.e. this isn't lying persistently, just at the moment of KILL."""
    pipe = DummyPipeline()
    slow = _SlowRecorderStillReportingTrue(block_sec=0.05)
    pipe.recorder = slow
    client = TestClient(build_app(pipe))

    client.post("/api/v1/safety/kill", json={"reason": "test"})

    deadline = time.monotonic() + 2.0
    while slow.stop_calls < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.1)  # let the teardown thread finish flipping media["recording"]

    later = client.get("/api/v1/media/status").json()
    assert later["recording"] is False


# ---------------------------------------------------------------------------
# R13 — Recorder start/stop/status lock; stop() drops _proc only by identity
# ---------------------------------------------------------------------------

class _SlowTerminateProcess:
    """Simulates a real ffmpeg process whose wait() after terminate() takes a
    noticeable amount of time (the real ffmpeg terminate()+wait(stop_timeout)
    path), giving a concurrent start() a genuine window to race stop() —
    exactly the "KILL -> quick RESUME -> media/start" scenario R13 fixes."""

    def __init__(self, wait_delay: float):
        self._running = True
        self.wait_delay = wait_delay
        self.terminated = False

    def poll(self):
        return None if self._running else 0

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        time.sleep(self.wait_delay)
        self._running = False
        return 0

    def kill(self):
        self._running = False


def test_stop_does_not_drop_a_concurrent_starts_process_handle(tmp_path):
    old_proc = _SlowTerminateProcess(wait_delay=0.3)
    new_proc_holder: dict = {}

    class SequencedPopen:
        def __init__(self):
            self.calls = 0

        def __call__(self, cmd, stdout=None, stderr=None):
            self.calls += 1
            if self.calls == 1:
                return old_proc
            new_proc = FakeProcess(running=True)
            new_proc_holder["proc"] = new_proc
            return new_proc

    recorder = make_recorder(tmp_path, SequencedPopen())
    recorder.start()  # spawns old_proc

    results: dict = {}

    def do_stop():
        results["stop"] = recorder.stop()

    t = threading.Thread(target=do_stop)
    t.start()
    time.sleep(0.05)  # let stop() acquire the lock and be mid-wait() on old_proc

    start_begin = time.monotonic()
    results["start"] = recorder.start()  # the "quick RESUME -> media/start"
    start_elapsed = time.monotonic() - start_begin
    t.join(timeout=2.0)

    assert not t.is_alive()
    assert start_elapsed >= 0.2, (
        "start() should have blocked on the recorder lock until the concurrent "
        "stop() finished, not raced it"
    )
    assert results["start"]["ok"] is True
    assert results["start"]["started"] is True
    assert recorder.is_running() is True
    assert recorder._proc is new_proc_holder["proc"], (
        "R13: the NEW recording's process handle must not be dropped by the "
        "older stop() call's bookkeeping-clear"
    )


def test_stop_only_clears_proc_matching_the_one_it_terminated(tmp_path):
    """Direct, single-threaded exercise of the identity-guarded clear: if
    self._proc is swapped out from under a stop() call while it's blocked in
    wait() (defense-in-depth against any future caller that mutates _proc
    without holding the lock — see the R13 comment in recorder.py), stop()
    must NOT null out the new one."""
    recorder = make_recorder(tmp_path, FakePopenFactory())
    recorder.start()
    original_proc = recorder._proc
    assert original_proc is not None
    replacement = FakeProcess(running=True)

    class SwapDuringWait:
        """Delegates everything to original_proc except wait(), which — as a
        side effect, standing in for a concurrent start() landing while this
        stop() call is blocked inside the real ffmpeg wait(timeout=...) —
        swaps recorder._proc out before returning."""

        def __getattr__(self, name):
            return getattr(original_proc, name)

        def wait(self, timeout=None):
            recorder._proc = replacement
            return original_proc.wait(timeout=timeout)

    recorder._proc = SwapDuringWait()

    result = recorder.stop()

    assert result["ok"] is True
    assert original_proc.terminated is True
    assert recorder._proc is replacement, (
        "R13: stop() cleared a proc it did not itself terminate/wait on"
    )
    assert recorder.is_running() is True


# ---------------------------------------------------------------------------
# R22 — Recorder.start() ok field agrees with started on instant ffmpeg death
# ---------------------------------------------------------------------------

def test_recorder_start_returns_ok_false_when_ffmpeg_dies_instantly(tmp_path):
    process = FakeProcess(running=False)  # poll() != None immediately => "died instantly"
    recorder = make_recorder(tmp_path, FakePopenFactory(process))

    result = recorder.start()

    assert result["ok"] is False
    assert result["started"] is False
    assert "error" in result
    assert recorder.is_running() is False


def test_media_record_start_route_503s_on_real_recorders_instant_death(tmp_path):
    """End-to-end: wire the REAL Recorder (not a hand-rolled dict) into the
    control API and confirm the route still surfaces 503/ok:false, now backed
    by a Recorder.start() that itself reports ok:false (R22) rather than
    relying solely on the route's started-is-False special case (REC-1)."""
    from wavecam.recorder import Recorder, RecorderConfig

    pipe = DummyPipeline()
    process = FakeProcess(running=False)
    pipe.recorder = Recorder(
        RecorderConfig(rec_dir=tmp_path, segment_seconds=60),
        popen=FakePopenFactory(process),
        now=lambda: "20260701_000000",
    )
    client = TestClient(build_app(pipe))

    resp = client.post("/api/v1/media/record/start", json={})

    assert resp.status_code == 503
    body = resp.json()
    assert body["ok"] is False
    assert body["code"] == "recording_failed"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
