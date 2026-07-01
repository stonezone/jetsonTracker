"""Interactive acting-agent: the arm-state safety machine + the claude -p driver.

Phase 1a builds the conversation + safety bridge only — no acting tools yet.
ArmState is the supervise-only gate: DISARMED by default, an ARMED session
auto-expires after a TTL, and KILL is supreme (disarms and forbids re-arm until
explicitly cleared). Acting tiers (Phase 1b+) will read ``can_act()`` before any
mutating tool runs.
"""
from __future__ import annotations

import inspect
import json
import os
import signal
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
    "the Bash tool — always check the JSON for ok:true; a refusal is a non-2xx status with "
    "ok:false, code, and message (the agent chat/arm/summon routes are the same shape). "
    "HARD RULES you must never break: (1) the operator's KILL / Emergency Stop is human-only — never "
    "POST /safety/kill or /safety/resume, and never try to disable, bypass, or re-arm around them; "
    "never POST /system/restart either — a restart is a service-level action outside your scope. "
    "(2) The camera is supervise-only: move it (ptz/* or calibration/*) only when the operator "
    "explicitly asks this turn, and remember it always yields to a manual aim and to KILL. "
    "(3) Prefer reversible config tuning; describe any risky or hard-to-undo step before doing it. "
    "When NOT armed you have no shell and can only inspect and advise. Keep replies concise and concrete."
)

# H1 defense-in-depth: forbidden actions the agent must never invoke even while armed,
# restated here (not just prose in the prompt) so future acting-tool gating can check
# a request path against this list mechanically.
AGENT_FORBIDDEN_PATHS = (
    "/safety/kill",
    "/safety/resume",
    "/system/restart",
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


def _kill_process_group(proc: subprocess.Popen) -> None:
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()   # fallback: at least kill the direct child
        except Exception:
            pass


def _run_claude_cli(argv: list[str], env: dict, stdin_text: str, timeout: float,
                    session: "Optional[AgentSession]" = None) -> str:
    """Run the claude CLI with the prompt on stdin. Module-level so tests inject
    a fake (``monkeypatch.setattr(agent_session, "_run_claude_cli", fake)``) —
    ``AgentSession.chat`` looks this name up at call time via the module
    namespace, so a monkeypatched replacement is always honored even though
    this is also the default (real) runner.

    H1 (audit 2026-07-01): launches the CLI in its OWN process group
    (``start_new_session=True``) so a KILL can SIGKILL the whole group rather
    than merely flipping ArmState while an armed turn's Bash tool keeps
    running. When *session* is given, the Popen handle is stashed on
    ``session._proc`` for the duration of the call so ``session.terminate()``
    can reach it from another thread (the request that handles /safety/kill).
    """
    proc = subprocess.Popen(
        argv, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, start_new_session=True,
    )
    if session is not None:
        with session._proc_lock:
            session._proc = proc
    try:
        try:
            out, err = proc.communicate(input=stdin_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            proc.communicate()  # reap after kill, avoid a zombie
            raise RuntimeError(f"claude CLI timed out after {int(timeout)}s")
    finally:
        if session is not None:
            with session._proc_lock:
                if session._proc is proc:
                    session._proc = None
    if proc.returncode != 0:
        msg = (err or out or "").strip()[:300]
        token = env.get("CLAUDE_CODE_OAUTH_TOKEN")
        if token and token in msg:   # never surface the OAuth token, even on a crash dump
            msg = msg.replace(token, "<redacted>")
        raise RuntimeError(f"claude exited {proc.returncode}: {msg[:200]}")
    return out


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
    run: Optional[Callable[..., str]] = None   # test seam; None -> module _run_claude_cli (real path)
    # session_id is keyed per provider — a Claude conversation can't --resume under
    # DeepSeek, so each provider threads its own session.
    _session_ids: dict = field(default_factory=dict)
    # H1: per-provider lock serializing _session_ids read/resume/write (M15) —
    # two concurrent chat turns on the same provider must not both --resume the
    # same session_id and race the last-writer-wins update.
    _session_lock: threading.Lock = field(default_factory=threading.Lock)
    # H1: the currently in-flight child process (real runner only), so agent_kill()
    # can SIGKILL its process group. None when no turn is running or the injected
    # test runner is in use (fakes have no real OS process to track).
    _proc: Optional[subprocess.Popen] = field(default=None, repr=False, compare=False)
    _proc_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

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

    def terminate(self) -> bool:
        """H1: SIGKILL the in-flight child's process group, if any. Called from
        agent_kill() so KILL actually stops a running armed turn's Bash tool
        instead of merely flipping ArmState while the subprocess runs on.
        Returns True if a process was signaled. A no-op (returns False) when
        no real subprocess is tracked — either nothing is running, or the
        injected test seam (self.run) is in use, which has no OS process for
        us to reach."""
        with self._proc_lock:
            proc = self._proc
        if proc is None or proc.poll() is not None:
            return False
        _kill_process_group(proc)
        return True

    def chat(self, message: str, status_text: str, armed: bool = False,
             provider: str = DEFAULT_PROVIDER) -> dict:
        # M15: serialize per-provider session-id read/resume/write. Two concurrent
        # turns on the same provider must not both --resume the same session_id
        # (the CLI can fork/error, and last-writer-wins would silently drop one
        # branch's conversation context) — hold the lock for the WHOLE turn
        # (argv build through session_id write), not just the dict access, or a
        # second turn could still read a stale sid between our read and write.
        with self._session_lock:
            return self._chat_locked(message, status_text, armed=armed, provider=provider)

    def _chat_locked(self, message: str, status_text: str, armed: bool,
                      provider: str) -> dict:
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
        if self.run is not None:
            out = self.run(argv, env, prompt, REQUEST_TIMEOUT_SEC)
        else:
            # Module-level (not module-import-time-captured) lookup so tests that
            # monkeypatch agent_session._run_claude_cli are honored. The real
            # implementation additionally accepts ``session=`` (H1 process-group
            # tracking) but a plain 4-arg fake (existing tests) doesn't -- inspect
            # rather than try/except so a genuine TypeError raised BY a correctly
            # invoked fake still propagates instead of being swallowed here.
            runner = _run_claude_cli
            try:
                accepts_session = "session" in inspect.signature(runner).parameters
            except (TypeError, ValueError):
                accepts_session = False
            if accepts_session:
                out = runner(argv, env, prompt, REQUEST_TIMEOUT_SEC, session=self)
            else:
                out = runner(argv, env, prompt, REQUEST_TIMEOUT_SEC)
        try:
            data = json.loads(out)
        except (json.JSONDecodeError, ValueError):
            self._session_ids[provider] = None   # corrupted/partial turn — fresh session next time
            raise RuntimeError("claude returned non-JSON output")
        new_sid = data.get("session_id") or sid
        self._session_ids[provider] = new_sid
        return {"reply": data.get("result", ""), "session_id": new_sid or ""}
