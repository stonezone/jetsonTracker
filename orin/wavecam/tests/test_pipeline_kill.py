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


if __name__ == "__main__":
    test_kill_stops_camera_and_sets_status()
    test_resume_clears_killed_status_without_waiting_for_next_frame()
    test_pipeline_repeats_stop_commands_when_stop_state_persists()
    print("PIPELINE KILL TESTS PASSED")
