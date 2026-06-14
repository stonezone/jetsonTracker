"""PTZ ownership + KILL latch — pure logic, no I/O.

Exactly one writer may drive the PTZ at a time, so the vision loop, manual nudges,
and (later) the GPS tracker never fight over the camera. A sticky KILL latch blocks
all autonomous starts until RESUME. The pipeline/web layer wires the actual stop()
+ zoom-stop on kill and the manual deadman; this module only holds the state +
the rules so they're unit-testable offline.
"""
from __future__ import annotations
import threading
from typing import Dict

IDLE = "idle"
CALIBRATE = "calibrate"
OWNERS = {IDLE, "manual", "vision_follow", "gps_tracker", "testbed", CALIBRATE}
AUTONOMOUS = {"vision_follow", "gps_tracker", "testbed"}


class PtzOwner:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._owner = IDLE
        self._killed = False

    @property
    def owner(self) -> str:
        with self._lock:
            return self._owner

    @owner.setter
    def owner(self, value: str) -> None:
        with self._lock:
            self._owner = value

    @property
    def killed(self) -> bool:
        with self._lock:
            return self._killed

    @killed.setter
    def killed(self, value: bool) -> None:
        with self._lock:
            self._killed = bool(value)

    def request(self, owner: str) -> bool:
        """Claim the PTZ. Rejected while killed, or while a different non-idle
        owner holds it (no auto-steal). Idempotent for the current holder."""
        with self._lock:
            if owner not in OWNERS or owner == IDLE:
                return False
            if self._killed:
                return False
            if self._owner != IDLE and self._owner != owner:
                return False
            self._owner = owner
            return True

    def release(self, owner: str) -> bool:
        """Only the current holder may release -> idle."""
        with self._lock:
            if owner != IDLE and self._owner == owner:
                self._owner = IDLE
                return True
            return False

    def kill(self) -> None:
        """Sticky global KILL: drop ownership and latch until resume()."""
        with self._lock:
            self._killed = True
            self._owner = IDLE

    def resume(self) -> None:
        with self._lock:
            self._killed = False

    def can_autonomous_start(self, owner: str) -> bool:
        with self._lock:
            if self._killed or owner not in AUTONOMOUS:
                return False
            return self._owner in (IDLE, owner)

    def can_manual(self) -> bool:
        """Manual pan/tilt nudges are allowed only when no autonomous owner holds
        the PTZ. (KILL / STOP are always allowed and handled by the caller.)"""
        with self._lock:
            return not self._killed and self._owner in (IDLE, "manual")

    def state(self) -> Dict[str, object]:
        with self._lock:
            return {"owner": self._owner, "killed": self._killed}
