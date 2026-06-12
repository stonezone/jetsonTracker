"""PointingVerifier — verify-and-resend for absolute pan/tilt moves.

Called once per pipeline loop tick. After VERIFY_DELAY_SEC has elapsed since
an absolute move, reads the encoder from PtzState. If the pointing error on
either axis exceeds POINTING_TOLERANCE_ENC, issues one retry and logs a
pointing_miss event. A second failure logs again but does not retry (avoids
oscillation while the camera is still settling or obstructed).

Tolerance and retry rationale (bench 2026-06-11):
  - Large absolute slews (e.g. 1200-count) overshoot by ~390 counts and then
    wander ±30 counts for 50+ seconds without converging. Small moves (<50
    counts) land exact.
  - POINTING_TOLERANCE_ENC=30 survives worst-case post-slew hunt without
    masking real failures; max 2 resends bounds oscillation on large slews.
  - Values imported from ptz_state module constants so they are tunable
    in one place.
"""
from __future__ import annotations

import time
from typing import Optional, Tuple

from .ptz_state import POINTING_TOLERANCE_ENC, VERIFY_DELAY_SEC


class PointingVerifier:
    def __init__(self, ptz, ptz_state, events):
        self._ptz = ptz
        self._ptz_state = ptz_state
        self._events = events
        self._target: Optional[Tuple[int, int]] = None
        self._issue_t: Optional[float] = None
        self._retry_count: int = 0

    def record_move(self, pan_enc: int, tilt_enc: int, t: float | None = None) -> None:
        """Call immediately after issuing an absolute pan/tilt command.
        If this is the same target as the previous move (e.g. GPS loop re-issuing
        the same command), preserve the retry count so the "one retry max" rule
        applies across repeated identical commands to the same position."""
        new_target = (pan_enc, tilt_enc)
        if new_target != self._target:
            self._retry_count = 0
        self._target = new_target
        self._issue_t = t if t is not None else time.time()

    def tick(self) -> None:
        """Call once per pipeline loop. Verifies and retries if conditions are met."""
        if self._target is None or self._issue_t is None:
            return
        if (time.time() - self._issue_t) < VERIFY_DELAY_SEC:
            return  # camera still settling

        enc, age = self._ptz_state.latest()
        if enc is None:
            return  # no encoder data yet — skip silently

        pan_target, tilt_target = self._target
        pan_actual, tilt_actual = enc
        pan_err = abs(pan_actual - pan_target)
        tilt_err = abs(tilt_actual - tilt_target)

        if pan_err <= POINTING_TOLERANCE_ENC and tilt_err <= POINTING_TOLERANCE_ENC:
            self._target = None   # success — clear pending verify
            return

        detail = (f"pan_err={pan_err} tilt_err={tilt_err} "
                  f"target=({pan_target},{tilt_target}) "
                  f"actual=({pan_actual},{tilt_actual}) "
                  f"retry={self._retry_count}")
        self._events.record("pointing_miss", detail)

        if self._retry_count == 0:
            self._ptz.pan_tilt_absolute(pan_target, tilt_target)
            self._retry_count += 1
            self._issue_t = time.time()   # reset settle clock for the retry
        else:
            # Second miss — give up on this move; next GPS command will reissue.
            self._target = None
