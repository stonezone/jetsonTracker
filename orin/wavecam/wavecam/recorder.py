"""Segmented local recorder for the WaveCam main RTSP stream.

The recorder is a small process-lifecycle wrapper around ffmpeg. It remuxes the
camera main stream with ``-c copy`` into MP4 segments on local storage. It does
not encode video, so it avoids NVENC assumptions and keeps the Orin workload
bounded.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import threading
import time
from typing import Callable, Protocol, Sequence


class ProcessLike(Protocol):
    def poll(self): ...
    def terminate(self) -> None: ...
    def wait(self, timeout: float | None = None): ...
    def kill(self) -> None: ...


PopenFactory = Callable[[Sequence[str]], ProcessLike]
Clock = Callable[[], str]


@dataclass(frozen=True)
class RecorderConfig:
    rec_dir: Path = Path("/data/recordings")
    rtsp_main: str = "rtsp://192.168.100.88:554/1"
    segment_seconds: int = 600
    ffmpeg_bin: str = "ffmpeg"
    stop_timeout_sec: float = 5.0


class Recorder:
    """Owns one ffmpeg recorder subprocess and reports segment status."""

    def __init__(
        self,
        config: RecorderConfig | None = None,
        *,
        popen: PopenFactory | None = None,
        now: Clock | None = None,
    ) -> None:
        self.config = config or RecorderConfig()
        self._popen = popen or self._default_popen
        self._now = now or self._default_now
        self._proc: ProcessLike | None = None
        self._active_segment_pattern: str | None = None
        self._active_segment_prefix: str | None = None
        # R13 (audit round-2): serialize start/stop/status against the async
        # /safety/kill teardown (M16 — media.stop_for_safety() runs recorder.stop()
        # on a daemon thread while the HTTP response returns immediately). Without
        # this lock, a quick RESUME -> media/start POST within that ~stop_timeout_sec
        # teardown window could race stop()'s `self._proc = None` and drop the NEW
        # recording's handle (orphan ffmpeg, recorder reports not-recording, a
        # second start spawns a second ffmpeg).
        self._lock = threading.Lock()
        self.config.rec_dir.mkdir(parents=True, exist_ok=True)

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, segment_seconds: int | None = None) -> dict:
        with self._lock:
            if self.is_running():
                return {
                    "ok": True,
                    "already": True,
                    "segment_pattern": self._active_segment_pattern,
                    "segment_prefix": self._active_segment_prefix,
                    "segment_name": self._current_segment_name(),
                }

            seconds = int(segment_seconds or self.config.segment_seconds)
            segment_prefix = f"wavecam_{self._now()}_"
            segment_pattern = f"{segment_prefix}%03d.mp4"
            pattern = self.config.rec_dir / segment_pattern
            cmd = self._command(pattern, seconds)
            self._proc = self._popen(cmd)
            # Verify ffmpeg is still alive a moment after spawn — a missing binary,
            # bad RTSP source, or full disk can kill it instantly.
            time.sleep(0.25)
            if self._proc.poll() is not None:
                self._proc = None
                # R22 (audit round-2): this used to report ok:true alongside
                # started:false. The /api/v1/media/record/start route already special-
                # cases started is False into a 503 refusal (REC-1), but any other
                # caller reading this dict directly would see a false "ok" — the
                # envelope's own ok field must agree with started.
                return {
                    "ok": False,
                    "started": False,
                    "error": "ffmpeg exited immediately (check RTSP source and path)",
                }
            self._active_segment_prefix = segment_prefix
            self._active_segment_pattern = segment_pattern
            return {
                "ok": True,
                "started": True,
                "segment_pattern": self._active_segment_pattern,
                "segment_prefix": self._active_segment_prefix,
                "segment_name": None,
            }

    def stop(self) -> dict:
        with self._lock:
            if not self.is_running():
                self._proc = None
                self._active_segment_pattern = None
                self._active_segment_prefix = None
                return {"ok": True, "already_stopped": True}

            proc = self._proc
            assert proc is not None
            killed = False
            proc.terminate()
            try:
                proc.wait(timeout=self.config.stop_timeout_sec)
            except Exception:
                proc.kill()
                killed = True
            # R13: only drop the tracked handle (and segment bookkeeping) if it is
            # still the EXACT process we just terminated. Held under self._lock this
            # can't actually race a concurrent start() any more (that call blocks on
            # the lock until this returns) — the identity check is defense-in-depth
            # against any future caller that mutates self._proc without the lock.
            if self._proc is proc:
                self._proc = None
                self._active_segment_pattern = None
                self._active_segment_prefix = None

            result = {"ok": True, "stopped": True}
            if killed:
                result["killed"] = True
            return result

    def status(self) -> dict:
        with self._lock:
            recording = self.is_running()
            if not recording:
                self._active_segment_pattern = None
                self._active_segment_prefix = None

            segments = self._segments()
            total_bytes = sum(path.stat().st_size for path in segments)
            disk = shutil.disk_usage(self.config.rec_dir)
            latest = [path.name for path in segments[-5:]]
            current_segment_name = self._current_segment_name() if recording else None
            return {
                "recording": recording,
                "dir": str(self.config.rec_dir),
                "segments": len(segments),
                "segment_name": current_segment_name if recording else (latest[-1] if latest else None),
                "current_segment_name": current_segment_name,
                "segment_pattern": self._active_segment_pattern,
                "segment_prefix": self._active_segment_prefix,
                "latest": latest,
                "total_mb": round(total_bytes / 1_000_000, 1) if segments else 0.0,
                "free_gb": round(disk.free / 1_000_000_000, 1),
            }

    def _command(self, pattern: Path, segment_seconds: int) -> list[str]:
        return [
            self.config.ffmpeg_bin,
            "-nostdin",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-i",
            self.config.rtsp_main,
            "-c",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            str(segment_seconds),
            "-reset_timestamps",
            "1",
            "-movflags",
            "+faststart",
            str(pattern),
        ]

    def _segments(self) -> list[Path]:
        return sorted(self.config.rec_dir.glob("*.mp4"))

    def _current_segment_name(self) -> str | None:
        if not self._active_segment_prefix:
            return None
        active_segments = sorted(self.config.rec_dir.glob(f"{self._active_segment_prefix}*.mp4"))
        return active_segments[-1].name if active_segments else None

    @staticmethod
    def _default_now() -> str:
        return time.strftime("%Y%m%d_%H%M%S")

    @staticmethod
    def _default_popen(cmd: Sequence[str]) -> ProcessLike:
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main_stream_from_detection_source(source) -> str:
    """Return the full-quality RTSP stream paired with the detection source.

    The Prisual uses ``/2`` for the sub-stream used by detection and ``/1`` for
    the main recording stream. Non-RTSP or non-Prisual-like sources fall back to
    the default main stream so recorder startup remains deterministic.
    """
    if not isinstance(source, str) or not source.startswith("rtsp://"):
        return RecorderConfig().rtsp_main

    normalized = source.rstrip("/")
    base, _, tail = normalized.rpartition("/")
    if tail == "2" and base:
        return f"{base}/1"
    return normalized
