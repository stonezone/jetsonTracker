"""Audit round-2 — Agent C's agent_session.py fixes.

R11 — a TimeoutExpired reap used an unbounded proc.communicate() under
_session_lock; if a grandchild escaped the process group and kept holding a
stdout/stderr pipe open, every future agent chat turn (which shares the same
per-provider lock) could block forever. The reap is now bounded, with a
second killpg + a bounded wait() fallback before giving up.

R14 — the session lock used to be ONE lock shared by every provider (despite
a comment claiming "per-provider"), so a stuck claude_code turn blocked a
concurrent deepseek turn. Locking is now genuinely per-provider.

N2 — AGENT_FORBIDDEN_PATHS was dead code (referenced nowhere); dropped rather
than wired up, since there is no request-path-level tool in this codebase for
it to gate (the armed agent acts via a raw Bash tool, not a dedicated
HTTP-calling tool).
"""
from __future__ import annotations

import json
import subprocess
import threading
import time

import wavecam.agent_session as agent_session
from wavecam.agent_session import AgentSession, _run_claude_cli


def _keys(tmp_path, **extra):
    p = tmp_path / "k.json"
    payload = {"claude_code_oauth_token": "t"}
    payload.update(extra)
    p.write_text(json.dumps(payload))
    return str(p)


# ---------------------------------------------------------------------------
# R11 — bounded reap after a TimeoutExpired, with a second-kill fallback
# ---------------------------------------------------------------------------

class _FakeStuckProc:
    """Simulates a child whose first communicate() times out (the normal
    turn-timeout path) and whose POST-KILL reap ALSO times out (a grandchild
    escaped the process group and is still holding a pipe open) — the
    scenario R11 guards against. No real sleeping: every step raises/returns
    immediately so the test runs fast."""

    def __init__(self):
        self.pid = 999_999_999  # never a real pid
        self.communicate_calls = 0
        self.wait_calls = 0

    def communicate(self, input=None, timeout=None):
        self.communicate_calls += 1
        if self.communicate_calls <= 2:
            raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout)
        return ("", "")  # unreached in this test, but keeps the fake honest

    def wait(self, timeout=None):
        self.wait_calls += 1
        return 0

    def poll(self):
        return None

    def kill(self):
        pass


def test_timeout_reap_is_bounded_and_falls_back_to_second_kill(monkeypatch):
    """R11: a TimeoutExpired whose post-kill reap ALSO hangs must not block
    forever — _kill_process_group is called a second time and a bounded
    wait() is used as the last-resort reap, then the original timeout error
    still propagates."""
    fake_proc = _FakeStuckProc()
    monkeypatch.setattr(agent_session.subprocess, "Popen", lambda *a, **k: fake_proc)

    kill_calls = []
    monkeypatch.setattr(agent_session, "_kill_process_group", lambda proc: kill_calls.append(proc))

    start = time.monotonic()
    try:
        _run_claude_cli(["fake-cli"], {}, "prompt", timeout=1.0)
        assert False, "expected a RuntimeError (claude CLI timed out)"
    except RuntimeError as exc:
        assert "timed out after 1s" in str(exc)
    elapsed = time.monotonic() - start

    # Nothing here should ever really sleep — the fake raises/returns instantly.
    assert elapsed < 2.0, f"reap path blocked for {elapsed:.2f}s — not actually bounded"
    assert len(kill_calls) == 2, "expected a SECOND killpg after the bounded reap also timed out"
    assert fake_proc.communicate_calls == 2, "expected exactly one bounded reap attempt"
    assert fake_proc.wait_calls == 1, "expected the last-resort bounded wait() fallback"


