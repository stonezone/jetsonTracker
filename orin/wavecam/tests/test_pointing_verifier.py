"""State-machine tests for verify-and-resend.

The verifier is a stateful object that:
  1. Records the target when an absolute move is issued.
  2. On next _tick() call (called once per pipeline loop after VERIFY_DELAY_SEC),
     reads the encoder from ptz_state.latest().
  3. If error > POINTING_TOLERANCE_ENC on either axis, issues one retry and
     emits a pointing_miss event.
  4. If a second tick still misses, emits pointing_miss again but does NOT retry.
"""
import time
import types
from wavecam.pointing_verifier import PointingVerifier
from wavecam.ptz_state import POINTING_TOLERANCE_ENC, VERIFY_DELAY_SEC


def _mock_ptz():
    calls = []
    obj = types.SimpleNamespace(
        pan_tilt_absolute=lambda pan, tilt, **kw: calls.append(("abs", pan, tilt)),
        _calls=calls,
    )
    return obj


def _mock_ptz_state(enc):
    """Returns a PtzState-compatible object whose latest() always returns enc."""
    return types.SimpleNamespace(latest=lambda: (enc, 0.01))


def _mock_events():
    recorded = []
    return types.SimpleNamespace(
        record=lambda kind, detail: recorded.append((kind, detail)),
        _recorded=recorded,
    )


def test_no_action_when_within_tolerance():
    ptz = _mock_ptz()
    ps = _mock_ptz_state((1000, -100))
    ev = _mock_events()
    v = PointingVerifier(ptz, ps, ev)
    v.record_move(pan_enc=1000, tilt_enc=-100, t=time.time() - VERIFY_DELAY_SEC - 0.1)
    v.tick()
    assert not ptz._calls
    assert not ev._recorded


def test_miss_triggers_retry_and_event():
    ptz = _mock_ptz()
    ps = _mock_ptz_state((500, -100))   # far from target
    ev = _mock_events()
    v = PointingVerifier(ptz, ps, ev)
    v.record_move(pan_enc=1000, tilt_enc=-100, t=time.time() - VERIFY_DELAY_SEC - 0.1)
    v.tick()
    assert len(ptz._calls) == 1
    assert ptz._calls[0] == ("abs", 1000, -100)
    assert any(k == "pointing_miss" for k, _ in ev._recorded)


def test_second_miss_logs_but_does_not_retry():
    ptz = _mock_ptz()
    ps = _mock_ptz_state((500, -100))
    ev = _mock_events()
    v = PointingVerifier(ptz, ps, ev)
    t_issue = time.time() - VERIFY_DELAY_SEC - 0.1
    v.record_move(pan_enc=1000, tilt_enc=-100, t=t_issue)
    v.tick()           # first miss → retry
    ptz._calls.clear()
    # H7: re-recording the SAME pending target is a no-op, so backdate the
    # retry's settle clock directly to reach the second verify.
    v._issue_t = time.time() - VERIFY_DELAY_SEC - 0.1
    v.tick()           # second miss → log only
    assert not ptz._calls
    miss_events = [d for k, d in ev._recorded if k == "pointing_miss"]
    assert len(miss_events) == 2


def test_no_tick_before_settle_time():
    ptz = _mock_ptz()
    ps = _mock_ptz_state((500, -100))
    ev = _mock_events()
    v = PointingVerifier(ptz, ps, ev)
    v.record_move(pan_enc=1000, tilt_enc=-100, t=time.time())   # just issued
    v.tick()   # settle time not elapsed
    assert not ptz._calls
    assert not ev._recorded


def test_stale_encoder_skips_verify():
    """If ptz_state has no encoder data yet, verification is silently skipped."""
    ptz = _mock_ptz()
    ps = types.SimpleNamespace(latest=lambda: (None, None))
    ev = _mock_events()
    v = PointingVerifier(ptz, ps, ev)
    v.record_move(pan_enc=1000, tilt_enc=-100, t=time.time() - VERIFY_DELAY_SEC - 0.1)
    v.tick()
    assert not ptz._calls
    assert not ev._recorded


class _FlippingBlock:
    """blocked() returns the scripted values in order, then sticks on the last."""
    def __init__(self, *values):
        self.values = list(values)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        v = self.values[0] if len(self.values) == 1 else self.values.pop(0)
        return v


def test_tick_while_blocked_clears_and_never_moves():
    """C1: KILL (or any block) during the settle window must clear the pending
    verify and never re-issue the absolute move."""
    ptz = _mock_ptz()
    ps = _mock_ptz_state((100, 0))   # far from target -> would normally retry
    v = PointingVerifier(ptz, ps, _mock_events(), blocked=lambda: True)
    v.record_move(1000, 0, t=time.time() - 10)
    v.tick()
    assert ptz._calls == []
    assert v._target is None


def test_block_flipping_true_before_resend_suppresses_move():
    """C1/C2 race: block engages between tick entry and the resend decision —
    the verifier must re-check immediately before commanding the camera."""
    ptz = _mock_ptz()
    ps = _mock_ptz_state((100, 0))
    blk = _FlippingBlock(False, True)   # entry check passes, pre-resend check blocks
    v = PointingVerifier(ptz, ps, _mock_events(), blocked=blk)
    v.record_move(1000, 0, t=time.time() - 10)
    v.tick()
    assert ptz._calls == []
    assert blk.calls >= 2
