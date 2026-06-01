from __future__ import annotations
import sys
import types

sys.modules.setdefault("cv2", types.SimpleNamespace())

from wavecam.pipeline import Pipeline, SharedState
from wavecam.ptz_owner import PtzOwner


class DummyPtz:
    def __init__(self):
        self.calls = []

    def stop(self):
        self.calls.append("stop")

    def zoom(self, direction):
        self.calls.append(("zoom", direction))


def make_pipeline(ptz_enabled=True):
    pipe = Pipeline.__new__(Pipeline)
    pipe.cfg = types.SimpleNamespace(ptz=types.SimpleNamespace(enabled=ptz_enabled))
    pipe.ptz = DummyPtz()
    pipe.state = SharedState()
    pipe.owner = PtzOwner()
    return pipe


def test_kill_stops_camera_and_sets_status():
    pipe = make_pipeline()
    pipe.owner.request("testbed")

    pipe.kill(True)

    assert pipe.owner.owner == "idle"
    assert pipe.owner.killed is True
    assert pipe.state.get_status()["state"] == "KILLED"
    assert pipe.state.get_status()["killed"] is True
    assert pipe.ptz.calls == ["stop", ("zoom", "stop")]


def test_resume_clears_killed_status_without_waiting_for_next_frame():
    pipe = make_pipeline()
    pipe.kill(True)

    pipe.kill(False)

    status = pipe.state.get_status()
    assert status["killed"] is False
    assert status["state"] == "SEARCHING"
    assert pipe.owner.killed is False
    assert pipe.owner.owner == "testbed"


if __name__ == "__main__":
    test_kill_stops_camera_and_sets_status()
    test_resume_clears_killed_status_without_waiting_for_next_frame()
    print("PIPELINE KILL TESTS PASSED")
