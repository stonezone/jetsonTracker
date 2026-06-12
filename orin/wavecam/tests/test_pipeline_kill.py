from __future__ import annotations
import sys
import types

sys.modules.setdefault("cv2", types.SimpleNamespace())

from wavecam.controller import STOP_CMD
from wavecam.events import EventRing
from wavecam.pipeline import Pipeline, SharedState
from wavecam.ptz_owner import PtzOwner


class DummyPtz:
    def __init__(self):
        self.calls = []

    def stop(self):
        self.calls.append("stop")

    def zoom(self, direction, speed=0):
        self.calls.append(("zoom", direction, speed))


def make_pipeline(ptz_enabled=True):
    pipe = Pipeline.__new__(Pipeline)
    pipe.cfg = types.SimpleNamespace(
        ptz=types.SimpleNamespace(
            enabled=ptz_enabled,
            command_min_interval=0.0,
            stop_resend_interval=0.0,
        )
    )
    pipe.ptz = DummyPtz()
    pipe.state = SharedState()
    pipe.owner = PtzOwner()
    pipe._last_cmd_key = None
    pipe._last_cmd_time = 0.0
    pipe._last_zoom_key = None
    pipe._last_zoom_time = 0.0
    pipe.events = EventRing()
    pipe.ptz_state = types.SimpleNamespace(
        start=lambda: None, latest=lambda: (None, None),
        latest_zoom=lambda: (None, None),
        is_alive=lambda: False, stop=lambda: None)
    from wavecam.pointing_verifier import PointingVerifier
    pipe._pointing_verifier = PointingVerifier(
        pipe.ptz, pipe.ptz_state, pipe.events,
        blocked=lambda: pipe.owner.killed or pipe.owner.owner != "gps_tracker")
    return pipe


def test_kill_stops_camera_and_sets_status():
    pipe = make_pipeline()
    pipe.owner.request("testbed")

    pipe.kill(True)

    assert pipe.owner.owner == "idle"
    assert pipe.owner.killed is True
    assert pipe.state.get_status()["state"] == "KILLED"
    assert pipe.state.get_status()["killed"] is True
    assert pipe.ptz.calls == ["stop", ("zoom", "stop", 0)]


def test_resume_clears_killed_status_without_waiting_for_next_frame():
    pipe = make_pipeline()
    pipe.kill(True)

    pipe.kill(False)

    status = pipe.state.get_status()
    assert status["killed"] is False
    assert status["state"] == "SEARCHING"
    assert pipe.owner.killed is False
    assert pipe.owner.owner == "testbed"


def test_pipeline_repeats_stop_commands_when_stop_state_persists():
    pipe = make_pipeline()

    pipe._send_cmd(STOP_CMD)
    pipe._send_cmd(STOP_CMD)
    pipe._send_zoom("stop")
    pipe._send_zoom("stop")

    assert pipe.ptz.calls == ["stop", "stop", ("zoom", "stop", 0), ("zoom", "stop", 0)]


class _CrashingGrab:
    def __init__(self):
        self.stopped = False

    def start(self):
        pass

    def read(self):
        raise RuntimeError("frame source exploded")

    def stop(self):
        self.stopped = True


def test_run_stops_ptz_even_when_the_loop_crashes():
    import threading

    import pytest

    pipe = make_pipeline()
    pipe.cfg.loop = types.SimpleNamespace(target_fps=30, log_every_sec=10)
    pipe.grab = _CrashingGrab()
    pipe.start_paused = False
    pipe._stop = threading.Event()
    pipe._shadow_writer = None
    pipe.estimator = None

    with pytest.raises(RuntimeError):
        pipe.run()

    assert "stop" in pipe.ptz.calls          # camera halted despite the crash
    assert pipe.grab.stopped is True
    assert pipe.owner.owner == "idle"


if __name__ == "__main__":
    test_kill_stops_camera_and_sets_status()
    test_resume_clears_killed_status_without_waiting_for_next_frame()
    test_pipeline_repeats_stop_commands_when_stop_state_persists()
    print("PIPELINE KILL TESTS PASSED")


def test_kill_clears_pending_pointing_verify():
    """Reviewer C1: a KILL during the settle window must drop the pending
    verify so the verifier can never re-issue the move after resume."""
    pipe = make_pipeline()
    pipe.owner.request("gps_tracker")
    pipe._pointing_verifier.record_move(pan_enc=1000, tilt_enc=0)

    pipe.kill(True)
    assert pipe._pointing_verifier._target is None

    pipe.kill(False)                       # resume
    pipe._pointing_verifier.tick()         # nothing pending, nothing blocked-cleared
    assert pipe.ptz.calls.count("stop") >= 1
    assert not any(c[0] == "abs" for c in pipe.ptz.calls if isinstance(c, tuple))
