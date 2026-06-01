from __future__ import annotations

import subprocess
from pathlib import Path

from wavecam.recorder import Recorder, RecorderConfig, main_stream_from_detection_source


class FakeProcess:
    def __init__(self, *, running: bool = True, wait_raises: bool = False):
        self._running = running
        self.wait_raises = wait_raises
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if self._running else 0

    def terminate(self):
        self.terminated = True
        self._running = False

    def wait(self, timeout=None):
        if self.wait_raises:
            self._running = True
            raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout)
        self._running = False
        return 0

    def kill(self):
        self.killed = True
        self._running = False


class FakePopenFactory:
    def __init__(self, process: FakeProcess | None = None):
        self.process = process or FakeProcess()
        self.commands: list[list[str]] = []

    def __call__(self, cmd, stdout=None, stderr=None):
        self.commands.append(list(cmd))
        return self.process


def make_recorder(tmp_path: Path, popen: FakePopenFactory | None = None) -> Recorder:
    return Recorder(
        RecorderConfig(
            rec_dir=tmp_path,
            rtsp_main="rtsp://192.168.100.88:554/1",
            segment_seconds=120,
        ),
        popen=popen or FakePopenFactory(),
        now=lambda: "20260601_120000",
    )


def test_start_launches_rtsp_main_stream_copy_segments(tmp_path: Path):
    popen = FakePopenFactory()
    recorder = make_recorder(tmp_path, popen)

    result = recorder.start()

    assert result["ok"] is True
    assert result["started"] is True
    assert result["segment_name"] == "wavecam_20260601_120000_%03d.mp4"
    assert len(popen.commands) == 1
    cmd = popen.commands[0]
    assert cmd[:6] == ["ffmpeg", "-nostdin", "-loglevel", "error", "-rtsp_transport", "tcp"]
    assert cmd[cmd.index("-i") + 1] == "rtsp://192.168.100.88:554/1"
    assert cmd[cmd.index("-c") + 1] == "copy"
    assert cmd[cmd.index("-f") + 1] == "segment"
    assert cmd[cmd.index("-segment_time") + 1] == "120"
    assert "nvenc" not in " ".join(cmd).lower()
    assert cmd[-1] == str(tmp_path / "wavecam_20260601_120000_%03d.mp4")


def test_start_is_idempotent_while_process_is_running(tmp_path: Path):
    popen = FakePopenFactory()
    recorder = make_recorder(tmp_path, popen)

    recorder.start()
    second = recorder.start()

    assert second == {"ok": True, "already": True}
    assert len(popen.commands) == 1


def test_stop_terminates_running_process(tmp_path: Path):
    process = FakeProcess(running=True)
    recorder = make_recorder(tmp_path, FakePopenFactory(process))
    recorder.start()

    result = recorder.stop()

    assert result == {"ok": True, "stopped": True}
    assert process.terminated is True
    assert process.killed is False
    assert recorder.is_running() is False


def test_stop_kills_process_after_timeout(tmp_path: Path):
    process = FakeProcess(running=True, wait_raises=True)
    recorder = make_recorder(tmp_path, FakePopenFactory(process))
    recorder.start()

    result = recorder.stop()

    assert result == {"ok": True, "stopped": True, "killed": True}
    assert process.terminated is True
    assert process.killed is True
    assert recorder.is_running() is False


def test_status_reports_latest_segment_and_disk_space(tmp_path: Path):
    older = tmp_path / "wavecam_20260601_115900_000.mp4"
    latest = tmp_path / "wavecam_20260601_120000_000.mp4"
    older.write_bytes(b"a" * 3)
    latest.write_bytes(b"b" * 5)
    popen = FakePopenFactory()
    recorder = make_recorder(tmp_path, popen)
    recorder.start()

    status = recorder.status()

    assert status["recording"] is True
    assert status["segment_name"] == latest.name
    assert status["segments"] == 2
    assert status["latest"] == [older.name, latest.name]
    assert status["total_mb"] == 0.0
    assert isinstance(status["free_gb"], float)
    assert status["dir"] == str(tmp_path)


def test_main_stream_derives_prisual_main_rtsp_from_detection_substream():
    assert (
        main_stream_from_detection_source("rtsp://192.168.100.88:554/2")
        == "rtsp://192.168.100.88:554/1"
    )
    assert (
        main_stream_from_detection_source("rtsp://192.168.100.88:554/2/")
        == "rtsp://192.168.100.88:554/1"
    )
    custom = "rtsp://camera.local/live/main"
    assert main_stream_from_detection_source(custom) == custom
    assert main_stream_from_detection_source(0) == RecorderConfig().rtsp_main
