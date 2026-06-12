"""PtzState — background pan/tilt encoder poller.

Owns a dedicated poll_loop thread that calls inquire_pan_tilt() at POLL_HZ.
Exposes latest() as a non-blocking lock read of the cached snapshot. Never
issues move commands; never blocks the pipeline or API threads.

Socket-lock discipline (matches ptz_visca.py):
  ViscaIP._lock is held for sendto only. The recv loop runs outside the lock.
  PtzState calls ptz.inquire_pan_tilt() which follows this discipline internally.
  Because PtzState is the ONLY caller of inquire_pan_tilt() (the pipeline loop
  does not call it), there is no concurrent recv contention.

Bench-measured constants (2026-06-11, Prisual NDI PTZ, 300s stress test at each
rate with 10Hz velocity commands interleaved):
  - POLL_HZ=10: 2982 sent, 0 lost (0.0% loss), p95=81.8ms, 5964 interleave events
  - POLL_HZ=5:  1489 sent, 0 lost, p95=73ms
  - POLL_HZ=2:  594 sent, 3 lost (0.5%), p95=66ms
  10Hz chosen: lowest loss, comfortably under 100ms p95, fine interleave tolerance.

Known follow-up: a stale-late-reply race in ViscaIP.inquire_pan_tilt exists
(drain-then-send still admits a late reply to the PREVIOUS inquiry). Low
probability at 10Hz with 82ms p95. Not fixed here — out of plan scope. Track as
a future hardening item.
"""
from __future__ import annotations

import threading
import time
from typing import Optional, Tuple

# ── Bench parameters ─────────────────────────────────────────────────────────
# Filled from the Task 0 bench run (2026-06-11). See plan header for methodology.
POLL_HZ: float = 10.0              # bench 2026-06-11: 0.0% loss, p95 81.8ms at 10Hz
REPLY_LATENCY_P95_MS: float = 82.0 # bench 2026-06-11 (under 10Hz velocity traffic)
REPLY_LOSS_PCT: float = 0.0        # bench 2026-06-11: zero loss at 10Hz (2982 sent)
INTERLEAVE_OBSERVED: bool = True    # bench 2026-06-11: 5964 non-pos frames in 300s at 10Hz
# ─────────────────────────────────────────────────────────────────────────────

# Position tolerance for verify-and-resend (encoder counts).
# Bench 2026-06-11: 1200-count slews overshot by ~390 counts and wandered ±30
# counts for 50+ seconds; small moves (<50 counts) land exact. Setting tolerance
# to 30 to survive worst-case hunt without masking real failures. Using max 2
# resends to bound oscillation on large slews.
POINTING_TOLERANCE_ENC: int = 30

# How long to wait after issuing an absolute command before reading back
# the encoder. Must exceed the camera's settle time for small moves.
VERIFY_DELAY_SEC: float = 0.5


class PtzState:
    """Background encoder-position cache. One instance per pipeline."""

    def __init__(self, ptz, poll_hz: float = POLL_HZ):
        self._ptz = ptz
        self._poll_hz = poll_hz
        self._lock = threading.Lock()
        self._enc: Optional[Tuple[int, int]] = None   # (pan, tilt) counts
        self._ts: Optional[float] = None              # time of last valid reply
        self._thread: Optional[threading.Thread] = None
        self._stop_ev = threading.Event()

    # ── public API (non-blocking, safe from any thread) ──────────────────────

    def latest(self) -> Tuple[Optional[Tuple[int, int]], Optional[float]]:
        """Return (enc, age_sec) where enc=(pan,tilt) or None if no reply yet.
        age_sec is seconds since the last valid reply, or None."""
        with self._lock:
            if self._enc is None:
                return None, None
            return self._enc, time.time() - self._ts

    def start(self) -> None:
        """Start the background poll thread. Idempotent."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_ev.clear()
        self._thread = threading.Thread(
            target=self._poll_loop, name="ptz-poller", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the poll thread to exit and join (blocks up to 1.5s (a blocked recv chain can take 4×0.3s))."""
        self._stop_ev.set()
        if self._thread:
            self._thread.join(timeout=1.5)

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ── internal ─────────────────────────────────────────────────────────────

    def _poll_once(self) -> None:
        """Single inquiry cycle. Called by the poll loop and directly in tests."""
        result = self._ptz.inquire_pan_tilt()
        if result is not None:
            with self._lock:
                self._enc = result
                self._ts = time.time()

    def _poll_loop(self) -> None:
        period = 1.0 / max(0.1, self._poll_hz)
        while not self._stop_ev.is_set():
            t0 = time.time()
            try:
                self._poll_once()
            except Exception as e:
                # Log but do not crash — a transient UDP failure must not kill
                # the poller; it will retry next cycle.
                print(f"[ptz_state] poll error: {e}")
            dt = time.time() - t0
            wait = period - dt
            if wait > 0:
                self._stop_ev.wait(wait)
