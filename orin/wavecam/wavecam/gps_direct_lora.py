"""Direct-LoRa USB serial GPS ingest for the Wio Tracker L1 base station.

The base firmware emits newline-delimited JSON at 115200 baud. This reader owns
that serial port on a daemon thread and exposes lock-guarded snapshots through
the same non-blocking seam used by ``MeshtasticGps``.
"""
from __future__ import annotations

import glob
import json
import logging
import threading
import time
from dataclasses import replace
from typing import Any, Callable, Optional, Tuple

from .gps_stub import NormalizedFix

log = logging.getLogger(__name__)

SerialFactory = Callable[..., Any]

# L2 (audit 2026-07-01): after this many consecutive open failures on the
# configured device path, glob for other /dev/ttyACM* candidates -- recovers
# from a re-enumeration (e.g. a stale process holds ACM0 and the replugged
# Wio lands on ACM1) without a service restart.
OPEN_FAIL_GLOB_THRESHOLD = 5


def _flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def _flag_or_default(data: dict, key: str, default: bool) -> bool:
    """M8: read an optional validity flag ("spd_ok"/"crs_ok"). Absent => default
    (True), so older firmware that doesn't emit the flag keeps behaving as before."""
    if key not in data:
        return default
    return _flag(data.get(key))


def _e7_to_deg(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value) / 10_000_000.0


