"""Phase-0 regression lock (Backend Plan v3): CALIBRATE outranks MANUAL and all
autonomy, and a manual release restores a calibrate session (the B13/4a25265 fix)
without resurrecting an autonomous owner.

These lock *current* behavior verified in the 2026-06-15 ground-check — no behavior
change. They guard the v3 Phase-6 authority refactor from regressing the safety
ordering. CALIBRATE is deliberately NOT in ptz_owner.AUTONOMOUS, so manual takeover
(which only displaces AUTONOMOUS owners) can never preempt a calibrate session.
"""
from __future__ import annotations
import types

from wavecam.control_ptz import PtzDispatcher
from wavecam.ptz_owner import CALIBRATE, PtzOwner


class DummyPtz:
    def __init__(self):
        self.calls = []

    def stop(self):
        self.calls.append("stop")

    def zoom(self, direction, speed=0):
        self.calls.append(("zoom", direction, speed))

    def pan_tilt(self, *args):
        self.calls.append(("pan_tilt", *args))

    def home(self):
        self.calls.append("home")


class DummyArbiter:
    def __init__(self):
        self.reset_called = 0

    def reset_vision_state(self):
        self.reset_called += 1


class DummyState:
    def __init__(self):
        self.status = {}

    def set_status(self, **kw):
        self.status.update(kw)


def make_dispatcher():
    pipe = types.SimpleNamespace()
    pipe.owner = PtzOwner()
    pipe.ptz = DummyPtz()
    pipe.state = DummyState()
    pipe.arbiter = DummyArbiter()
    pipe.cfg = types.SimpleNamespace(ptz=types.SimpleNamespace(zoom_max_speed=5))
    disp = PtzDispatcher(pipe, bump_revision=lambda: None)
    return pipe, disp


def test_owner_rejects_manual_while_calibrate_holds():
    pipe, disp = make_dispatcher()
    assert pipe.owner.request(CALIBRATE)
    assert pipe.owner.owner == CALIBRATE
    # PtzOwner level: a plain manual request cannot steal calibrate.
    assert pipe.owner.request("manual") is False
    # Dispatcher level: even takeover=True cannot displace calibrate, because
    # CALIBRATE is not an AUTONOMOUS owner.
    assert disp.claim_manual(takeover=True) is False
    assert pipe.owner.owner == CALIBRATE


def test_calibrate_blocks_autonomous_start():
    pipe, disp = make_dispatcher()
    assert pipe.owner.request(CALIBRATE)
    assert disp.start_autonomous("vision_follow") is False
    assert disp.start_autonomous("gps_tracker") is False
    assert pipe.owner.owner == CALIBRATE


def test_claim_manual_takes_over_autonomous_only_with_takeover():
    pipe, disp = make_dispatcher()
    assert pipe.owner.request("vision_follow")
    assert disp.claim_manual(takeover=False) is False
    assert pipe.owner.owner == "vision_follow"
    assert disp.claim_manual(takeover=True) is True
    assert pipe.owner.owner == "manual"


def test_claim_manual_from_calibrate_is_atomic_and_stages_restore():
    # GLM A3: CALIBRATE→manual must use the atomic transition() (no transient IDLE
    # window the pipeline could seize), and stage CALIBRATE for restore on release.
    pipe, disp = make_dispatcher()
    assert pipe.owner.request(CALIBRATE)
    assert disp.claim_manual_from_calibrate() is True
    assert pipe.owner.owner == "manual"
    assert disp._restore_owner_after_manual == CALIBRATE
    # round-trip: releasing manual hands ownership back to CALIBRATE
    disp.release_manual_owner()
    assert pipe.owner.owner == CALIBRATE


def test_claim_manual_from_calibrate_refuses_when_not_calibrate():
    pipe, disp = make_dispatcher()
    assert pipe.owner.request("vision_follow")
    assert disp.claim_manual_from_calibrate() is False
    assert pipe.owner.owner == "vision_follow"   # untouched


def test_release_manual_restores_calibrate_session():
    # B13/4a25265: a standalone calibrate capture claims manual, then releasing
    # manual must hand ownership back to CALIBRATE.
    pipe, disp = make_dispatcher()
    pipe.owner.request("manual")
    disp._restore_owner_after_manual = CALIBRATE
    disp.release_manual_owner()
    assert pipe.owner.owner == CALIBRATE
    assert pipe.arbiter.reset_called >= 1


def test_release_manual_does_not_restore_autonomous_owner():
    # B13: autonomous owners are left for the arbiter to re-decide, not restored
    # directly (prevents a stale owner from instantly fighting the operator).
    pipe, disp = make_dispatcher()
    pipe.owner.request("manual")
    disp._restore_owner_after_manual = "vision_follow"
    disp.release_manual_owner()
    assert pipe.owner.owner == "idle"


def test_calibrate_cannot_be_restored_after_kill():
    # KILL latch wins: release during a kill must not re-grant calibrate.
    pipe, disp = make_dispatcher()
    pipe.owner.request("manual")
    disp._restore_owner_after_manual = CALIBRATE
    pipe.owner.kill()
    disp.release_manual_owner()
    assert pipe.owner.owner == "idle"
    assert pipe.owner.killed is True


def test_claim_manual_from_calibrate_stops_ptz_and_stages_restore():
    # H1/H2: the calibrate->manual standalone-capture takeover must run inside the
    # dispatcher lock (no external poke of _restore_owner_after_manual) AND stop
    # PTZ before the capture samples encoders, mirroring claim_manual.
    pipe, disp = make_dispatcher()
    assert pipe.owner.request(CALIBRATE)
    assert disp.claim_manual_from_calibrate() is True
    assert pipe.owner.owner == "manual"
    assert disp._restore_owner_after_manual == CALIBRATE
    # PTZ + zoom halted before sampling (H2)
    assert "stop" in pipe.ptz.calls
    assert ("zoom", "stop", 0) in pipe.ptz.calls
    # releasing manual restores the calibrate session (existing B13 contract)
    disp.release_manual_owner()
    assert pipe.owner.owner == CALIBRATE


def test_claim_manual_from_calibrate_refuses_when_not_calibrate():
    # Only valid from an active calibrate session; never displaces another owner.
    pipe, disp = make_dispatcher()
    assert pipe.owner.request("vision_follow")
    assert disp.claim_manual_from_calibrate() is False
    assert pipe.owner.owner == "vision_follow"


if __name__ == "__main__":
    test_owner_rejects_manual_while_calibrate_holds()
    test_calibrate_blocks_autonomous_start()
    test_claim_manual_takes_over_autonomous_only_with_takeover()
    test_release_manual_restores_calibrate_session()
    test_release_manual_does_not_restore_autonomous_owner()
    test_calibrate_cannot_be_restored_after_kill()
    print("CALIBRATE PRIORITY REGRESSION TESTS PASSED")
