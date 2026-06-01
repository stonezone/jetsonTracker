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


def test_viewer_can_read_but_not_kill():
    client = client_with_auth(True, {"v": "viewer"})
    assert client.get("/api/v1/status", headers=hdr("v")).status_code == 200
    blocked = client.post("/api/v1/safety/kill", json={}, headers=hdr("v"))
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "forbidden"


def test_supervisor_no_direct_ptz_but_config_ok():
    client = client_with_auth(True, {"s": "supervisor"})
    assert client.post("/api/v1/ptz/stop", json={}, headers=hdr("s")).status_code == 403
    ok = client.post("/api/v1/config/hot", json={"patch": {"ptz.deadzone": 0.1}}, headers=hdr("s"))
    assert ok.status_code == 200


def test_agent_is_read_only_in_v1():
    client = client_with_auth(True, {"a": "agent"})
    assert client.get("/api/v1/status", headers=hdr("a")).status_code == 200
    blocked = client.post(
        "/api/v1/ptz/velocity",
        json={"requested_owner": "manual", "pan": 0.2, "tilt": 0.0, "zoom": 0.0},
        headers=hdr("a"),
    )
    assert blocked.status_code == 403


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
