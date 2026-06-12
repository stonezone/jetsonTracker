"""Stale-late-reply defenses (2026-06-11 trajectory captures showed time-jumbled
encoder readings during motion: a delayed reply to the previous inquiry was
accepted as current, and one garbage frame could poison downstream consumers).

Two independent layers:
  1. ViscaIP.inquire_pan_tilt returns the FRESHEST queued position frame.
  2. PtzState._poll_once rejects physically implausible jumps unless confirmed
     by the next sample (real large moves re-baseline; one-off garbage drops).
"""
import socket as socket_mod
import time
import types

from wavecam.ptz_state import PtzState, MAX_SLEW_COUNTS_PER_SEC
from wavecam.ptz_visca import ViscaIP


def _pos_frame(pan: int, tilt: int) -> bytes:
    p, t = pan & 0xFFFF, tilt & 0xFFFF
    return bytes([0x90, 0x50,
                  (p >> 12) & 0xF, (p >> 8) & 0xF, (p >> 4) & 0xF, p & 0xF,
                  (t >> 12) & 0xF, (t >> 8) & 0xF, (t >> 4) & 0xF, t & 0xF,
                  0xFF])


class _FakeSock:
    """Scripted recvfrom mirroring the real race: replies become readable only
    AFTER the inquiry is sent (so _drain() pre-send eats nothing, exactly like
    a stale frame arriving in the drain→reply window). Empty queue raises
    timeout (blocking) or BlockingIOError (non-blocking) like a real UDP socket."""

    def __init__(self, frames):
        self.pending = list(frames)
        self.frames = []
        self.sent = []
        self.blocking = True

    def sendto(self, payload, addr):
        self.sent.append(payload)
        self.frames = self.pending
        self.pending = []

    def recvfrom(self, n):
        if self.frames:
            return self.frames.pop(0), ("cam", 1259)
        if self.blocking:
            raise socket_mod.timeout()
        raise BlockingIOError()

    def setblocking(self, flag):
        self.blocking = flag

    def settimeout(self, t):
        self.blocking = True

    def close(self):
        pass


def _visca_with(frames) -> ViscaIP:
    v = ViscaIP.__new__(ViscaIP)
    v.ip, v.port, v.addr, v.timeout = "x", 1, 0x81, 0.3
    import threading
    v._lock = threading.RLock()
    v._sock = _FakeSock(frames)
    return v


def test_inquire_returns_freshest_queued_position():
    # A stale frame (previous inquiry's late reply) sits AHEAD of the fresh one.
    v = _visca_with([_pos_frame(100, 0), _pos_frame(900, 0)])
    assert v.inquire_pan_tilt() == (900, 0)


def test_inquire_single_frame_unchanged():
    v = _visca_with([_pos_frame(-250, -40)])
    assert v.inquire_pan_tilt() == (-250, -40)


def test_inquire_skips_interleave_then_returns_position():
    ack = bytes([0x90, 0x41, 0xFF])
    v = _visca_with([ack, _pos_frame(42, 7)])
    assert v.inquire_pan_tilt() == (42, 7)


def _state_with_replies(replies) -> PtzState:
    q = list(replies)
    fake = types.SimpleNamespace(inquire_pan_tilt=lambda: q.pop(0) if q else None)
    return PtzState(fake, poll_hz=100)


def test_plausibility_gate_drops_one_garbage_frame():
    ps = _state_with_replies([(100, 0), (90000, 0), (104, 0)])
    ps._poll_once()                       # baseline
    time.sleep(0.01)
    ps._poll_once()                       # garbage — held back, cache unchanged
    enc, _ = ps.latest()
    assert enc == (100, 0)
    time.sleep(0.01)
    ps._poll_once()                       # plausible again — accepted
    enc, _ = ps.latest()
    assert enc == (104, 0)


def test_plausibility_gate_accepts_confirmed_large_move():
    ps = _state_with_replies([(100, 0), (50000, 0), (50010, 0)])
    ps._poll_once()
    time.sleep(0.01)
    ps._poll_once()                       # huge jump — held back
    enc, _ = ps.latest()
    assert enc == (100, 0)
    time.sleep(0.01)
    ps._poll_once()                       # agrees with the held-back sample
    enc, _ = ps.latest()
    assert enc == (50010, 0)              # re-baselined: it was real
