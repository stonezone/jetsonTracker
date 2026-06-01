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
        self.config.rec_dir.mkdir(parents=True, exist_ok=True)

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, segment_seconds: int | None = None) -> dict:
        if self.is_running():
            return {"ok": True, "already": True}

        seconds = int(segment_seconds or self.config.segment_seconds)
        segment_name = f"wavecam_{self._now()}_%03d.mp4"
        pattern = self.config.rec_dir / segment_name
        cmd = self._command(pattern, seconds)
        self._proc = self._popen(cmd)
        return {"ok": True, "started": True, "segment_name": segment_name}

    def stop(self) -> dict:
        if not self.is_running():
            self._proc = None
            return {"ok": True, "already_stopped": True}

        assert self._proc is not None
        killed = False
        self._proc.terminate()
        try:
            self._proc.wait(timeout=self.config.stop_timeout_sec)
        except Exception:
            self._proc.kill()
            killed = True
        self._proc = None

        result = {"ok": True, "stopped": True}
        if killed:
            result["killed"] = True
        return result

    def status(self) -> dict:
        segments = self._segments()
        total_bytes = sum(path.stat().st_size for path in segments)
        disk = shutil.disk_usage(self.config.rec_dir)
        latest = [path.name for path in segments[-5:]]
        return {
            "recording": self.is_running(),
            "dir": str(self.config.rec_dir),
            "segments": len(segments),
            "segment_name": latest[-1] if latest else None,
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

    @staticmethod
    def _default_now() -> str:
        return time.strftime("%Y%m%d_%H%M%S")

    @staticmethod
    def _default_popen(cmd: Sequence[str]) -> ProcessLike:
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