def _float_value(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_value(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class DirectRadioGps:
    """Live direct-LoRa GPS source.

    Public reads never touch serial; only the background reader thread blocks on
    USB. Remote ``seq`` lines update the subject fix. Stable ``base`` lines
    update the camera/tripod reference position.
    """

    def __init__(
        self,
        dev_path: str = "/dev/ttyACM0",
        baud: int = 115200,
        reconnect_sec: float = 3.0,
        serial_factory: SerialFactory | None = None,
        coast_on_no_fix_sec: float = 2.0,
    ):
        self.dev_path = dev_path
        self.baud = int(baud)
        self.reconnect_sec = float(reconnect_sec)
        self._serial_factory = serial_factory
        # L2: consecutive open failures on self.dev_path; reset to 0 on a
        # successful open. current_dev_path is what actually got opened last
        # (the configured path, or a glob-discovered fallback).
        self._open_fail_count = 0
        self.current_dev_path = dev_path
        # GPS-1: on an HONEST no-fix packet, coast on the last good fix for this long
        # (a wipeout / wave-trough blackout) instead of dropping the aim instantly.
        # 0 = clear immediately (the pre-GPS-1 behavior).
        self.coast_on_no_fix_sec = float(coast_on_no_fix_sec)
        self.enabled = False
        self._serial = None

        self._lock = threading.Lock()
        self._latest: Optional[NormalizedFix] = None
        self._last_fix_ok_ts: Optional[float] = None   # when _latest was last set from a real fix
        self._latest_no_fix_at: Optional[float] = None  # ts of the most recent honest no-fix packet
        self._cam: Optional[Tuple[float, float, float]] = None
        self._cam_ts: float = 0.0
        # H9 (audit 2026-07-01): the base's settled running mean (_cam) is
        # unbounded and freezes after ~30 min, defeating BaseDriftMonitor (a 10m
        # tripod kick moves the mean ~5.5mm/sample). raw_lat/raw_lon are the
        # INSTANTANEOUS fix at the same tick; prefer it for drift detection,
        # falling back to the mean when the firmware doesn't emit it.
        self._cam_raw: Optional[Tuple[float, float, float]] = None
        self._cam_raw_ts: float = 0.0
        self._last_poll_ts: Optional[float] = None
        self._target_telemetry: dict[str, int | None] = {
            "target_battery_mv": None,
            "target_sats": None,
        }

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def connect(self) -> bool:
        """Start the reader thread.

        Returns True when the thread is started. If the USB device is absent, the
        thread keeps retrying until it appears. If pyserial is unavailable, return
        False so run.py can keep the vision pipeline alive without this source.
        """
        if self._serial_factory is None:
            try:
                import serial  # noqa: F401
            except Exception as e:  # pragma: no cover - host without pyserial
                log.warning("pyserial unavailable: %s", e)
                return False
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._reader_loop, name="direct-lora-gps", daemon=True)
        self._thread.start()
        return True

    def _candidate_paths(self) -> list:
        """L2: the configured path first; after OPEN_FAIL_GLOB_THRESHOLD
        consecutive failures, also try other /dev/ttyACM* nodes (a re-enumerated
        Wio can land on a different node while a stale process still holds the
        configured one)."""
        candidates = [self.dev_path]
        if self._open_fail_count >= OPEN_FAIL_GLOB_THRESHOLD:
            try:
                for path in sorted(glob.glob("/dev/ttyACM*")):
                    if path not in candidates:
                        candidates.append(path)
            except OSError:
                pass
        return candidates

    def _open_serial(self, dev_path: str):
        if self._serial_factory is not None:
            return self._serial_factory(dev_path, self.baud, timeout=1)
        import serial

        return serial.Serial(dev_path, self.baud, timeout=1)

    def _reader_loop(self) -> None:
        while not self._stop.is_set():
            if self._serial is None:
                candidates = self._candidate_paths()
                opened = False
                last_err: Exception | None = None
                for dev_path in candidates:
                    try:
                        self._serial = self._open_serial(dev_path)
                        self.current_dev_path = dev_path
                        self.enabled = True
                        self._open_fail_count = 0
                        opened = True
                        if dev_path != self.dev_path:
                            log.warning(
                                "DirectRadioGps recovered via glob fallback on %s "
                                "(configured %s unavailable after %d failures)",
                                dev_path, self.dev_path, OPEN_FAIL_GLOB_THRESHOLD,
                            )
                        else:
                            log.info("DirectRadioGps connected on %s at %s baud", dev_path, self.baud)
                        break
                    except Exception as e:
                        last_err = e
                        continue
                if not opened:
                    self._open_fail_count += 1
                    self.enabled = False
                    log.warning(
                        "DirectRadioGps connect failed on %s: %s; retrying in %.1fs (fail #%d)",
                        self.dev_path,
                        last_err,
                        self.reconnect_sec,
                        self._open_fail_count,
                    )
                    self._stop.wait(self.reconnect_sec)
                    continue

            try:
                raw = self._serial.readline()
                if not raw:
                    continue
                if isinstance(raw, bytes):
                    line = raw.decode("utf-8", errors="replace").strip()
                else:
                    line = str(raw).strip()
                if line:
                    self._handle_line(line)
            except Exception as e:
                log.warning("DirectRadioGps reader error: %s; reconnecting", e)
                self._close_serial()
                self.enabled = False
                with self._lock:
                    self._latest = None
                    self._target_telemetry = {
                        "target_battery_mv": None,
                        "target_sats": None,
                    }
                self._stop.wait(self.reconnect_sec)

    def _handle_line(self, line: str, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return

        if _flag(data.get("base")):
            self._handle_base_line(data, now)
            return

        if "seq" in data:
            self._handle_remote_line(data, now)

    def _handle_base_line(self, data: dict, now: float) -> None:
        cam = None
        if _flag(data.get("fix")) and _flag(data.get("stable")):
            lat = _e7_to_deg(data.get("lat_e7"))
            lon = _e7_to_deg(data.get("lon_e7"))
            if lat is None or lon is None:
                with self._lock:
                    self._last_poll_ts = now
                return
            cam = (lat, lon, _float_value(data.get("alt_m")))

        # H9: raw_lat/raw_lon are the instantaneous fix (1e7-scaled), gated on
        # "fix" only (not "stable") -- same contract as the tracker seq line.
        # Older firmware that doesn't emit them leaves _cam_raw at its last value.
        cam_raw = None
        if _flag(data.get("fix")) and "raw_lat" in data and "raw_lon" in data:
            raw_lat = _e7_to_deg(data.get("raw_lat"))
            raw_lon = _e7_to_deg(data.get("raw_lon"))
            if raw_lat is not None and raw_lon is not None:
                cam_raw = (raw_lat, raw_lon, _float_value(data.get("alt_m")))

        with self._lock:
            if cam is not None:
                self._cam = cam
                self._cam_ts = now
            if cam_raw is not None:
                self._cam_raw = cam_raw
                self._cam_raw_ts = now
            self._last_poll_ts = now

    def _handle_remote_line(self, data: dict, now: float) -> None:
        fix = None
        telemetry = {
            "target_battery_mv": _int_value(data.get("batt_mv")),
            "target_sats": _int_value(data.get("sats")),
        }
        if _flag(data.get("fix")):
            lat = _e7_to_deg(data.get("lat_e7"))
            lon = _e7_to_deg(data.get("lon_e7"))
            if lat is None or lon is None:
                # Corrupt/partial line (fix flag set, coords unparseable): keep the
                # last-known-good fix instead of erasing it. get_fix() re-ages it
                # from its ts and the downstream age gate (drive_stale_sec) drops it
                # once stale, so a transient bad packet no longer drops the track.
                with self._lock:
                    self._target_telemetry = telemetry
                    self._last_poll_ts = now
                return
            gps_age_sec = max(0.0, _float_value(data.get("gps_age_ms")) / 1000.0)
            ts = now - gps_age_sec
            # M8 (audit 2026-07-01): honor optional spd_ok/crs_ok validity flags.
            # Absent => assume valid (backward compatible with older firmware).
            # When false, null course / zero speed instead of reporting the raw
            # memset-0 field as if it were real data (course=0 == due north).
            spd_ok = _flag_or_default(data, "spd_ok", True)
            crs_ok = _flag_or_default(data, "crs_ok", True)
            course = (_float_value(data.get("course_cdeg")) / 100.0) % 360.0 if crs_ok else 0.0
            speed = _float_value(data.get("speed_cm_s")) / 100.0 if spd_ok else 0.0
            # M9 (audit 2026-07-01): hacc_cm -> h_acc_m (None when absent so callers
            # can distinguish "unknown" from "0m accurate").
            hacc_cm = _float_value(data.get("hacc_cm"), default=-1.0)
            h_acc_m = (hacc_cm / 100.0) if hacc_cm >= 0.0 else None
            fix = NormalizedFix(
                lat=lat,
                lon=lon,
                course=course,
                speed=speed,
                ts=ts,
                age_sec=gps_age_sec,
                src="direct_lora",
                h_acc_m=h_acc_m,
            )

        with self._lock:
            if fix is not None:
                # Real fix → refresh the position and stamp when it was good.
                self._latest = fix
                self._last_fix_ok_ts = now
            else:
                # Honest no-fix (fix flag clear): GPS-1 — KEEP the last fix and let
                # get_fix() coast on it for coast_on_no_fix_sec, then drop. Telemetry
                # (battery/sats) is still valid and updates. With coast=0 this clears
                # on the next get_fix() (old behavior).
                self._latest_no_fix_at = now
            self._target_telemetry = telemetry
            self._last_poll_ts = now

    def reader_alive(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive() and self.enabled

    def last_poll_age_sec(self) -> Optional[float]:
        with self._lock:
            ts = self._last_poll_ts
        if ts is None:
            return None
        return max(0.0, time.time() - ts)

    def get_fix(self, now: Optional[float] = None) -> Optional[NormalizedFix]:
        now = time.time() if now is None else now
        with self._lock:
            fix = self._latest
            no_fix_at = self._latest_no_fix_at
            ok_ts = self._last_fix_ok_ts
        if fix is None:
            return None
        # GPS-1 coast: if the latest packet was an honest no-fix, keep returning the
        # last good fix only until coast_on_no_fix_sec past when it was last good; then
        # drop it (the downstream drive_stale_sec gate also still applies via age_sec).
        if no_fix_at is not None and (ok_ts is None or no_fix_at >= ok_ts):
            if (now - (ok_ts if ok_ts is not None else no_fix_at)) > self.coast_on_no_fix_sec:
                return None
        return replace(fix, age_sec=max(0.0, now - fix.ts))

    def get_camera_position(self) -> Optional[Tuple[float, float, float]]:
        """The base/camera reference position.

        H9 (audit 2026-07-01): prefer the INSTANTANEOUS raw fix (raw_lat/raw_lon)
        when the firmware has emitted one -- the settled running mean (_cam) is
        unbounded and freezes after ~30 min, which silently defeats
        BaseDriftMonitor (this is the only read seam it consumes). Falls back to
        the mean on older firmware that doesn't emit raw_lat/raw_lon, or before
        the first raw sample arrives.
        """
        with self._lock:
            return self._cam_raw if self._cam_raw is not None else self._cam

    def get_camera_age(self, now: Optional[float] = None) -> Optional[float]:
        """Age of whichever position get_camera_position() would return (raw
        fix when present, else the settled mean) so freshness gates that pair
        the two calls (e.g. CALIBRATE's live-base check) stay consistent."""
        with self._lock:
            if self._cam_raw is not None:
                cam, ts = self._cam_raw, self._cam_raw_ts
            else:
                cam, ts = self._cam, self._cam_ts
        if cam is None or ts <= 0:
            return None
        return max(0.0, (time.time() if now is None else now) - ts)

    def get_target_telemetry(self) -> dict[str, int | None]:
        """Latest tracker-side telemetry from the remote packet."""
        with self._lock:
            return dict(self._target_telemetry)

    def _close_serial(self) -> None:
        ser = self._serial
        self._serial = None
        if ser is None:
            return
        try:
            ser.close()
        except Exception:
            pass

    def close(self) -> None:
        self._stop.set()
        self._close_serial()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        self.enabled = False
