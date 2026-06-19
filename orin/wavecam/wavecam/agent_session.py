"""Interactive acting-agent: the arm-state safety machine + the claude -p driver.

Phase 1a builds the conversation + safety bridge only — no acting tools yet.
ArmState is the supervise-only gate: DISARMED by default, an ARMED session
auto-expires after a TTL, and KILL is supreme (disarms and forbids re-arm until
explicitly cleared). Acting tiers (Phase 1b+) will read ``can_act()`` before any
mutating tool runs.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

CLAUDE_CLI_PATH = "/home/zack/.local/bin/claude"
REQUEST_TIMEOUT_SEC = 90.0


class ArmState:
    """Operator arm gate.

    DISARMED by default; ARMED auto-expires ``ttl_sec`` after the last ``arm()``;
    KILL disarms immediately and blocks re-arm until ``clear_kill()``. ``now`` is
    injected (monotonic by default) so the TTL logic is deterministic under test.
    """

    def __init__(self, ttl_sec: float, now: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_sec
        self._now = now
        self._armed_at: Optional[float] = None
        self._killed = False

    def arm(self) -> None:
        if self._killed:
            return
        self._armed_at = self._now()

    def disarm(self) -> None:
        self._armed_at = None

    def kill(self) -> None:
        self._killed = True
        self._armed_at = None

    def clear_kill(self) -> None:
        self._killed = False

    @property
    def killed(self) -> bool:
        return self._killed

    @property
    def armed(self) -> bool:
        if self._killed or self._armed_at is None:
            return False
        return (self._now() - self._armed_at) < self._ttl

    def can_act(self) -> bool:
        return self.armed and not self._killed

    def snapshot(self) -> dict:
        return {"armed": self.armed, "killed": self._killed, "ttl_sec": self._ttl}
