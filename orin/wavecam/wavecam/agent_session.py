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
REQUEST_TIMEOUT_SEC = 120.0

# Injected as --append-system-prompt every turn. Establishes the rig context and
# the hard safety rules. The HARD RULES restate the supervise-only / KILL-human-only
# invariants in-band so an armed (tool-using) turn can't be talked into violating them.
AGENT_SYSTEM_PROMPT = (
    "You are the WaveCam onboard assistant running on the Jetson Orin rig that auto-films a "
    "foil-surfer. The control API is at http://localhost:8088/api/v1 (status, config/hot, ptz, "
    "calibration, system/restart, logs, gps). When ARMED you may act by calling it with curl via "
    "the Bash tool — always check the JSON for ok:true, because refusals are HTTP 200 with ok:false. "
    "HARD RULES you must never break: (1) the operator's KILL / Emergency Stop is human-only — never "
    "POST /safety/kill or /safety/resume, and never try to disable, bypass, or re-arm around them. "
    "(2) The camera is supervise-only: move it (ptz/* or calibration/*) only when the operator "
    "explicitly asks this turn, and remember it always yields to a manual aim and to KILL. "
    "(3) Prefer reversible config tuning; describe any risky or hard-to-undo step before doing it. "
    "When NOT armed you have no shell and can only inspect and advise. Keep replies concise and concrete."
)


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
    run: Optional[Callable[..., str]] = None   # defaults to module _run_claude_cli, resolved at call time
    session_id: Optional[str] = None

    def chat(self, message: str, status_text: str, armed: bool = False) -> dict:
        token = _load_token(self.keys_path)
        env = {**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": token}
        argv = [self.cli_path, "--output-format", "json",
                "--append-system-prompt", AGENT_SYSTEM_PROMPT]
        if self.session_id:
            argv += ["--resume", self.session_id]
        # Arm-state gates the toolset. ARMED → Claude can act (Bash/Edit + auto-approve);
        # DISARMED → read-only advice (no shell). Variadic tool flags MUST precede -p,
        # which terminates them; the operator prompt arrives on stdin.
        if armed:
            argv += ["--permission-mode", "bypassPermissions",
                     "--allowedTools", "Bash", "Read", "Edit", "Write"]
        else:
            argv += ["--disallowedTools", "Bash", "Edit", "Write"]
        argv += ["-p"]
        prompt = f"Live system status:\n{status_text}\n\nOperator: {message}"
        runner = self.run if self.run is not None else _run_claude_cli
        out = runner(argv, env, prompt, REQUEST_TIMEOUT_SEC)
        data = json.loads(out)
        self.session_id = data.get("session_id") or self.session_id
        return {"reply": data.get("result", ""), "session_id": self.session_id or ""}
