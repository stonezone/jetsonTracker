"""Tests for the LLM advisor (supervise-only diagnostics, 3 providers).

The transport is injected, so every test runs offline: we assert on the
exact request each provider would send (URL, auth header shape, model id)
and on the state machine around the background consultation.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from wavecam.advisor import AdvisorService, PROVIDERS, SYSTEM_PROMPT


def _write_keys(tmp_path, **overrides):
    keys = {
        "claude_oauth_token": "sk-ant-oat01-test",
        "openai_api_key": "sk-test-openai",
        "deepseek_api_key": "sk-test-deepseek",
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


CLAUDE_REPLY = {"content": [{"type": "text", "text": "HEALTHY — all good."}]}
CHAT_REPLY = {"choices": [{"message": {"content": "HEALTHY — all good."}}]}
RESPONSES_REPLY = {"output": [{"content": [
    {"type": "output_text", "text": "HEALTHY — all good."}]}]}


# ── request shaping ──────────────────────────────────────────────────────

def test_claude_request_shape(tmp_path):
    svc, calls = _service(tmp_path, CLAUDE_REPLY)
    assert svc.summon("claude")[0]
    report = _wait_done(svc)
    url, headers, body = calls[0]
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["Authorization"].startswith("Bearer sk-ant-oat01")
    # OAuth bearers are rejected without this beta header.
    assert headers["anthropic-beta"] == "oauth-2025-04-20"
    assert body["model"] == "claude-opus-4-8"
    assert body["system"] == SYSTEM_PROMPT
    assert report["text"] == "HEALTHY — all good."


def test_codex_request_shape(tmp_path):
    svc, calls = _service(tmp_path, RESPONSES_REPLY)
    assert svc.summon("codex")[0]
    _wait_done(svc)
    url, headers, body = calls[0]
    assert url == "https://api.openai.com/v1/responses"
    assert body["model"] == "gpt-5.5"
    assert body["instructions"] == SYSTEM_PROMPT


def test_deepseek_request_shape(tmp_path):
    svc, calls = _service(tmp_path, CHAT_REPLY)
    assert svc.summon("deepseek")[0]
    _wait_done(svc)
    url, headers, body = calls[0]
    assert url == "https://api.deepseek.com/chat/completions"
    # deepseek-chat retires 2026-07-24; the successor id must be pinned.
    assert body["model"] == "deepseek-v4-flash"
    assert body["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}


# ── supervise-only invariants ────────────────────────────────────────────

def test_no_tools_in_any_request(tmp_path):
    """The advisor must never offer the model tools — supervise-only is
    structural, not just prompted."""
    for provider, reply in [("claude", CLAUDE_REPLY), ("codex", RESPONSES_REPLY),
                            ("deepseek", CHAT_REPLY)]:
        svc, calls = _service(tmp_path, reply)
        svc.summon(provider)
        _wait_done(svc)
        _, _, body = calls[0]
        assert "tools" not in body, provider


def test_events_truncated_in_prompt(tmp_path):
    svc, calls = _service(tmp_path, CLAUDE_REPLY)
    svc.summon("claude")
    _wait_done(svc)
    prompt = calls[0][2]["messages"][0]["content"]
    assert prompt.count('"kind": "lock"') <= 30


# ── state machine ────────────────────────────────────────────────────────

def test_unknown_provider_refused(tmp_path):
    svc, _ = _service(tmp_path, CLAUDE_REPLY)
    ok, msg = svc.summon("skynet")
    assert not ok and "skynet" in msg


def test_second_summon_refused_while_running(tmp_path):
    gate = threading.Event()

    def slow_post(url, headers, body, timeout=0):
        gate.wait(2.0)
        return CLAUDE_REPLY

    svc = AdvisorService(_context, keys_path=_write_keys(tmp_path),
                         post_fn=slow_post)
    assert svc.summon("claude")[0]
    ok, msg = svc.summon("deepseek")
    assert not ok and "already running" in msg
    gate.set()
    _wait_done(svc)
    # done -> a new summon is accepted again
    assert svc.summon("deepseek")[0]


def test_provider_error_reported_not_raised(tmp_path):
    def bad_post(url, headers, body, timeout=0):
        raise RuntimeError("HTTP 429 from provider: rate limited")

    svc = AdvisorService(_context, keys_path=_write_keys(tmp_path),
                         post_fn=bad_post)
    svc.summon("claude")
    report = _wait_done(svc)
    assert report["status"] == "error"
    assert "429" in report["error"]


def test_missing_keys_file_is_friendly_error(tmp_path):
    svc = AdvisorService(_context, keys_path=str(tmp_path / "nope.json"),
                         post_fn=lambda *a, **k: CLAUDE_REPLY)
    svc.summon("claude")
    report = _wait_done(svc)
    assert report["status"] == "error"
    assert "keys file missing" in report["error"]


def test_summon_returns_immediately(tmp_path):
    """The request thread must never wait on the provider (2026-06-08 rule)."""
    def slow_post(url, headers, body, timeout=0):
        time.sleep(0.5)
        return CLAUDE_REPLY

    svc = AdvisorService(_context, keys_path=_write_keys(tmp_path),
                         post_fn=slow_post)
    t0 = time.time()
    svc.summon("claude")
    assert time.time() - t0 < 0.1
    assert svc.report()["status"] == "running"
    _wait_done(svc)


def test_provider_registry_complete():
    assert set(PROVIDERS) == {"claude", "codex", "deepseek"}
