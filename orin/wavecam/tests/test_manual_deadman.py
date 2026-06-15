"""Phase-0 regression lock (Backend Plan v3): the manual deadman timer releases
manual ownership when it expires, and a stale-generation expiry is ignored.

Locks current behavior (the manual hold mechanism already exists as the per-request
deadman_ms, 100-5000ms, default 800). No behavior change.
"""
from __future__ import annotations
import types

from wavecam.control_ptz import PtzDispatcher
from wavecam.ptz_owner import PtzOwner


class DummyPtz:
    def __init__(self):
        self.calls = []

    def stop(self):
        self.calls.append("stop")

    def zoom(self, direction, speed=0):
        self.calls.append(("zoom", direction, speed))

    def pan_tilt(self, *args):
        self.calls.append(("pan_tilt", *args))


class DummyArbiter:
    def reset_vision_state(self):
        pass


def make_dispatcher():
    pipe = types.SimpleNamespace()
    pipe.owner = PtzOwner()
    pipe.ptz = DummyPtz()
    pipe.arbiter = DummyArbiter()
    pipe.cfg = types.SimpleNamespace(ptz=types.SimpleNamespace(zoom_max_speed=5))
    bumps = []
    disp = PtzDispatcher(pipe, bump_revision=lambda: bumps.append(1))
    return pipe, disp, bumps


def test_manual_deadman_expiry_releases_manual_and_stops():
    pipe, disp, bumps = make_dispatcher()
    assert pipe.owner.request("manual")
    disp._manual_pan_tilt_active = True
    generation = disp.schedule_manual_deadman(50)

    disp.manual_deadman_expired(generation)

    assert pipe.owner.owner == "idle"
    assert disp._manual_pan_tilt_active is False
    assert "stop" in pipe.ptz.calls
    assert bumps  # revision bumped so callers see the state change


def test_stale_generation_deadman_is_ignored():
    pipe, disp, _ = make_dispatcher()
    assert pipe.owner.request("manual")
    stale = disp.schedule_manual_deadman(50)
    disp.cancel_manual_deadman()  # bumps the generation, invalidating `stale`

    disp.manual_deadman_expired(stale)

    assert pipe.owner.owner == "manual"  # stale timer did not release


def test_deadman_noop_when_owner_not_manual():
    pipe, disp, _ = make_dispatcher()
    assert pipe.owner.request("vision_follow")
    generation = disp.schedule_manual_deadman(50)

    disp.manual_deadman_expired(generation)

    assert pipe.owner.owner == "vision_follow"  # autonomous owner untouched


if __name__ == "__main__":
    test_manual_deadman_expiry_releases_manual_and_stops()
    test_stale_generation_deadman_is_ignored()
    test_deadman_noop_when_owner_not_manual()
    print("MANUAL DEADMAN REGRESSION TESTS PASSED")
