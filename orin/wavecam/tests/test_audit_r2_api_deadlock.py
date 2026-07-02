"""Audit round-2 — R10: ABBA lock-order deadlock between the adapter lock and
PtzDispatcher's own lock.

The restart path (control_system.SystemManager.request_service_restart /
prepare_for_restart) and the CALIBRATE session-start takeover path
(control_calibration.CalibrationManager.start_session) both hold the adapter
lock (A) and then reach into PtzDispatcher's lock (B) via
cancel_manual_deadman()/cancel_zoom_deadman(). Before this fix,
PtzDispatcher.manual_deadman_expired/zoom_deadman_expired held B and then
called self._bump_revision() (which needs A) WHILE STILL HOLDING B — an A->B
/ B->A ABBA pair that could wedge the whole API, including /safety/kill.

Fix: both expiry callbacks now compute their decision under B, then call
_bump_revision() only AFTER releasing B, so this side of the pair never holds
both locks at once.
"""
from __future__ import annotations

import threading
import time
import types

from wavecam.control_ptz import PtzDispatcher
from wavecam.ptz_owner import PtzOwner


class SlowStopPtz:
    """DummyPtz-shaped fake whose stop()/zoom() sleep, so a thread inside
    manual_deadman_expired's/zoom_deadman_expired's critical section is
    reliably still holding PtzDispatcher._lock (B) at the moment a concurrent
    adapter-lock holder tries to acquire B too — reproducing the race
    deterministically instead of relying on scheduler luck."""

    def __init__(self, delay: float = 0.2):
        self.delay = delay
        self.calls: list = []

    def stop(self):
        self.calls.append("stop")
        time.sleep(self.delay)

    def zoom(self, direction, speed=0):
        self.calls.append(("zoom", direction, speed))
        time.sleep(self.delay)

    def pan_tilt(self, *args):
        self.calls.append(("pan_tilt", *args))


class DummyArbiter:
    def reset_vision_state(self):
        pass


def _make_dispatcher(delay=0.2):
    pipe = types.SimpleNamespace()
    pipe.owner = PtzOwner()
    pipe.ptz = SlowStopPtz(delay=delay)
    pipe.arbiter = DummyArbiter()
    pipe.cfg = types.SimpleNamespace(ptz=types.SimpleNamespace(zoom_max_speed=5))
    adapter_lock = threading.RLock()

    def bump_revision():
        # Mirrors ControlApiAdapter.bump_revision(): acquires the ADAPTER lock,
        # not the dispatcher lock.
        with adapter_lock:
            pass

    disp = PtzDispatcher(pipe, bump_revision=bump_revision)
    return pipe, disp, adapter_lock


def test_manual_deadman_expiry_does_not_abba_deadlock_with_adapter_lock():
    pipe, disp, adapter_lock = _make_dispatcher(delay=0.2)
    assert pipe.owner.request("manual")
    disp._manual_pan_tilt_active = True
    generation = disp.schedule_manual_deadman(50_000)  # never fires on its own

    started = threading.Event()

    def deadman_thread():
        # Mirrors the real timer callback firing: acquires disp._lock (B),
        # sleeps inside it via SlowStopPtz.stop(), and must release B BEFORE
        # calling bump_revision() (which needs the adapter lock, A).
        started.set()
        disp.manual_deadman_expired(generation)

    def restart_thread():
        # Mirrors control_system.SystemManager.request_service_restart /
        # control_calibration.CalibrationManager.start_session: hold the
        # adapter lock (A) first, then reach into the ptz lock (B).
        assert started.wait(timeout=2.0)
        time.sleep(0.05)  # let deadman_thread actually be inside the B section
        with adapter_lock:
            disp.cancel_zoom_deadman()

    t1 = threading.Thread(target=deadman_thread)
    t2 = threading.Thread(target=restart_thread)
    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    assert not t1.is_alive(), "manual_deadman_expired hung — ABBA deadlock reproduced"
    assert not t2.is_alive(), "adapter-lock holder hung waiting on the ptz lock — deadlock"
    assert pipe.owner.owner == "idle"


def test_zoom_deadman_expiry_does_not_abba_deadlock_with_adapter_lock():
    pipe, disp, adapter_lock = _make_dispatcher(delay=0.2)
    generation = disp.schedule_zoom_deadman(50_000)

    started = threading.Event()

    def deadman_thread():
        started.set()
        disp.zoom_deadman_expired(generation)

    def restart_thread():
        assert started.wait(timeout=2.0)
        time.sleep(0.05)
        with adapter_lock:
            disp.cancel_manual_deadman()

    t1 = threading.Thread(target=deadman_thread)
    t2 = threading.Thread(target=restart_thread)
    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    assert not t1.is_alive(), "zoom_deadman_expired hung — ABBA deadlock reproduced"
    assert not t2.is_alive(), "adapter-lock holder hung waiting on the ptz lock — deadlock"


if __name__ == "__main__":
    test_manual_deadman_expiry_does_not_abba_deadlock_with_adapter_lock()
    test_zoom_deadman_expiry_does_not_abba_deadlock_with_adapter_lock()
    print("R10 ABBA DEADLOCK REGRESSION TESTS PASSED")
