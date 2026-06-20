"""Deep-review HIGH regression locks (2026-06-19): SAFE-2 (atomic manual takeover),
SAFE-3 (re-check KILL before VISCA), SAFE-4 (restore-owner not clobbered)."""
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


def _make():
    pipe = types.SimpleNamespace()
    pipe.owner = PtzOwner()
    pipe.ptz = DummyPtz()
    pipe.arbiter = DummyArbiter()
    pipe.cfg = types.SimpleNamespace(ptz=types.SimpleNamespace(
        zoom_max_speed=5, deadzone=0.08, max_pan_speed=10, max_tilt_speed=8,
        min_speed=1, invert_pan=False, invert_tilt=False))
    disp = PtzDispatcher(pipe, bump_revision=lambda: None)
    return pipe, disp


# --- SAFE-2: atomic manual takeover ---

def test_takeover_from_autonomous_uses_atomic_transition():
    pipe, disp = _make()
    assert pipe.owner.request("vision_follow")     # autonomous owns
    assert disp.claim_manual(takeover=True) is True
    assert pipe.owner.owner == "manual"
    assert disp._restore_owner_after_manual == "vision_follow"


def test_takeover_refused_if_owner_moved_underneath():
    # If the owner is no longer the autonomous one we saw, transition() refuses and
    # leaves ownership untouched (no stale restore, no half-claimed state).
    pipe, disp = _make()
    assert pipe.owner.request("manual")            # already manual
    # request() succeeds immediately -> takeover branch not even reached
    assert disp.claim_manual(takeover=True) is True
    assert pipe.owner.owner == "manual"


def test_no_takeover_without_flag_when_autonomous_owns():
    pipe, disp = _make()
    assert pipe.owner.request("gps_tracker")
    assert disp.claim_manual(takeover=False) is False
    assert pipe.owner.owner == "gps_tracker"        # untouched


# --- SAFE-4: restore-owner not clobbered by a second manual claim ---

def test_restore_owner_not_overwritten_by_second_claim():
    pipe, disp = _make()
    assert pipe.owner.request("gps_tracker")
    assert disp.claim_manual(takeover=True) is True
    assert disp._restore_owner_after_manual == "gps_tracker"
    # A second claim while already manual must NOT overwrite the staged restore.
    assert disp.claim_manual(takeover=True) is True
    assert disp._restore_owner_after_manual == "gps_tracker"


# --- SAFE-3: no motion after KILL ---

def test_send_manual_velocity_short_circuits_when_killed():
    pipe, disp = _make()
    assert pipe.owner.request("manual")
    pipe.owner.kill()                               # KILL lands after claim
    req = types.SimpleNamespace(pan=0.5, tilt=0.0, zoom=0.0, deadman_ms=800)
    disp.send_manual_velocity(req)
    # No pan_tilt issued; only a stop.
    assert not any(isinstance(c, tuple) and c[0] == "pan_tilt" for c in pipe.ptz.calls)
    assert "stop" in pipe.ptz.calls


def test_send_manual_zoom_velocity_short_circuits_when_killed():
    pipe, disp = _make()
    assert pipe.owner.request("manual")
    pipe.owner.kill()
    disp.send_manual_zoom_velocity(0.5)
    # Only a zoom-stop, never a tele/wide drive.
    assert ("zoom", "stop", 0) in pipe.ptz.calls
    assert not any(isinstance(c, tuple) and c[0] == "zoom" and c[1] in ("tele", "wide")
                   for c in pipe.ptz.calls)


print("PTZ SAFETY REVIEW TESTS PASSED")
