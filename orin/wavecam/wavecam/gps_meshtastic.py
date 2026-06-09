"""Meshtastic LoRa GPS ingest — the live replacement for ``GpsStub``.

Reads the REMOTE tracker's position from the BASE Wio Tracker L1 over USB serial
(``/dev/ttyACM*``) and produces a :class:`NormalizedFix`. The L76K position packets
omit ground speed/track unless those flags are enabled, so ``course``/``speed`` are
derived from position deltas. The BASE node's own L76K fix is the camera/tripod
reference position (:meth:`get_camera_position`).

THREADING (this is the whole point): a daemon **reader thread** owns the Meshtastic
``SerialInterface`` and refreshes a lock-guarded snapshot (latest remote fix + camera
position) on a timer. ``get_fix()`` and ``get_camera_position()`` are **non-blocking
reads of that snapshot and NEVER call the Meshtastic lib**. An earlier version called
the lib directly on the API request thread and the in-process interface wedged the
whole HTTP API (2026-06-08 incident). Confining all lib access to the reader thread is
the fix; the public reads can never block the caller.

Drop-in for ``GpsStub``: same ``enabled`` + ``get_fix()`` contract. ``meshtastic`` is
imported lazily so the module stays importable without the library.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import replace
from typing import Optional, Tuple

from .gps_stub import NormalizedFix

log = logging.getLogger(__name__)

EARTH_R_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_M * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial true bearing from point 1 to point 2, degrees in [0, 360)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _remote_from_nodes(nodes: dict, my_num,
                       remote_id: Optional[str]) -> Optional[Tuple[str, float, float, float]]:
    """Pick the remote fix from a meshtastic node dict (pure, no I/O).

    Skips the local/base node (``num == my_num``); returns ``(id, lat, lon, ts)`` for
    the node matching ``remote_id`` if given, else the one with the freshest fix.
    Returns None if no non-local node carries a position.
    """
    best = None
    for n in nodes.values():
        if n.get("num") == my_num:
            continue
        pos = n.get("position") or {}
        lat, lon = pos.get("latitude"), pos.get("longitude")
        if lat is None or lon is None:
            continue
        nid = (n.get("user") or {}).get("id")
        if remote_id is not None and nid != remote_id:
            continue
        ts = float(pos.get("time") or 0.0)
        if best is None or ts > best[3]:
            best = (nid, float(lat), float(lon), ts)
    return best


def _camera_from_nodes(nodes: dict, my_num) -> Optional[Tuple[float, float, float]]:
    """The base/local node's own ``(lat, lon, alt)``, or None if it has no fix. Pure."""
    n = next((x for x in nodes.values() if x.get("num") == my_num), None)
    if not n:
        return None
    pos = n.get("position") or {}
    lat, lon = pos.get("latitude"), pos.get("longitude")
    if lat is None or lon is None:
        return None
    return float(lat), float(lon), float(pos.get("altitude") or 0.0)


