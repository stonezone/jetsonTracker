"""Phase 1a: ArmState safety machine + AgentSession claude -p driver."""
from __future__ import annotations

import json

import pytest

from wavecam.agent_session import AgentSession, ArmState


def test_default_disarmed():
    s = ArmState(ttl_sec=600.0, now=lambda: 0.0)
    assert s.armed is False and s.killed is False and s.can_act() is False


def test_arm_then_ttl_expiry():
    t = {"v": 0.0}
    s = ArmState(ttl_sec=600.0, now=lambda: t["v"])
    s.arm()
    assert s.can_act() is True
    t["v"] = 599.0
    assert s.armed is True
    t["v"] = 601.0
    assert s.armed is False and s.can_act() is False  # auto-expired


def test_kill_disarms_and_blocks_rearm():
    s = ArmState(ttl_sec=600.0, now=lambda: 0.0)
    s.arm()
    s.kill()
    assert s.killed is True and s.can_act() is False
    s.arm()                       # re-arm attempt while killed
    assert s.can_act() is False   # refused until clear_kill()
    s.clear_kill()
    s.arm()
    assert s.can_act() is True


def test_snapshot_shape():
    s = ArmState(ttl_sec=300.0, now=lambda: 0.0)
    assert s.snapshot() == {"armed": False, "killed": False, "ttl_sec": 300.0}


def test_chat_threads_session_id_and_uses_stdin(tmp_path):
    keys = tmp_path / "k.json"
    keys.write_text(json.dumps({"claude_code_oauth_token": "x"}))
    calls = []

    def fake_run(argv, env, stdin_text, timeout):
        calls.append({"argv": argv, "stdin": stdin_text,
                      "env_token": env.get("CLAUDE_CODE_OAUTH_TOKEN")})
        return json.dumps({"result": "hi there", "session_id": "SID-1"})

    sess = AgentSession(keys_path=str(keys), run=fake_run)
    r1 = sess.chat("hello", status_text="FPS=27")
    assert r1 == {"reply": "hi there", "session_id": "SID-1"}
    assert "--resume" not in calls[0]["argv"]              # first turn: no resume
    assert calls[0]["env_token"] == "x"                    # token injected via env, not argv
    assert "x" not in " ".join(calls[0]["argv"])           # token never on the command line
    assert "FPS=27" in calls[0]["stdin"] and "hello" in calls[0]["stdin"]  # prompt via stdin

    r2 = sess.chat("again", status_text="FPS=30")
    argv2 = calls[1]["argv"]
    i = argv2.index("--resume")
    assert argv2[i:i + 2] == ["--resume", "SID-1"]         # second turn resumes the session
    assert r2["session_id"] == "SID-1"


def test_chat_missing_token_raises(tmp_path):
    keys = tmp_path / "k.json"
    keys.write_text(json.dumps({"other": "v"}))
    sess = AgentSession(keys_path=str(keys), run=lambda *a, **k: "{}")
    with pytest.raises(RuntimeError, match="claude_code_oauth_token"):
        sess.chat("hi", status_text="")
