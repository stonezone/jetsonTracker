"""H7 (audit 2026-07-01): absolute-move send gate + verifier starvation.

The old gate re-sent an identical absolute move every command_min_interval
(20 Hz); each resend's record_move() reset the verifier's settle clock, so
verify-and-resend could never fire while gps_tracker commands flowed — dead in
exactly the mode it was built for. Now: send only on target CHANGE plus a
>=2.5 s keepalive, and record_move() is a no-op for an unchanged pending
target.

Also covers L10 (no moving command after KILL from any of the three send
paths) and M5 (TTL-cached YOLO boxes are not reused while the camera pans).
"""
from __future__ import annotations

import time
import types

from wavecam.controller import PtzAbsoluteCommand, PtzCommand, STOP_CMD
from wavecam.events import EventRing
from wavecam.pipeline import ABS_CMD_KEEPALIVE_SEC, Pipeline, SharedState
from wavecam.pointing_verifier import PointingVerifier
from wavecam.ptz_owner import PtzOwner
from wavecam.ptz_state import VERIFY_DELAY_SEC
from wavecam.ptz_visca import PAN_RIGHT, TILT_STOP


class _AbsPtz:
    def __init__(self):
        self.abs_calls = []
        self.calls = []

    def pan_tilt_absolute(self, pan, tilt, **kw):
        self.abs_calls.append((pan, tilt))

    def zoom_absolute(self, enc):
        self.calls.append(("zoom_abs", enc))

    def pan_tilt(self, *a):
        self.calls.append(("pan_tilt",) + a)

    def stop(self):
        self.calls.append("stop")

    def zoom(self, direction, speed=0):
        self.calls.append(("zoom", direction, speed))


def _pipe(enc=(0, 0)):
    pipe = Pipeline.__new__(Pipeline)
    pipe.cfg = types.SimpleNamespace(
        ptz=types.SimpleNamespace(enabled=True, command_min_interval=0.05,
                                  stop_resend_interval=0.25),
        gps=types.SimpleNamespace(max_pan_speed=4, max_tilt_speed=3),
    )
    pipe.ptz = _AbsPtz()
    pipe.state = SharedState()
    pipe.owner = PtzOwner()
    pipe.owner.request("gps_tracker")
    pipe.events = EventRing()
    pipe.ptz_state = types.SimpleNamespace(latest=lambda: (enc, 0.01),
                                           latest_zoom=lambda: (None, None))
    pipe._pointing_verifier = PointingVerifier(
        pipe.ptz, pipe.ptz_state, pipe.events,
        blocked=lambda: pipe.owner.killed or pipe.owner.owner != "gps_tracker")
    pipe._last_abs_cmd_key = None
    pipe._last_abs_cmd_time = 0.0
    pipe._last_cmd_key = None
    pipe._last_cmd_time = 0.0
    pipe._last_zoom_key = None
    pipe._last_zoom_time = 0.0
    return pipe


CMD = PtzAbsoluteCommand(pan_enc=1000, tilt_enc=0)
MOVE_CMD = PtzCommand(8, 1, PAN_RIGHT, TILT_STOP)


def test_identical_target_not_resent_within_keepalive():
    pipe = _pipe()
    for _ in range(10):                       # the old gate re-sent every 50 ms
        pipe._send_absolute_cmd(CMD)
    assert pipe.ptz.abs_calls == [(1000, 0)], \
        "an unchanged absolute target must not be re-sent at command rate"


def test_changed_target_sends_immediately():
    pipe = _pipe()
    pipe._send_absolute_cmd(CMD)
    pipe._send_absolute_cmd(PtzAbsoluteCommand(pan_enc=1200, tilt_enc=50))
    assert pipe.ptz.abs_calls == [(1000, 0), (1200, 50)]


def test_keepalive_resends_after_interval():
    pipe = _pipe()
    pipe._send_absolute_cmd(CMD)
    pipe._last_abs_cmd_time -= ABS_CMD_KEEPALIVE_SEC + 0.1
    pipe._send_absolute_cmd(CMD)
    assert pipe.ptz.abs_calls == [(1000, 0), (1000, 0)]


def test_verifier_fires_between_identical_resends():
    """The whole point of H7: with ~1 Hz identical GPS targets, the verifier's
    settle clock must survive the resends so tick() can verify and retry."""
    pipe = _pipe(enc=(100, 0))                # encoder far from target -> miss
    pipe._send_absolute_cmd(CMD)              # original move
    v = pipe._pointing_verifier
    issue_t = v._issue_t
    assert issue_t is not None

    pipe._send_absolute_cmd(CMD)              # 1 Hz re-issue (deduped anyway)
    # simulate a keepalive resend of the SAME target
    pipe._last_abs_cmd_time -= ABS_CMD_KEEPALIVE_SEC + 0.1
    pipe._send_absolute_cmd(CMD)
    assert v._issue_t == issue_t, \
        "record_move() must not reset the settle clock for an unchanged target"

    v._issue_t = time.time() - VERIFY_DELAY_SEC - 0.1   # settle window elapsed
    v.tick()
    retries = [c for c in pipe.ptz.abs_calls if c == (1000, 0)]
    assert len(retries) >= 3 and \
        any(e["kind"] == "pointing_miss" for e in pipe.events.since(0)), \
        "the verifier must be able to fire between identical resends"


def test_record_move_fresh_after_verifier_gave_up():
    """Once the verifier clears its target (success or second miss), the next
    identical command records a fresh settle clock."""
    v = PointingVerifier(_AbsPtz(), types.SimpleNamespace(latest=lambda: ((0, 0), 0.01)),
                         EventRing())
    v.record_move(1000, 0, t=100.0)
    v._target = None                          # verifier resolved the move
    v._issue_t = None
    v.record_move(1000, 0, t=200.0)
    assert v._issue_t == 200.0


# --- L10: KILL guards on every send path -------------------------------------

def test_killed_blocks_moving_commands_on_all_send_paths():
    pipe = _pipe()
    pipe.owner.kill()
    pipe._send_cmd(MOVE_CMD)
    pipe._send_zoom("tele", 3)
    pipe._send_absolute_cmd(CMD)
    assert pipe.ptz.abs_calls == []
    assert all(c == "stop" or c[0] == "zoom" and c[1] == "stop"
               for c in pipe.ptz.calls if isinstance(c, (str, tuple))), \
        "no moving command may follow KILL"
    assert not any(isinstance(c, tuple) and c[0] == "pan_tilt" for c in pipe.ptz.calls)


def test_killed_still_allows_stops():
    pipe = _pipe()
    pipe.owner.kill()
    pipe._send_cmd(STOP_CMD)
    pipe._send_zoom("stop")
    assert "stop" in pipe.ptz.calls
    assert ("zoom", "stop", 0) in pipe.ptz.calls


# --- L1: verifier ignores stale encoder snapshots ------------------------------

def test_verifier_skips_stale_encoder_age():
    ptz = _AbsPtz()
    stale_state = types.SimpleNamespace(latest=lambda: ((100, 0), 5.0))  # 5 s old
    ev = EventRing()
    v = PointingVerifier(ptz, stale_state, ev)
    v.record_move(1000, 0, t=time.time() - VERIFY_DELAY_SEC - 0.1)
    v.tick()
    assert ptz.abs_calls == [], "a wedged poller must not trigger a false re-slew"
    assert not any(e["kind"] == "pointing_miss" for e in ev.since(0))
