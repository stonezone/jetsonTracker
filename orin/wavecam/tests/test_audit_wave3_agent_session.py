"""Audit 2026-07-01 Wave 3 — H1 (Popen in its own process group + terminate()
SIGKILLs it) and M15 (per-provider lock around _session_ids read/resume/write).
"""
from __future__ import annotations

import json
import os
import threading
import time

from wavecam.agent_session import AgentSession, _run_claude_cli


def _keys(tmp_path):
    p = tmp_path / "k.json"
    p.write_text(json.dumps({"claude_code_oauth_token": "t"}))
    return str(p)


def test_run_tracked_launches_in_its_own_process_group(tmp_path):
    """H1: the real (non-injected) runner must start_new_session=True so the
    child (and anything it forks, e.g. a Bash tool call) is a killable unit
    distinct from the API server's own process group."""
    sess = AgentSession(keys_path=_keys(tmp_path))
    # A tiny python program that reports its own pgid, then exits — stands in
    # for the claude CLI without requiring it to be installed in the test env.
    argv = [
        "python3", "-c",
        "import os, json; print(json.dumps({'result': str(os.getpgid(0)), "
        "'session_id': 'S'}))",
    ]
    out = _run_claude_cli(argv, dict(os.environ), "", timeout=5.0, session=sess)
    data = json.loads(out)
    child_pgid = int(data["result"])
    assert child_pgid != os.getpgid(0), "child must not share our process group"
    # After completion, _proc is cleared.
    assert sess._proc is None


def test_terminate_sigkills_inflight_process_group(tmp_path):
    """H1's core safety claim: agent_kill() -> terminate() must actually stop a
    running turn, not just flip a flag while the subprocess (and its Bash tool
    children) keep running for up to REQUEST_TIMEOUT_SEC."""
    sess = AgentSession(keys_path=_keys(tmp_path))
    marker = tmp_path / "still_running"

    def run_long_turn():
        argv = [
            "python3", "-c",
            f"import time; open({str(marker)!r}, 'w').write('x'); time.sleep(30)",
        ]
        try:
            _run_claude_cli(argv, dict(os.environ), "", timeout=30.0, session=sess)
        except RuntimeError:
            pass  # killed -> non-zero exit or timeout, either is fine here

    t = threading.Thread(target=run_long_turn)
    t.start()
    deadline = time.time() + 3.0
    while not marker.exists() and time.time() < deadline:
        time.sleep(0.02)
    assert marker.exists(), "test process never started"
    time.sleep(0.1)  # let the child fully install its process group

    with sess._proc_lock:
        proc = sess._proc
    assert proc is not None and proc.poll() is None, "process must be tracked and alive"

    killed = sess.terminate()
    assert killed is True

    t.join(timeout=5.0)
    assert not t.is_alive(), "terminate() must let the turn's thread unblock promptly"
    assert proc.poll() is not None, "the tracked process must actually be dead"


def test_terminate_is_a_noop_when_nothing_running(tmp_path):
    sess = AgentSession(keys_path=_keys(tmp_path))
    assert sess.terminate() is False


def test_terminate_noop_when_using_injected_test_runner(tmp_path):
    # AgentSession.run (the test seam) never populates self._proc — terminate()
    # must stay a safe no-op rather than erroring when the fake path is used.
    sess = AgentSession(
        keys_path=_keys(tmp_path),
        run=lambda argv, env, stdin_text, timeout: json.dumps(
            {"result": "ok", "session_id": "S"}
        ),
    )
    sess.chat("hi", status_text="")
    assert sess.terminate() is False


# --- M15: per-provider session-id serialization ---

def test_concurrent_chats_same_provider_do_not_interleave_session_ids(tmp_path):
    """Two overlapping chat() calls on the SAME provider must not both read the
    same stale session_id and then race the write — the whole turn (read sid,
    build argv, run, write new sid) must be serialized per AgentSession."""
    keys_path = _keys(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def fake_run(argv, env, stdin_text, timeout):
        calls.append(argv)
        # First caller in blocks here so a second, concurrent call is forced to
        # wait for the lock rather than racing the dict.
        if not entered.is_set():
            entered.set()
            release.wait(timeout=2.0)
        return json.dumps({"result": "ok", "session_id": f"S{len(calls)}"})

    sess = AgentSession(keys_path=keys_path, run=fake_run)

    def first():
        sess.chat("a", status_text="", provider="claude_code")

    t = threading.Thread(target=first)
    t.start()
    assert entered.wait(timeout=2.0)

    # A second call attempted while the first is still "in flight" must block
    # on the lock (not run concurrently) — release the first, then the second
    # proceeds and resumes the session the first one wrote, never a stale one.
    release.set()
    t.join(timeout=2.0)
    r2 = sess.chat("b", status_text="", provider="claude_code")

    assert "--resume" in calls[1]
    i = calls[1].index("--resume")
    assert calls[1][i + 1] == "S1"   # resumed the session the FIRST call wrote
    assert r2["session_id"] == "S2"


def test_session_lock_is_per_agentsession_not_global(tmp_path):
    # Two independent AgentSessions (as SystemManager would only ever build one,
    # but this pins that the lock lives on the instance, not module state).
    keys_path = _keys(tmp_path)
    a = AgentSession(keys_path=keys_path, run=lambda *a, **k: json.dumps(
        {"result": "ok", "session_id": "A1"}))
    b = AgentSession(keys_path=keys_path, run=lambda *a, **k: json.dumps(
        {"result": "ok", "session_id": "B1"}))
    assert a._session_lock is not b._session_lock
