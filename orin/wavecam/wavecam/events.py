"""Structured event ring for pipeline state transitions.

Every lock/owner/GPS/kill change is recorded here so the first water
session produces evidence (via /events) rather than anecdotes. journalctl
also gets a permanent copy via logging.info so records survive ring rollover.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque

_log = logging.getLogger(__name__)


class EventRing:
    def __init__(self, maxlen: int = 500):
        self._lock = threading.Lock()
        self._ring: deque[dict] = deque(maxlen=maxlen)

    def record(self, kind: str, detail: str | dict, t: float | None = None) -> None:
        ts = t if t is not None else time.time()
        event = {"t": ts, "kind": kind, "detail": detail}
        with self._lock:
            self._ring.append(event)
        _log.info("[event] %s %s", kind, detail)

    def since(self, ts: float) -> list[dict]:
        with self._lock:
            return [dict(e) for e in self._ring if e["t"] > ts]
