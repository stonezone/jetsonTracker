"""PTZ ownership + KILL latch — pure logic, no I/O.

Exactly one writer may drive the PTZ at a time, so the vision loop, manual nudges,
and (later) the GPS tracker never fight over the camera. A sticky KILL latch blocks
all autonomous starts until RESUME. The pipeline/web layer wires the actual stop()
+ zoom-stop on kill and the manual deadman; this module only holds the state +
the rules so they're unit-testable offline.
"""
from __future__ import annotations
from typing import Dict

IDLE = "idle"
OWNERS = {IDLE, "manual", "vision_follow", "gps_tracker", "testbed"}
AUTONOMOUS = {"vision_follow", "gps_tracker", "testbed"}


class PtzOwner:
    def __init__(self) -> None:
        self.owner = IDLE
        self.killed = False

    def request(self, owner: str) -> bool:
        """Claim the PTZ. Rejected while killed, or while a different non-idle
        owner holds it (no auto-steal). Idempotent for the current holder."""
        if owner not in OWNERS or owner == IDLE:
            return False
        if self.killed:
            return False
        if self.owner != IDLE and self.owner != owner:
            return False
        self.owner = owner
        return True

    def release(self, owner: str) -> bool:
        """Only the current holder may release -> idle."""
        if owner != IDLE and self.owner == owner:
            self.owner = IDLE
            return True
        return False

    def kill(self) -> None:
        """Sticky global KILL: drop ownership and latch until resume()."""
        self.killed = True
        self.owner = IDLE

    def resume(self) -> None:
        self.killed = False

    def can_autonomous_start(self, owner: str) -> bool:
        if self.killed or owner not in AUTONOMOUS:
            return False
        return self.owner in (IDLE, owner)

    def can_manual(self) -> bool:
        """Manual pan/tilt nudges are allowed only when no autonomous owner holds
        the PTZ. (KILL / STOP are always allowed and handled by the caller.)"""
        return not self.killed and self.owner in (IDLE, "manual")

    def state(self) -> Dict[str, object]:
        return {"owner": self.owner, "killed": self.killed}
