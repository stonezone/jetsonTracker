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
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

CLAUDE_CLI_PATH = "/home/zack/.local/bin/claude"
REQUEST_TIMEOUT_SEC = 120.0

# Vendor providers run the SAME claude CLI but pointed at an Anthropic-compatible
# endpoint with a static API key (mirrors the operator's deepclaude/glmcode/kimicode
# shell aliases). claude_code is the default and uses the subscription OAuth token
# instead (handled separately). (base_url, key_field in agent_keys.json, model)
PROVIDER_ENDPOINTS = {
    "deepseek": ("https://api.deepseek.com/anthropic", "deepseek_api_key", "deepseek-v4-flash"),
    "glm":      ("https://api.z.ai/api/anthropic", "glm_api_key", "glm-4.7"),
    "kimi":     ("https://api.moonshot.ai/anthropic", "moonshot_api_key", "kimi-k2.7-code"),
}
DEFAULT_PROVIDER = "claude_code"

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
        # The control API serves arm/kill/resume/status on a thread pool, so kill()
        # can race arm()/snapshot(). The lock keeps correlated fields consistent —
        # snapshot() can never report armed=True together with killed=True.
        self._lock = threading.Lock()

    def _armed_unlocked(self) -> bool:
        if self._killed or self._armed_at is None:
            return False
        return (self._now() - self._armed_at) < self._ttl

    def arm(self) -> None:
        """Arm the agent. Silent no-op while killed (KILL is supreme until
        clear_kill()); callers read can_act()/the /agent/arm response for the truth."""
        with self._lock:
            if self._killed:
                return
            self._armed_at = self._now()

    def disarm(self) -> None:
        with self._lock:
            self._armed_at = None

    def kill(self) -> None:
        with self._lock:
            self._killed = True
            self._armed_at = None

    def clear_kill(self) -> None:
        with self._lock:
            self._killed = False

    @property
    def killed(self) -> bool:
        with self._lock:
            return self._killed

    @property
    def armed(self) -> bool:
        with self._lock:
            return self._armed_unlocked()

    def can_act(self) -> bool:
        with self._lock:
            return self._armed_unlocked() and not self._killed

    def snapshot(self) -> dict:
        with self._lock:
            return {"armed": self._armed_unlocked(), "killed": self._killed, "ttl_sec": self._ttl}


def _run_claude_cli(argv: list[str], env: dict, stdin_text: str, timeout: float) -> str:
    """Run the claude CLI with the prompt on stdin. Module-level so tests inject a fake."""
    try:
        proc = subprocess.run(argv, env=env, input=stdin_text,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"claude CLI timed out after {int(timeout)}s")
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()[:300]
        token = env.get("CLAUDE_CODE_OAUTH_TOKEN")
        if token and token in msg:   # never surface the OAuth token, even on a crash dump
            msg = msg.replace(token, "<redacted>")
        raise RuntimeError(f"claude exited {proc.returncode}: {msg[:200]}")
    return proc.stdout


def _load_keys(keys_path: str) -> dict:
    with open(keys_path) as fh:
        return json.load(fh)


def _load_token(keys_path: str) -> str:
    token = _load_keys(keys_path).get("claude_code_oauth_token")
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
    # session_id is keyed per provider — a Claude conversation can't --resume under
    # DeepSeek, so each provider threads its own session.
    _session_ids: dict = field(default_factory=dict)

    def _provider_env(self, provider: str) -> dict:
        """Env for the claude subprocess. claude_code → subscription OAuth token;
        a vendor provider → ANTHROPIC_* pointed at its endpoint with its API key."""
        if provider == DEFAULT_PROVIDER:
            return {**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": _load_token(self.keys_path)}
        if provider not in PROVIDER_ENDPOINTS:
            raise RuntimeError(f"provider_unconfigured: unknown provider {provider!r}")
        base_url, key_field, model = PROVIDER_ENDPOINTS[provider]
        keys = _load_keys(self.keys_path)
        api_key = keys.get(key_field)
        if not api_key:
            raise RuntimeError(f"provider_unconfigured: {key_field} missing for {provider}")
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_OAUTH_TOKEN"}
        env["ANTHROPIC_BASE_URL"] = base_url
        env["ANTHROPIC_AUTH_TOKEN"] = str(api_key)
        env["ANTHROPIC_MODEL"] = model
        return env

    def chat(self, message: str, status_text: str, armed: bool = False,
             provider: str = DEFAULT_PROVIDER) -> dict:
        env = self._provider_env(provider)
        argv = [self.cli_path, "--output-format", "json",
                "--append-system-prompt", AGENT_SYSTEM_PROMPT]
        sid = self._session_ids.get(provider)
        if sid:
            argv += ["--resume", sid]
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
        try:
            data = json.loads(out)
        except (json.JSONDecodeError, ValueError):
            self._session_ids[provider] = None   # corrupted/partial turn — fresh session next time
            raise RuntimeError("claude returned non-JSON output")
        new_sid = data.get("session_id") or sid
        self._session_ids[provider] = new_sid
        return {"reply": data.get("result", ""), "session_id": new_sid or ""}
