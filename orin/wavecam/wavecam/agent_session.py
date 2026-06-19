"""Interactive acting-agent: the arm-state safety machine + the claude -p driver.

Phase 1a builds the conversation + safety bridge only — no acting tools yet.
ArmState is the supervise-only gate: DISARMED by default, an ARMED session
auto-expires after a TTL, and KILL is supreme (disarms and forbids re-arm until
explicitly cleared). Acting tiers (Phase 1b+) will read ``can_act()`` before any
mutating tool runs.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Optional

CLAUDE_CLI_PATH = "/home/zack/.local/bin/claude"
REQUEST_TIMEOUT_SEC = 90.0


class ArmState:
    """Operator arm gate.

    DISARMED by default; ARMED auto-expires ``ttl_sec`` after the last ``arm()``;
    KILL disarms immediately and blocks re-arm until ``clear_kill()``. ``now`` is
    injected (monotonic by default) so the TTL logic is deterministic under test.
    """

    def __init__(self, ttl_sec: float, now: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_sec
        self._now = now
        self._armed_at: Optional[float] = None
        self._killed = False

    def arm(self) -> None:
        if self._killed:
            return
        self._armed_at = self._now()

    def disarm(self) -> None:
        self._armed_at = None

    def kill(self) -> None:
        self._killed = True
        self._armed_at = None

    def clear_kill(self) -> None:
        self._killed = False

    @property
    def killed(self) -> bool:
        return self._killed

    @property
    def armed(self) -> bool:
        if self._killed or self._armed_at is None:
            return False
        return (self._now() - self._armed_at) < self._ttl

    def can_act(self) -> bool:
        return self.armed and not self._killed

    def snapshot(self) -> dict:
        return {"armed": self.armed, "killed": self._killed, "ttl_sec": self._ttl}


def _run_claude_cli(argv: list[str], env: dict, stdin_text: str, timeout: float) -> str:
    """Run the claude CLI with the prompt on stdin. Module-level so tests inject a fake."""
    proc = subprocess.run(argv, env=env, input=stdin_text,
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: "
                           f"{(proc.stderr or proc.stdout or '').strip()[:200]}")
    return proc.stdout


def _load_token(keys_path: str) -> str:
    with open(keys_path) as fh:
        token = json.load(fh).get("claude_code_oauth_token")
    if not token:
        raise RuntimeError("claude_code_oauth_token missing from agent_keys.json")
    return str(token)


@dataclass
class AgentSession:
    """Drives a multi-turn `claude -p` conversation, threading the session_id so
    each turn resumes the last. The prompt is fed via stdin (never argv) so the
    variadic tool flags can't consume it, and the OAuth token is injected via the
    child env (never the command line, never logged)."""

    keys_path: str
    cli_path: str = CLAUDE_CLI_PATH
    run: Callable[..., str] = _run_claude_cli
    session_id: Optional[str] = None

    def chat(self, message: str, status_text: str) -> dict:
        token = _load_token(self.keys_path)
        env = {**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": token}
        argv = [self.cli_path, "--output-format", "json"]
        if self.session_id:
            argv += ["--resume", self.session_id]
        argv += ["-p"]   # -p terminates any variadic flag; the prompt arrives on stdin
        prompt = (f"You are the WaveCam onboard assistant. Live system status:\n"
                  f"{status_text}\n\nOperator: {message}")
        out = self.run(argv, env, prompt, REQUEST_TIMEOUT_SEC)
        data = json.loads(out)
        self.session_id = data.get("session_id") or self.session_id
        return {"reply": data.get("result", ""), "session_id": self.session_id or ""}