def test_timeout_reap_succeeds_on_first_bounded_attempt(monkeypatch):
    """The common case: the post-kill reap succeeds within the bounded
    timeout on the first try — no second kill, no wait() fallback needed."""

    class _FakeProc:
        def __init__(self):
            self.pid = 999_999_998
            self.communicate_calls = 0

        def communicate(self, input=None, timeout=None):
            self.communicate_calls += 1
            if self.communicate_calls == 1:
                raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout)
            return ("", "")

        def poll(self):
            return None

        def kill(self):
            pass

    fake_proc = _FakeProc()
    monkeypatch.setattr(agent_session.subprocess, "Popen", lambda *a, **k: fake_proc)
    kill_calls = []
    monkeypatch.setattr(agent_session, "_kill_process_group", lambda proc: kill_calls.append(proc))

    try:
        _run_claude_cli(["fake-cli"], {}, "prompt", timeout=1.0)
        assert False, "expected a RuntimeError (claude CLI timed out)"
    except RuntimeError:
        pass

    assert len(kill_calls) == 1, "the reap succeeded first try — no second kill needed"
    assert fake_proc.communicate_calls == 2


# ---------------------------------------------------------------------------
# R14 — per-provider lock (a stuck turn on one provider doesn't block another)
# ---------------------------------------------------------------------------

def test_concurrent_chats_on_different_providers_do_not_block_each_other(tmp_path):
    keys_path = _keys(tmp_path, deepseek_api_key="DK")
    entered_claude = threading.Event()
    release_claude = threading.Event()

    def fake_run(argv, env, stdin_text, timeout):
        # claude_code's env has no ANTHROPIC_MODEL override; deepseek's does.
        if env.get("ANTHROPIC_MODEL") != "deepseek-v4-flash":
            entered_claude.set()
            assert release_claude.wait(timeout=2.0), "test setup: never released"
        return json.dumps({"result": "ok", "session_id": "S"})

    sess = AgentSession(keys_path=keys_path, run=fake_run)

    def claude_turn():
        sess.chat("hi", status_text="", provider="claude_code")

    t = threading.Thread(target=claude_turn)
    t.start()
    assert entered_claude.wait(timeout=2.0), "claude_code turn never started"

    # R14: a concurrent turn on a DIFFERENT provider must not queue up behind
    # the in-flight claude_code turn — it should complete promptly even
    # though that turn is still parked waiting on release_claude.
    start = time.monotonic()
    result = sess.chat("hi", status_text="", provider="deepseek")
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, f"deepseek turn blocked {elapsed:.2f}s behind the claude_code turn"
    assert result["reply"] == "ok"

    release_claude.set()
    t.join(timeout=2.0)
    assert not t.is_alive()


def test_provider_lock_is_reused_across_calls_same_provider(tmp_path):
    """The per-provider lock objects must be stable across calls (not
    recreated per-turn, which would defeat serialization for that provider)."""
    keys_path = _keys(tmp_path)
    sess = AgentSession(
        keys_path=keys_path,
        run=lambda *a, **k: json.dumps({"result": "ok", "session_id": "S"}),
    )
    lock_a = sess._provider_lock("claude_code")
    lock_b = sess._provider_lock("claude_code")
    assert lock_a is lock_b


def test_provider_lock_differs_across_providers(tmp_path):
    keys_path = _keys(tmp_path)
    sess = AgentSession(
        keys_path=keys_path,
        run=lambda *a, **k: json.dumps({"result": "ok", "session_id": "S"}),
    )
    assert sess._provider_lock("claude_code") is not sess._provider_lock("deepseek")


# ---------------------------------------------------------------------------
# N2 — AGENT_FORBIDDEN_PATHS dropped as dead code
# ---------------------------------------------------------------------------

def test_agent_forbidden_paths_constant_removed():
    assert not hasattr(agent_session, "AGENT_FORBIDDEN_PATHS"), (
        "N2: AGENT_FORBIDDEN_PATHS was unreferenced dead code and was removed; "
        "if this fails, either the constant was reintroduced (fine, update this "
        "pin) or something started depending on it again (wire it up properly)."
    )


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
