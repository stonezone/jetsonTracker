"""Component heartbeats. Every long-lived loop calls beat(name) each cycle;
/health turns silence into a visible failure. This generalizes the
gps reader_alive retrofit — silent thread death was this project's #1
incident class (wedged API 06-08, dead reader, quiet engine degradation)."""
from __future__ import annotations

import threading
import time


class HealthRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._last: dict[str, tuple[float, dict]] = {}

    def beat(self, name: str, detail: dict | None = None) -> None:
        with self._lock:
            self._last[name] = (time.time(), detail or {})

    def snapshot(self, stale_after_sec: float = 5.0) -> dict:
        now = time.time()
        with self._lock:
            comps = {
                name: {"ok": (now - ts) < stale_after_sec,
                       "age_sec": round(now - ts, 2), "detail": detail}
                for name, (ts, detail) in self._last.items()
            }
        return {"ok": all(c["ok"] for c in comps.values()) if comps else False,
                "components": comps}
