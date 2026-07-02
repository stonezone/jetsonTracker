"""Audit 2026-07-01 Wave 3 — C2 (agent auth), L6 (refusal shape), M19 (status.agent).

C2: /agent/arm, /agent/chat, /agent/summon must refuse when auth is globally
disabled AND cfg.agent.allow_unauthenticated is false. Default (attribute absent,
mirroring every existing test fixture and any cfg predating this field) stays
PERMISSIVE so the whole existing agent test suite (auth disabled, no
allow_unauthenticated opinion) keeps working unchanged; the rig closes the hole
by setting allow_unauthenticated: false explicitly.
"""
from __future__ import annotations

import os
import sys
import types

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))
from test_control_api import DummyPipeline, _agent_pipe, _stub_claude  # noqa: E402

from wavecam.web import build_app  # noqa: E402


def _agent_pipe_with_auth_flag(enabled: bool, allow_unauthenticated) -> DummyPipeline:
    pipe = _agent_pipe(enabled)
    pipe.cfg.agent = types.SimpleNamespace(
        enabled=enabled, model="", arm_ttl_sec=600.0, mcp_config_path="",
        allow_unauthenticated=allow_unauthenticated,
    )
    return pipe


def test_agent_chat_refused_when_auth_disabled_and_not_allowed(monkeypatch):
    _stub_claude(monkeypatch)
    pipe = _agent_pipe_with_auth_flag(True, allow_unauthenticated=False)
    client = TestClient(build_app(pipe))
    resp = client.post("/api/v1/agent/chat", json={"message": "hi"})
    assert resp.status_code == 401
    body = resp.json()
    assert body["ok"] is False and body["code"] == "auth_required"


def test_agent_arm_refused_when_auth_disabled_and_not_allowed():
    pipe = _agent_pipe_with_auth_flag(True, allow_unauthenticated=False)
    client = TestClient(build_app(pipe))
    resp = client.post("/api/v1/agent/arm", json={"armed": True})
    assert resp.status_code == 401
    assert resp.json()["code"] == "auth_required"
    # Confirm the refusal actually blocked the arm — the agent must not be armed.
    assert client.get("/api/v1/status").json()["agent"]["armed"] is False


def test_agent_summon_refused_when_auth_disabled_and_not_allowed():
    pipe = _agent_pipe_with_auth_flag(True, allow_unauthenticated=False)
    client = TestClient(build_app(pipe))
    resp = client.post(
        "/api/v1/agent/summon", json={"source": "test", "reason": "probe"}
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "auth_required"


def test_agent_chat_allowed_when_explicitly_permissive(monkeypatch):
    _stub_claude(monkeypatch, reply="fine")
    pipe = _agent_pipe_with_auth_flag(True, allow_unauthenticated=True)
    client = TestClient(build_app(pipe))
    resp = client.post("/api/v1/agent/chat", json={"message": "hi"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_agent_routes_default_permissive_when_field_absent(monkeypatch):
    # Back-compat: cfg.agent with NO allow_unauthenticated attribute at all
    # (every existing test fixture, and any cfg predating this audit fix) must
    # keep working — the default is permissive, not a refusal.
    _stub_claude(monkeypatch)
    pipe = _agent_pipe(True)  # no allow_unauthenticated attribute set
    client = TestClient(build_app(pipe))
    assert client.post("/api/v1/agent/chat", json={"message": "hi"}).status_code == 200
    assert client.post("/api/v1/agent/arm", json={"armed": True}).status_code == 200


def test_agent_routes_unaffected_when_global_auth_enabled():
    # When global auth IS enabled, the normal per-role Depends(require(...)) gate
    # already protects these routes regardless of allow_unauthenticated — a
    # token-carrying operator must not be blocked by the C2 refusal.
    from wavecam.auth import AuthConfig
    pipe = _agent_pipe_with_auth_flag(True, allow_unauthenticated=False)
    client = TestClient(build_app(pipe))
    client.app.state.auth = AuthConfig(enabled=True, tokens={"op": "operator"})
    resp = client.post(
        "/api/v1/agent/arm",
        json={"armed": True},
        headers={"Authorization": "Bearer op"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# --- L6: agent refusals use the standard api.refusal() shape (4xx + status block) ---

def test_agent_chat_disabled_uses_refusal_shape():
    client = TestClient(build_app(_agent_pipe(False)))
    resp = client.post("/api/v1/agent/chat", json={"message": "hi"})
    assert resp.status_code == 409
    body = resp.json()
    assert body["ok"] is False
    assert body["code"] == "agent_disabled"
    assert "status" in body  # standard refusal() shape includes a status snapshot


def test_agent_arm_disabled_uses_refusal_shape():
    client = TestClient(build_app(_agent_pipe(False)))
    resp = client.post("/api/v1/agent/arm", json={"armed": True})
    assert resp.status_code == 409
    body = resp.json()
    assert body["ok"] is False
    assert body["code"] == "agent_disabled"
    assert "status" in body


def test_agent_chat_error_uses_refusal_shape(monkeypatch):
    import wavecam.agent_session as ags
    monkeypatch.setattr(ags, "_load_token", lambda p: "tok")

    def boom(argv, env, stdin_text, timeout):
        raise RuntimeError("claude exited 1: boom")
    monkeypatch.setattr(ags, "_run_claude_cli", boom)

    pipe = _agent_pipe(True)
    client = TestClient(build_app(pipe))
    resp = client.post("/api/v1/agent/chat", json={"message": "hi"})
    assert resp.status_code == 502
    body = resp.json()
    assert body["ok"] is False
    assert body["code"] == "agent_error"
    assert "status" in body


# --- M19 (verify): /status agent block already includes `armed` ---

def test_status_agent_block_includes_armed(monkeypatch):
    _stub_claude(monkeypatch)
    client = TestClient(build_app(_agent_pipe(True)))
    agent = client.get("/api/v1/status").json()["agent"]
    assert "armed" in agent
    assert agent["armed"] is False
    client.post("/api/v1/agent/arm", json={"armed": True})
    assert client.get("/api/v1/status").json()["agent"]["armed"] is True
