"""Audit 2026-07-01 Wave 3 — M16 (kill doesn't block on media teardown),
L4 (sensors ingest gated CONFIG), L5 (/tune persists via /config/hot's path),
L7 (config revision check + restart scheduling are atomic under api._lock),
H1 (agent_kill() terminates an in-flight armed turn, not just ArmState).
"""
from __future__ import annotations

import os
import sys
import threading
import time

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))
from test_control_api import DummyPipeline, _agent_pipe, wait_until  # noqa: E402

from wavecam.auth import AuthConfig  # noqa: E402
from wavecam.web import build_app  # noqa: E402


class SlowRecorder:
    """A DummyRecorder-shaped fake whose stop() blocks, standing in for the
    real ffmpeg terminate()+wait(5)+kill() teardown M16 is about."""

    def __init__(self, block_sec: float):
        self._block_sec = block_sec
        self.stop_calls = 0
        self.stop_started = threading.Event()
        self.stop_finished = threading.Event()
        import types
        from pathlib import Path
        self.config = types.SimpleNamespace(rec_dir=Path("/tmp/wavecam-test-recordings"))
        self.media = {"recording": True, "segment_name": None, "current_segment_name": None,
                      "segment_pattern": None, "segment_prefix": None, "free_gb": 123.4,
                      "segments": 0, "latest": []}

    def status(self):
        return dict(self.media)

    def stop(self):
        self.stop_started.set()
        self.stop_calls += 1
        time.sleep(self._block_sec)
        self.media["recording"] = False
        self.stop_finished.set()
        return {"ok": True, "stopped": True}


def test_safety_kill_returns_before_slow_media_teardown_completes():
    # M16: kill_for_safety() used to call media.stop_for_safety() synchronously,
    # so a ~5s ffmpeg terminate/wait/kill sequence stalled the /safety/kill HTTP
    # response. It must now run on a daemon thread: the request returns promptly
    # and PTZ is ALREADY stopped (pipeline.kill() is synchronous) even though the
    # recorder is still tearing down in the background.
    pipe = DummyPipeline()
    slow = SlowRecorder(block_sec=1.5)
    pipe.recorder = slow
    client = TestClient(build_app(pipe))

    start = time.monotonic()
    resp = client.post("/api/v1/safety/kill", json={"reason": "test"})
    elapsed = time.monotonic() - start

    assert resp.json()["ok"] is True
    assert elapsed < 1.0, f"kill blocked {elapsed:.2f}s on media teardown"
    # Motion stop already happened synchronously inside pipeline.kill().
    assert ("stop",) in pipe.ptz.calls
    assert pipe.owner.killed is True
    # The teardown is still running (or about to finish) on its own thread.
    assert wait_until(lambda: slow.stop_finished.is_set(), timeout_sec=3.0)
    assert slow.stop_calls == 1


# --- L4: /sensors/phone ingest requires CONFIG, not READ ---

def test_sensors_phone_post_requires_config_not_read():
    client = TestClient(build_app(DummyPipeline()))
    client.app.state.auth = AuthConfig(enabled=True, tokens={"v": "viewer", "s": "supervisor"})
    viewer_blocked = client.post(
        "/api/v1/sensors/phone", json={"bump": False},
        headers={"Authorization": "Bearer v"},
    )
    supervisor_ok = client.post(
        "/api/v1/sensors/phone", json={"bump": False},
        headers={"Authorization": "Bearer s"},
    )
    assert viewer_blocked.status_code == 403
    assert viewer_blocked.json()["code"] == "forbidden"
    assert supervisor_ok.status_code == 200


def test_sensors_phone_ws_requires_config_not_read():
    client = TestClient(build_app(DummyPipeline()))
    client.app.state.auth = AuthConfig(enabled=True, tokens={"v": "viewer", "s": "supervisor"})
    # Viewer (READ-only) must be refused the sensor-ingest websocket.
    with client.websocket_connect("/api/v1/sensors/phone/ws?token=v") as ws:
        # The server closes with code 1008 on an unauthorized connect.
        import pytest
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()


