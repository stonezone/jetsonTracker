"""Tests for the LLM advisor (supervise-only diagnostics, 3 providers).

The transport is injected, so every test runs offline: we assert on the
exact request each provider would send (URL, auth header shape, model id)
and on the state machine around the background consultation.

Auth policy under test (operator directive 2026-06-12): Anthropic and
OpenAI are OAuth-ONLY — no API key may appear in any request; DeepSeek
is API-key.
"""
from __future__ import annotations

import json
import threading
import time

from wavecam.advisor import (
    AdvisorService, PROVIDERS, SYSTEM_PROMPT, ProviderHTTPError,
    CODEX_BACKEND_URL, CODEX_TOKEN_URL, CODEX_CLIENT_ID,
)


def _write_keys(tmp_path, **overrides):
    keys = {
        "claude_oauth_token": "sk-ant-oat01-test",
        "deepseek_api_key": "sk-test-deepseek",
        "codex_access_token": "eyJ-access-old",
        "codex_refresh_token": "rt-old",
        "codex_account_id": "acct-123",
    }
    keys.update(overrides)
    p = tmp_path / "agent_keys.json"
    p.write_text(json.dumps(keys))
    return str(p)


def _context():
    return {"status": {"state": "TRACKING", "fps": 31.2},
            "events": [{"kind": "lock", "detail": "locked"}] * 50}


def _service(tmp_path, reply):
    calls = []

    def fake_post(url, headers, body, timeout=0):
        calls.append((url, headers, body))
        return reply(url) if callable(reply) else reply

    svc = AdvisorService(_context, keys_path=_write_keys(tmp_path),
                         post_fn=fake_post)
    return svc, calls