class MeshtasticGps:
    """Live LoRa GPS source. A reader thread owns the serial interface; the public
    reads are non-blocking snapshot lookups that never touch the Meshtastic lib."""

    def __init__(self, dev_path: str = "/dev/ttyACM0", remote_id: Optional[str] = None,
                 min_move_m: float = 3.0, poll_sec: float = 1.0):
        self.dev_path = dev_path
        self.remote_id = remote_id
        self.min_move_m = min_move_m  # below this between fixes => GPS jitter, treat as stationary
        self.poll_sec = poll_sec
        self.enabled = False
        self._iface = None
        self._my_num = None
        # Reader-thread-confined derivation state — ONLY _reader_loop touches these:
        self._last: Optional[Tuple[float, float, float]] = None
        self._course = 0.0
        self._speed = 0.0
        # Shared snapshot, guarded by _lock (written by the reader, read by callers):
        self._lock = threading.Lock()
        self._latest: Optional[NormalizedFix] = None
        self._cam: Optional[Tuple[float, float, float]] = None
        self._cam_ts: float = 0.0
        # Reader lifecycle:
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def connect(self) -> bool:
        """Open the serial link to the base Wio and start the reader thread."""
        try:
            import meshtastic.serial_interface as msi  # lazy: optional dependency
        except Exception as e:  # pragma: no cover - host without the lib
            log.warning("meshtastic library unavailable: %s", e)
            return False
        try:
            self._iface = msi.SerialInterface(devPath=self.dev_path)
            self._my_num = self._iface.getMyNodeInfo().get("num")
        except Exception as e:
            log.warning("MeshtasticGps connect failed on %s: %s", self.dev_path, e)
            self._iface = None
            return False
        self.enabled = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._reader_loop, name="meshtastic-gps", daemon=True)
        self._thread.start()
        log.info("MeshtasticGps connected on %s (base node %s); reader thread started",
                 self.dev_path, self._my_num)
        return True

    def _reader_loop(self) -> None:
        """The ONLY place the Meshtastic lib is accessed. Refreshes the locked
        snapshot (remote fix + camera position) on a timer so the public reads can
        never block the caller's thread (that was the API-hang bug)."""
        while not self._stop.is_set():
            try:
                nodes = self._iface.nodes or {}
                remote = _remote_from_nodes(nodes, self._my_num, self.remote_id)
                fix = self._to_fix(*remote[1:], now=time.time()) if remote is not None else None
                cam = _camera_from_nodes(nodes, self._my_num)
                base_node = next((x for x in nodes.values() if x.get("num") == self._my_num), None)
                cam_ts = float((base_node.get("position") or {}).get("time") or 0.0)
                with self._lock:
                    self._latest = fix
                    self._cam = cam
                    self._cam_ts = cam_ts
            except Exception as e:  # a reader error must not kill the thread or the app
                log.warning("MeshtasticGps reader loop error: %s", e)
            self._stop.wait(self.poll_sec)

    def get_fix(self, now: Optional[float] = None) -> Optional[NormalizedFix]:
        """Non-blocking snapshot read (NEVER calls the Meshtastic lib). Returns the
        last remote fix with its age refreshed from the cached timestamp."""
        now = time.time() if now is None else now
        with self._lock:
            fix = self._latest
        if fix is None:
            return None
        return replace(fix, age_sec=max(0.0, now - fix.ts))

    def get_camera_position(self) -> Optional[Tuple[float, float, float]]:
        """Non-blocking snapshot read of the base node's own fix (camera/tripod
        reference position), or None until the base has sky view + a fix."""
        with self._lock:
            return self._cam

    def get_camera_age(self, now: Optional[float] = None) -> Optional[float]:
        """Age of the base node's last fix in seconds, or None if no base fix yet."""
        with self._lock:
            if self._cam is None or self._cam_ts <= 0:
                return None
            return max(0.0, (time.time() if now is None else now) - self._cam_ts)

    def _to_fix(self, lat: float, lon: float, ts: float, now: float) -> NormalizedFix:
        """Derive a NormalizedFix, computing course/speed from the previous fix.
        Called ONLY by the reader thread (so ``_last``/``_course``/``_speed`` need no
        lock). Movement below ``min_move_m`` is treated as GPS jitter (speed 0, hold
        heading); a repeated fix (same ts) reuses the last course/speed."""
        if self._last is None:
            self._last = (lat, lon, ts)
        elif ts > self._last[2]:
            dt = ts - self._last[2]
            dist = haversine_m(self._last[0], self._last[1], lat, lon)
            if dist >= self.min_move_m and dt > 0:
                self._speed = dist / dt
                self._course = bearing_deg(self._last[0], self._last[1], lat, lon)
            else:
                self._speed = 0.0
            self._last = (lat, lon, ts)
        return NormalizedFix(
            lat=lat, lon=lon, course=self._course, speed=self._speed,
            ts=ts, age_sec=max(0.0, now - ts), src="lora",
        )

    def close(self) -> None:
        """Stop the reader thread and release the serial interface."""
        self._stop.set()
        if self._thread is not None:
            # If the reader is parked in a blocking serial read, Event.set() won't wake
            # it, so join() times out and we drop the reference. The iface.close() below
            # then unblocks that read; the daemon thread finishes unwinding on its own (or
            # is reclaimed at process exit). Abandoning a wedged thread here is intentional.
            self._thread.join(timeout=3.0)
            self._thread = None
        if self._iface is not None:
            try:
                self._iface.close()
            except Exception:  # pragma: no cover - shutdown best-effort
                pass
            self._iface = None
        self.enabled = False


if __name__ == "__main__":  # live diagnostic: python3 -m wavecam.gps_meshtastic [port]
    import sys

    logging.basicConfig(level=logging.INFO)
    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM0"
    gps = MeshtasticGps(dev_path=port)
    if not gps.connect():
        print("connect failed")
        sys.exit(1)
    try:
        for _ in range(6):
            print("fix:", gps.get_fix(), "| camera:", gps.get_camera_position())
            time.sleep(2)
    finally:
        gps.close()
