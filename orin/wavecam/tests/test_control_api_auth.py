from __future__ import annotations

import json
import os
import sys

from fastapi.testclient import TestClient

from wavecam.auth import AuthConfig, load_auth
from wavecam.web import build_app

sys.path.insert(0, os.path.dirname(__file__))
from test_control_api import DummyPipeline  # noqa: E402  (reuse sibling test's fixture)


def client_with_auth(enabled: bool, tokens: dict[str, str] | None = None) -> TestClient:
    client = TestClient(build_app(DummyPipeline()))
    client.app.state.auth = AuthConfig(enabled=enabled, tokens=tokens or {})
    return client


def hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_auth_disabled_allows_all():
    # Default/unconfigured auth must preserve the open behavior (live app has no token).
    client = client_with_auth(enabled=False)
    assert client.get("/api/v1/status").status_code == 200
    assert client.post("/api/v1/safety/kill", json={}).json()["ok"] is True


def test_enabled_requires_token():
    client = client_with_auth(True, {"op": "operator"})
    body = client.get("/api/v1/status")
    assert body.status_code == 401
    assert body.json()["code"] == "unauthorized"


def test_bad_token_rejected():
    client = client_with_auth(True, {"op": "operator"})
    r = client.post("/api/v1/safety/kill", json={}, headers=hdr("nope"))
    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"


def test_legacy_mutation_routes_require_token_when_auth_enabled():
    client = client_with_auth(True, {"op": "operator"})
    cases = [
        ("post", "/kill", {}),
        ("post", "/resume", {}),
        ("post", "/ptz/stop", {}),
        ("post", "/ptz/zin", {}),
        ("post", "/ptz/zout", {}),
        ("post", "/ptz/zstop", {}),
        ("post", "/tune", {"deadzone": 0.1}),
    ]

    for method, path, body in cases:
        response = getattr(client, method)(path, json=body)
        assert response.status_code == 401, path
        assert response.json()["code"] == "unauthorized"


def test_operator_can_use_legacy_mutation_routes_when_auth_enabled():
    client = client_with_auth(True, {"op": "operator"})
    cases = [
        ("post", "/kill", {}),
        ("post", "/resume", {}),
        ("post", "/ptz/stop", {}),
        ("post", "/ptz/zin", {}),
        ("post", "/ptz/zout", {}),
        ("post", "/ptz/zstop", {}),
        ("post", "/tune", {"deadzone": 0.1}),
    ]

    for method, path, body in cases:
        response = getattr(client, method)(path, json=body, headers=hdr("op"))
        assert response.status_code == 200, path


def test_operator_allows_read_safety_ptz_config():
    client = client_with_auth(True, {"op": "operator"})
    assert client.get("/api/v1/status", headers=hdr("op")).status_code == 200
    assert client.post("/api/v1/safety/kill", json={}, headers=hdr("op")).status_code == 200
    assert client.post("/api/v1/safety/resume", json={}, headers=hdr("op")).status_code == 200
    assert client.post(
        "/api/v1/ptz/velocity",
        json={"requested_owner": "manual", "pan": 0.2, "tilt": 0.0, "zoom": 0.0},
        headers=hdr("op"),
    ).status_code == 200
    assert client.post(
        "/api/v1/agent/summon",
        json={"source": "test", "reason": "operator_diagnostics"},
        headers=hdr("op"),
    ).status_code == 202
    assert client.get("/api/v1/calibration", headers=hdr("op")).status_code == 200
    assert client.post(
        "/api/v1/calibration/heading",
        json={"requested_owner": "manual", "heading_deg": 180.0},
        headers=hdr("op"),
    ).status_code == 200


def test_viewer_can_read_but_not_kill():
    client = client_with_auth(True, {"v": "viewer"})
    assert client.get("/api/v1/status", headers=hdr("v")).status_code == 200
    assert client.get("/api/v1/media/status", headers=hdr("v")).status_code == 200
    assert client.get("/api/v1/calibration", headers=hdr("v")).status_code == 200
    blocked = client.post("/api/v1/safety/kill", json={}, headers=hdr("v"))
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "forbidden"
    calibration_blocked = client.post(
        "/api/v1/calibration/heading",
        json={"requested_owner": "manual", "heading_deg": 180.0},
        headers=hdr("v"),
    )
    assert calibration_blocked.status_code == 403
    record_blocked = client.post("/api/v1/media/record/start", json={}, headers=hdr("v"))
    assert record_blocked.status_code == 403
    restart_blocked = client.post(
        "/api/v1/system/restart",
        json={"reason": "viewer"},
        headers=hdr("v"),
    )
    assert restart_blocked.status_code == 403
    summon_blocked = client.post(
        "/api/v1/agent/summon",
        json={"source": "test", "reason": "viewer"},
        headers=hdr("v"),
    )
    assert summon_blocked.status_code == 403


def test_viewer_can_list_and_download_media(tmp_path):
    client = client_with_auth(True, {"v": "viewer"})
    pipe = client.app.state.pipeline
    pipe.recorder.config.rec_dir = tmp_path
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"clip")

    listed = client.get("/api/v1/media/list", headers=hdr("v"))
    downloaded = client.get(f"/api/v1/media/download/{clip.name}", headers=hdr("v"))
    unauthenticated = client.get("/api/v1/media/list")

    assert listed.status_code == 200
    assert listed.json()["files"][0]["name"] == "clip.mp4"
    assert downloaded.status_code == 200
    assert downloaded.content == b"clip"
    assert unauthenticated.status_code == 401


def test_supervisor_no_direct_ptz_but_config_ok():
    client = client_with_auth(True, {"s": "supervisor"})
    assert client.post("/api/v1/ptz/stop", json={}, headers=hdr("s")).status_code == 403
    ok = client.post("/api/v1/config/hot", json={"patch": {"ptz.deadzone": 0.1}}, headers=hdr("s"))
    assert ok.status_code == 200
    record = client.post("/api/v1/media/record/start", json={}, headers=hdr("s"))
    assert record.status_code == 200
    restart = client.post(
        "/api/v1/system/restart",
        json={"reason": "supervisor", "delay_seconds": 0.0},
        headers=hdr("s"),
    )
    assert restart.status_code == 202
    summon = client.post(
        "/api/v1/agent/summon",
        json={"source": "test", "reason": "supervisor"},
        headers=hdr("s"),
    )
    assert summon.status_code == 202


def test_agent_is_read_only_in_v1():
    client = client_with_auth(True, {"a": "agent"})
    assert client.get("/api/v1/status", headers=hdr("a")).status_code == 200
    blocked = client.post(
        "/api/v1/ptz/velocity",
        json={"requested_owner": "manual", "pan": 0.2, "tilt": 0.0, "zoom": 0.0},
        headers=hdr("a"),
    )
    assert blocked.status_code == 403
    summon_blocked = client.post(
        "/api/v1/agent/summon",
        json={"source": "test", "reason": "agent"},
        headers=hdr("a"),
    )
    assert summon_blocked.status_code == 403


def test_load_auth_missing_file_disabled(tmp_path):
    assert load_auth(str(tmp_path / "nope.json")).enabled is False


def test_load_auth_reads_tokens(tmp_path):
    p = tmp_path / "auth.json"
    p.write_text(json.dumps({"enabled": True, "tokens": {"x": "operator"}}))
    cfg = load_auth(str(p))
    assert cfg.enabled is True
    assert cfg.role_for("x") == "operator"


def test_load_auth_malformed_fails_open(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{ not json")
    assert load_auth(str(p)).enabled is False