def _wait_done(svc, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if svc.report().get("status") in ("done", "error"):
            return svc.report()
        time.sleep(0.01)
    raise AssertionError(f"consultation never finished: {svc.report()}")


CLAUDE_REPLY = json.dumps(
    {"content": [{"type": "text", "text": "HEALTHY — all good."}]})
CHAT_REPLY = json.dumps(
    {"choices": [{"message": {"content": "HEALTHY — all good."}}]})
CODEX_SSE_REPLY = (
    'event: response.output_text.delta\n'
    'data: {"type": "response.output_text.delta", "delta": "HEALTHY — "}\n\n'
    'data: {"type": "response.output_text.delta", "delta": "all good."}\n\n'
    'data: [DONE]\n'
)
REFRESH_REPLY = json.dumps(
    {"access_token": "eyJ-access-new", "refresh_token": "rt-new"})


# ── request shaping ──────────────────────────────────────────────────────

def test_codex_request_shape(tmp_path):
    svc, calls = _service(tmp_path, CODEX_SSE_REPLY)
    assert svc.summon("codex")[0]
    report = _wait_done(svc)
    url, headers, body = calls[0]
    # OAuth ONLY: the ChatGPT-plan backend, never api.openai.com + API key.
    assert url == CODEX_BACKEND_URL
    assert headers["Authorization"] == "Bearer eyJ-access-old"
    assert headers["chatgpt-account-id"] == "acct-123"
    assert body["model"] == "gpt-5.5"   # -codex variants 400 on plan accounts
    assert body["stream"] is True
    assert body["instructions"] == SYSTEM_PROMPT
    assert report["text"] == "HEALTHY — all good."


def test_deepseek_request_shape(tmp_path):
    svc, calls = _service(tmp_path, CHAT_REPLY)
    assert svc.summon("deepseek")[0]
    _wait_done(svc)
    url, headers, body = calls[0]
    assert url == "https://api.deepseek.com/chat/completions"
    # deepseek-chat retires 2026-07-24; the successor id must be pinned.
    assert body["model"] == "deepseek-v4-flash"
    assert body["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}


# ── codex OAuth refresh flow ─────────────────────────────────────────────

def test_codex_refreshes_on_401_and_persists_rotated_tokens(tmp_path):
    keys_path = _write_keys(tmp_path)
    calls = []

    def fake_post(url, headers, body, timeout=0):
        calls.append((url, headers, body))
        if url == CODEX_BACKEND_URL and headers["Authorization"].endswith("old"):
            raise ProviderHTTPError(401, "token expired")
        if url == CODEX_TOKEN_URL:
            return REFRESH_REPLY
        return CODEX_SSE_REPLY

    svc = AdvisorService(_context, keys_path=keys_path, post_fn=fake_post)
    svc.summon("codex")
    report = _wait_done(svc)
    assert report["status"] == "done"

    # 1) expired call, 2) refresh, 3) retried with the new access token
    assert [c[0] for c in calls] == [CODEX_BACKEND_URL, CODEX_TOKEN_URL,
                                     CODEX_BACKEND_URL]
    refresh_body = calls[1][2]
    assert refresh_body["client_id"] == CODEX_CLIENT_ID
    assert refresh_body["grant_type"] == "refresh_token"
    assert refresh_body["refresh_token"] == "rt-old"
    assert calls[2][1]["Authorization"] == "Bearer eyJ-access-new"

    # rotated tokens persisted (the CLI contract)
    saved = json.loads(open(keys_path).read())
    assert saved["codex_access_token"] == "eyJ-access-new"
    assert saved["codex_refresh_token"] == "rt-new"


def test_codex_non_auth_error_not_retried(tmp_path):
    def fake_post(url, headers, body, timeout=0):
        raise ProviderHTTPError(500, "backend down")

    svc = AdvisorService(_context, keys_path=_write_keys(tmp_path),
                         post_fn=fake_post)
    svc.summon("codex")
    report = _wait_done(svc)
    assert report["status"] == "error"
    assert "500" in report["error"]


# ── claude_code provider (headless `claude -p` subprocess) ───────────────

def test_claude_code_runs_cli_with_token_env(tmp_path, monkeypatch):
    import wavecam.advisor as adv
    calls = []

    def fake_run(argv, env, timeout):
        calls.append((argv, env, timeout))
        return "wavecam agent online\n"

    monkeypatch.setattr(adv, "_run_claude_cli", fake_run)
    keys_path = _write_keys(tmp_path, claude_code_oauth_token="cco-secret")
    svc = AdvisorService(_context, keys_path=keys_path)
    svc.summon("claude_code")
    report = _wait_done(svc)
    assert report["status"] == "done"
    assert report["text"] == "wavecam agent online"  # stdout trimmed

    argv, env, _timeout = calls[0]
    assert argv[0].endswith("claude") and argv[1] == "-p"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "cco-secret"
    # the token must travel in the env, never on the command line (process list)
    assert all("cco-secret" not in a for a in argv)


def test_claude_code_cli_failure_is_reported(tmp_path, monkeypatch):
    import wavecam.advisor as adv

    def boom(argv, env, timeout):
        raise RuntimeError("claude CLI exited 1: Not logged in")

    monkeypatch.setattr(adv, "_run_claude_cli", boom)
    svc = AdvisorService(_context,
                         keys_path=_write_keys(tmp_path, claude_code_oauth_token="x"))
    svc.summon("claude_code")
    report = _wait_done(svc)
    assert report["status"] == "error"
    assert "Not logged in" in report["error"]


def test_claude_code_missing_token_is_friendly_error(tmp_path, monkeypatch):
    import wavecam.advisor as adv
    monkeypatch.setattr(adv, "_run_claude_cli", lambda *a, **k: "should not run")
    svc = AdvisorService(_context, keys_path=_write_keys(tmp_path))  # no claude_code token
    svc.summon("claude_code")
    report = _wait_done(svc)
    assert report["status"] == "error"
    assert "claude_code_oauth_token" in report["error"]


# ── supervise-only invariants ────────────────────────────────────────────

def test_no_tools_in_any_request(tmp_path):
    """The advisor must never offer the model tools — supervise-only is
    structural, not just prompted."""
    for provider, reply in [("codex", CODEX_SSE_REPLY),
                            ("deepseek", CHAT_REPLY)]:
        svc, calls = _service(tmp_path, reply)
        svc.summon(provider)
        _wait_done(svc)
        for _, _, body in calls:
            assert "tools" not in body, provider


def test_events_truncated_in_prompt(tmp_path):
    svc, calls = _service(tmp_path, CHAT_REPLY)
    svc.summon("deepseek")
    _wait_done(svc)
    prompt = calls[0][2]["messages"][0]["content"]
    assert prompt.count('"kind": "lock"') <= 30


# ── state machine ────────────────────────────────────────────────────────

def test_unknown_provider_refused(tmp_path):
    svc, _ = _service(tmp_path, CHAT_REPLY)
    ok, msg = svc.summon("skynet")
    assert not ok and "skynet" in msg


def test_second_summon_refused_while_running(tmp_path):
    gate = threading.Event()

    def slow_post(url, headers, body, timeout=0):
        gate.wait(2.0)
        return CHAT_REPLY

    svc = AdvisorService(_context, keys_path=_write_keys(tmp_path),
                         post_fn=slow_post)
    assert svc.summon("deepseek")[0]
    ok, msg = svc.summon("deepseek")
    assert not ok and "already running" in msg
    gate.set()
    _wait_done(svc)
    # done -> a new summon is accepted again
    assert svc.summon("deepseek")[0]


def test_provider_error_reported_not_raised(tmp_path):
    def bad_post(url, headers, body, timeout=0):
        raise ProviderHTTPError(429, "rate limited")

    svc = AdvisorService(_context, keys_path=_write_keys(tmp_path),
                         post_fn=bad_post)
    svc.summon("deepseek")
    report = _wait_done(svc)
    assert report["status"] == "error"
    assert "429" in report["error"]


def test_missing_keys_file_is_friendly_error(tmp_path):
    svc = AdvisorService(_context, keys_path=str(tmp_path / "nope.json"),
                         post_fn=lambda *a, **k: CHAT_REPLY)
    svc.summon("deepseek")
    report = _wait_done(svc)
    assert report["status"] == "error"
    assert "keys file missing" in report["error"]


def test_summon_returns_immediately(tmp_path):
    """The request thread must never wait on the provider (2026-06-08 rule)."""
    def slow_post(url, headers, body, timeout=0):
        time.sleep(0.5)
        return CHAT_REPLY

    svc = AdvisorService(_context, keys_path=_write_keys(tmp_path),
                         post_fn=slow_post)
    t0 = time.time()
    svc.summon("deepseek")
    assert time.time() - t0 < 0.1
    assert svc.report()["status"] == "running"
    _wait_done(svc)


def test_provider_registry_complete():
    assert set(PROVIDERS) == {"claude_code", "codex", "deepseek"}
