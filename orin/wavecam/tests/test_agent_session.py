"""Phase 1a: ArmState safety machine + AgentSession claude -p driver."""
from __future__ import annotations

import json
import os

import pytest

from wavecam.agent_session import PROVIDER_ENDPOINTS, AgentSession, ArmState


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
    keys.write_text(json.dumps({"claude_code_oauth_token": "SEKRET_ZZZ"}))
    calls = []

    def fake_run(argv, env, stdin_text, timeout):
        calls.append({"argv": argv, "stdin": stdin_text,
                      "env_token": env.get("CLAUDE_CODE_OAUTH_TOKEN")})
        return json.dumps({"result": "hi there", "session_id": "SID-1"})

    sess = AgentSession(keys_path=str(keys), run=fake_run)
    r1 = sess.chat("hello", status_text="FPS=27")
    assert r1 == {"reply": "hi there", "session_id": "SID-1"}
    assert "--resume" not in calls[0]["argv"]              # first turn: no resume
    assert calls[0]["env_token"] == "SEKRET_ZZZ"           # token injected via env, not argv
    assert "SEKRET_ZZZ" not in " ".join(calls[0]["argv"])  # token never on the command line
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


def _argv_for(tmp_path, armed):
    keys = tmp_path / "k.json"
    keys.write_text(json.dumps({"claude_code_oauth_token": "t"}))
    captured = []

    def fake_run(argv, env, stdin_text, timeout):
        captured.append(argv)
        return json.dumps({"result": "ok", "session_id": "S"})

    AgentSession(keys_path=str(keys), run=fake_run).chat("do it", status_text="", armed=armed)
    return captured[0]


def test_chat_armed_enables_shell_and_autoapprove(tmp_path):
    argv = _argv_for(tmp_path, armed=True)
    assert "bypassPermissions" in argv
    assert "Bash" in argv
    # the variadic --allowedTools must be terminated by -p so the prompt isn't eaten
    assert argv.index("--allowedTools") < argv.index("-p")
    assert "--disallowedTools" not in argv


def test_chat_disarmed_blocks_shell(tmp_path):
    argv = _argv_for(tmp_path, armed=False)
    assert "--disallowedTools" in argv and "Bash" in argv
    assert "bypassPermissions" not in argv


def test_run_claude_cli_redacts_token_on_error():
    # H1 (audit 2026-07-01): _run_claude_cli is now Popen-based (its own process
    # group), so this drives a REAL (tiny, no CLI dependency) subprocess rather
    # than mocking subprocess.run — exercises the actual code path.
    import wavecam.agent_session as ags
    argv = ["python3", "-c",
            "import sys; sys.stderr.write('traceback leaked SEKRET_TOKEN_VALUE in env'); "
            "sys.exit(1)"]
    with pytest.raises(RuntimeError) as ei:
        ags._run_claude_cli(argv, {"CLAUDE_CODE_OAUTH_TOKEN": "SEKRET_TOKEN_VALUE"}, "p", 5.0)
    assert "SEKRET_TOKEN_VALUE" not in str(ei.value)   # token never surfaced
    assert "<redacted>" in str(ei.value)


def test_run_claude_cli_timeout_is_clean():
    import wavecam.agent_session as ags
    argv = ["python3", "-c", "import time; time.sleep(5)"]
    with pytest.raises(RuntimeError, match="timed out"):
        ags._run_claude_cli(argv, dict(os.environ), "p", 0.2)


def test_chat_non_json_output_clears_session(tmp_path):
    keys = tmp_path / "k.json"
    keys.write_text(json.dumps({"claude_code_oauth_token": "t"}))
    sess = AgentSession(keys_path=str(keys), run=lambda *a: "this is not json")
    sess._session_ids["claude_code"] = "OLD-SID"
    with pytest.raises(RuntimeError, match="non-JSON"):
        sess.chat("hi", status_text="")
    assert sess._session_ids.get("claude_code") is None   # corrupted turn → no stale resume


def _capture_run():
    cap = {}
    def fake_run(argv, env, stdin_text, timeout):
        cap["argv"] = argv
        cap["env"] = env
        return json.dumps({"result": "ok", "session_id": "S1"})
    return cap, fake_run


def test_chat_vendor_provider_injects_env(tmp_path):
    keys = tmp_path / "k.json"
    keys.write_text(json.dumps({"deepseek_api_key": "DS_KEY"}))
    cap, fake_run = _capture_run()
    sess = AgentSession(keys_path=str(keys), run=fake_run)
    sess.chat("hi", status_text="", provider="deepseek")
    assert cap["env"]["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert cap["env"]["ANTHROPIC_AUTH_TOKEN"] == "DS_KEY"
    assert cap["env"].get("ANTHROPIC_MODEL", "").startswith("deepseek")
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in cap["env"]   # vendor path doesn't use the OAuth token


def test_chat_claude_code_uses_oauth_token(tmp_path):
    keys = tmp_path / "k.json"
    keys.write_text(json.dumps({"claude_code_oauth_token": "OAUTH"}))
    cap, fake_run = _capture_run()
    sess = AgentSession(keys_path=str(keys), run=fake_run)
    sess.chat("hi", status_text="", provider="claude_code")
    assert cap["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "OAUTH"
    # The default path must not point the CLI at a vendor endpoint. (Don't assert
    # absence — the host env may carry an ANTHROPIC_BASE_URL; assert it's not a vendor's.)
    vendor_urls = {base for base, _, _ in PROVIDER_ENDPOINTS.values()}
    assert cap["env"].get("ANTHROPIC_BASE_URL") not in vendor_urls


def test_chat_unconfigured_provider_errors(tmp_path):
    keys = tmp_path / "k.json"
    keys.write_text("{}")
    sess = AgentSession(keys_path=str(keys), run=lambda *a: "{}")
    with pytest.raises(RuntimeError, match="provider_unconfigured|api_key"):
        sess.chat("hi", status_text="", provider="glm")


def test_session_id_keyed_per_provider(tmp_path):
    keys = tmp_path / "k.json"
    keys.write_text(json.dumps({"claude_code_oauth_token": "t", "deepseek_api_key": "k"}))
    calls = []
    def fake_run(argv, env, stdin_text, timeout):
        calls.append(argv)
        return json.dumps({"result": "ok", "session_id": f"S-{len(calls)}"})
    sess = AgentSession(keys_path=str(keys), run=fake_run)
    sess.chat("a", status_text="", provider="claude_code")   # S-1 for claude_code
    sess.chat("b", status_text="", provider="deepseek")      # S-2 for deepseek, NO resume of S-1
    assert "--resume" not in calls[1]                        # different provider → fresh session
    sess.chat("c", status_text="", provider="claude_code")   # resumes claude_code's S-1
    i = calls[2].index("--resume")
    assert calls[2][i + 1] == "S-1"
