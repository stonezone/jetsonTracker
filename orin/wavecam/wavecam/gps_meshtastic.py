"""Meshtastic LoRa GPS ingest — the live replacement for ``GpsStub``.

Reads the REMOTE tracker's position from the BASE Wio Tracker L1 over USB serial
(``/dev/ttyACM*``) and produces a :class:`NormalizedFix`. The L76K position
packets omit ground speed/track unless those flags are enabled, so ``course`` and
``speed`` are **derived from position deltas** (never depended on from the packet).
The BASE node's own L76K fix is exposed separately via :meth:`get_camera_position`
as the camera/tripod reference position.

Drop-in for ``GpsStub``: same ``enabled`` + ``get_fix()`` contract. ``meshtastic``
is imported lazily so this module stays importable on a host without the library
(``enabled`` stays False and ``get_fix()`` returns None).

Verified 2026-06-08 against the live 2-unit mesh: base ``!38c3f1fd`` on the Orin
reads remote ``!9f5802d5`` at 21.62688, -158.0466 over /dev/ttyACM0.
"""
from __future__ import annotations

import logging
import math
import time
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

    Skips the local/base node (``num == my_num``); returns ``(id, lat, lon, ts)``
    for the node matching ``remote_id`` if given, else the one with the freshest
    fix. Returns None if no non-local node carries a position.
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


class MeshtasticGps:
    """Live LoRa GPS source backed by the base Wio over USB serial."""

    def __init__(self, dev_path: str = "/dev/ttyACM0",
                 remote_id: Optional[str] = None, min_move_m: float = 3.0):
        self.dev_path = dev_path
        self.remote_id = remote_id
        self.min_move_m = min_move_m  # below this between fixes => treat as stationary (GPS jitter)
        self.enabled = False
        self._iface = None
        self._my_num = None
        self._last: Optional[Tuple[float, float, float]] = None  # lat, lon, ts
        self._course = 0.0
        self._speed = 0.0

    def connect(self) -> bool:
        """Open the serial link to the base Wio. Returns True on success."""
        try:
            import meshtastic.serial_interface as msi  # lazy: optional dependency
        except Exception as e:  # pragma: no cover - host without the lib
            log.warning("meshtastic library unavailable: %s", e)
            return False
        try:
            self._iface = msi.SerialInterface(devPath=self.dev_path)
            self._my_num = self._iface.getMyNodeInfo().get("num")
            self.enabled = True
            log.info("MeshtasticGps connected on %s (base node %s)", self.dev_path, self._my_num)
            return True
        except Exception as e:
            log.warning("MeshtasticGps connect failed on %s: %s", self.dev_path, e)
            self._iface = None
            self.enabled = False
            return False

    def get_fix(self, now: Optional[float] = None) -> Optional[NormalizedFix]:
        """Latest remote position as a NormalizedFix, or None if no fix yet."""
        if not self.enabled or self._iface is None:
            return None
        now = time.time() if now is None else now
        try:
            nodes = self._iface.nodes or {}
        except Exception as e:  # pragma: no cover
            log.warning("reading mesh nodes failed: %s", e)
            return None
        r = _remote_from_nodes(nodes, self._my_num, self.remote_id)
        if r is None:
            return None
        _id, lat, lon, ts = r
        return self._to_fix(lat, lon, ts, now)

    def _to_fix(self, lat: float, lon: float, ts: float, now: float) -> NormalizedFix:
        """Build a NormalizedFix, deriving course/speed from the previous fix.

        Movement below ``min_move_m`` between fixes is treated as GPS jitter:
        speed -> 0 and the last heading is held (so a brief stop doesn't spin the
        aim). A repeated fix (same ts) reuses the last course/speed and only
        refreshes age.
        """
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

    def get_camera_position(self) -> Optional[Tuple[float, float, float]]:
        """The base/local node's own L76K fix ``(lat, lon, alt)`` — the camera /
        tripod reference position — or None until the base has sky view + a fix."""
        if not self.enabled or self._iface is None:
            return None
        try:
            nodes = self._iface.nodes or {}
        except Exception:  # pragma: no cover
            return None
        n = next((x for x in nodes.values() if x.get("num") == self._my_num), None)
        if not n:
            return None
        pos = n.get("position") or {}
        lat, lon = pos.get("latitude"), pos.get("longitude")
        if lat is None or lon is None:
            return None
        return float(lat), float(lon), float(pos.get("altitude") or 0.0)

    def close(self) -> None:
        if self._iface is not None:
            try:
                self._iface.close()
            except Exception:  # pragma: no cover
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
    time.sleep(4)
    try:
        for _ in range(5):
            print("fix:", gps.get_fix(), "| camera:", gps.get_camera_position())
            time.sleep(5)
    finally:
        gps.close()