# --- L5: legacy /tune persists via the same path as /config/hot ---

def test_tune_persists_like_config_hot(tmp_path, monkeypatch):
    pipe = DummyPipeline()
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("ptz:\n  deadzone: 0.08\n")
    pipe.cfg.source_path = str(yaml_path)
    client = TestClient(build_app(pipe))

    resp = client.post("/tune", json={"deadzone": 0.15})

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert pipe.cfg.ptz.deadzone == 0.15
    overlay = tmp_path / "config.local.yaml"
    assert overlay.exists(), "/tune must persist through the same overlay path as /config/hot"
    assert "0.15" in overlay.read_text()


def test_config_hot_persist_field_true_is_accepted_noop(tmp_path):
    pipe = DummyPipeline()
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("ptz:\n  deadzone: 0.08\n")
    pipe.cfg.source_path = str(yaml_path)
    client = TestClient(build_app(pipe))

    resp = client.post(
        "/api/v1/config/hot",
        json={"persist": True, "patch": {"ptz.deadzone": 0.2}},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert pipe.cfg.ptz.deadzone == 0.2


# --- L7: config revision check + apply happen atomically under api._lock ---

def test_config_hot_concurrent_requests_do_not_both_pass_stale_revision(tmp_path):
    """A crude concurrency smoke test: fire two hot-config POSTs with the SAME
    (now-stale-after-the-first-applies) revision from two threads. At most one
    may succeed — the revision check + apply must be atomic (L7), not two
    separate lock acquisitions an interleaving could slip between."""
    pipe = DummyPipeline()
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("ptz:\n  deadzone: 0.08\n")
    pipe.cfg.source_path = str(yaml_path)
    client = TestClient(build_app(pipe))

    before_rev = client.get("/api/v1/config").json()["revision"]
    results = []

    def fire(deadzone):
        r = client.post(
            "/api/v1/config/hot",
            json={"revision": before_rev, "patch": {"ptz.deadzone": deadzone}},
        )
        results.append(r.status_code)

    t1 = threading.Thread(target=fire, args=(0.11,))
    t2 = threading.Thread(target=fire, args=(0.12,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Exactly one request should observe the original revision and succeed;
    # the other must see a revision_conflict once the first has applied.
    assert sorted(results) == [200, 409]


def test_system_restart_concurrent_requests_schedule_only_once(tmp_path):
    """L7: two concurrent /system/restart posts must not both schedule a timer."""
    pipe = DummyPipeline()
    client = TestClient(build_app(pipe))
    results = []

    def fire():
        r = client.post(
            "/api/v1/system/restart",
            json={"reason": "concurrent", "delay_seconds": 0.2},
        )
        results.append(r.status_code)

    t1 = threading.Thread(target=fire)
    t2 = threading.Thread(target=fire)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert sorted(results) == [202, 409]
    assert wait_until(lambda: pipe.restart_calls == ["wavecam.service"], timeout_sec=1.0)
    # Only one restart call, even though two requests raced.
    assert pipe.restart_calls == ["wavecam.service"]


# --- H1: /safety/kill terminates an in-flight armed agent turn, not just ArmState ---

def test_safety_kill_calls_agent_session_terminate():
    pipe = _agent_pipe(True)
    client = TestClient(build_app(pipe))
    adapter = client.app.state.control_api
    session = adapter._system._agent_session
    assert session is not None

    calls = []
    session.terminate = lambda: calls.append(True) or True  # spy, replaces the real method

    client.post("/api/v1/agent/arm", json={"armed": True})
    resp = client.post("/api/v1/safety/kill", json={"reason": "test"})

    assert resp.json()["ok"] is True
    assert calls == [True], "agent_kill() must call AgentSession.terminate()"
    assert client.get("/api/v1/status").json()["agent"]["armed"] is False
