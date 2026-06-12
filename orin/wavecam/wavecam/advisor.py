"""LLM advisor — the brain behind /api/v1/agent/summon.

SUPERVISE-ONLY by construction: the advisor receives a read-only context
snapshot (status, recent events, health) and returns prose diagnostics.
It is given no tools, no endpoints, and no way to move the camera — the
hard rule lives in the architecture, not in the prompt alone.

Three providers, selected per-summon from the iOS app:
  claude    Anthropic Messages API, OAuth bearer (Claude Code token —
            shares the operator's subscription quota).
  codex     OpenAI Responses API (API key; ChatGPT-plan OAuth tokens only
            work inside the Codex CLI, so this is the supportable path).
  deepseek  DeepSeek chat completions (OpenAI-compatible), API key.

All transport is stdlib urllib: the rig's Python environment is a frozen,
pinned set (documented 2026-06-11) and a diagnostics feature does not
justify new network-stack dependencies. Request/response shapes follow the
documented raw-HTTP forms for each API (Anthropic: version + oauth beta
headers; model IDs verified live 2026-06-12).

Credentials live in a rig-owned file outside the repo and outside rsync
(same pattern as auth.json / camera_pose.json). Never logged, never echoed
back through the API.

Threading contract (the 2026-06-08 lesson): summon() spawns a daemon
thread and returns immediately; report() is a lock-guarded snapshot read.
Nothing here may ever block the HTTP request thread or the vision loop.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

KEYS_PATH = "/data/projects/gimbal/agent_keys.json"

REQUEST_TIMEOUT_SEC = 60.0
MAX_REPLY_TOKENS = 1500
EVENTS_TAIL = 30

SYSTEM_PROMPT = (
    "You are the WaveCam supervisor: a diagnostics advisor for an "
    "autonomous PTZ camera that films a foil surfer 50-300m offshore "
    "using YOLO person detection, an orange-rashguard color cue, and "
    "LoRa GPS coarse pointing. You are SUPERVISE-ONLY: you have no tools "
    "and no ability to move the camera or change configuration — and you "
    "must never instruct anyone to bypass that. You receive a snapshot of "
    "live status, recent events, and health. Reply with: (1) a one-line "
    "verdict (HEALTHY / DEGRADED / FAULT), (2) the evidence for it, "
    "(3) the most likely cause of any anomaly, (4) what the operator "
    "should check or tune, in priority order. Be terse and concrete; "
    "reference the exact fields and values you used."
)


def _load_key(keys_path: str, name: str) -> str:
    try:
        with open(keys_path) as f:
            keys = json.load(f)
    except FileNotFoundError:
        raise RuntimeError(
            f"agent keys file missing on this host ({keys_path}); "
            "deploy it before summoning"
        )
    value = keys.get(name)
    if not value:
        raise RuntimeError(f"no '{name}' key in {keys_path}")
    return value


def _default_post(url: str, headers: dict, body: dict,
                  timeout: float = REQUEST_TIMEOUT_SEC) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"content-type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        # Surface the API's own error message, not just the status code.
        try:
            detail = json.loads(e.read().decode())
            msg = (detail.get("error") or {}).get("message") or str(detail)[:200]
        except Exception:
            msg = ""
        raise RuntimeError(f"HTTP {e.code} from provider: {msg}") from e


# ── provider request/response shaping (pure functions) ──────────────────────

def _claude_request(keys_path: str, prompt: str) -> tuple[str, dict, dict]:
    token = _load_key(keys_path, "claude_oauth_token")
    return (
        "https://api.anthropic.com/v1/messages",
        {
            "Authorization": f"Bearer {token}",
            "anthropic-version": "2023-06-01",
            # OAuth bearer tokens require this beta header on /v1/messages.
            "anthropic-beta": "oauth-2025-04-20",
        },
        {
            "model": "claude-opus-4-8",
            "max_tokens": MAX_REPLY_TOKENS,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        },
    )


def _claude_parse(resp: dict) -> str:
    return "".join(
        b.get("text", "") for b in resp.get("content", [])
        if b.get("type") == "text"
    )


def _openai_compat_parse(resp: dict) -> str:
    # Chat-completions shape (DeepSeek).
    choices = resp.get("choices")
    if choices:
        return choices[0].get("message", {}).get("content", "")
    # Responses-API shape (OpenAI): output[] -> message -> content[] -> text.
    parts = []
    for item in resp.get("output", []):
        for c in item.get("content", []):
            if c.get("type") == "output_text":
                parts.append(c.get("text", ""))
    return "".join(parts)


def _codex_request(keys_path: str, prompt: str) -> tuple[str, dict, dict]:
    key = _load_key(keys_path, "openai_api_key")
    return (
        "https://api.openai.com/v1/responses",
        {"Authorization": f"Bearer {key}"},
        {
            "model": "gpt-5.5",
            "max_output_tokens": MAX_REPLY_TOKENS,
            "instructions": SYSTEM_PROMPT,
            "input": prompt,
        },
    )


def _deepseek_request(keys_path: str, prompt: str) -> tuple[str, dict, dict]:
    key = _load_key(keys_path, "deepseek_api_key")
    return (
        "https://api.deepseek.com/chat/completions",
        {"Authorization": f"Bearer {key}"},
        {
            # deepseek-chat is deprecated 2026-07-24; v4-flash is its successor.
            "model": "deepseek-v4-flash",
            "max_tokens": MAX_REPLY_TOKENS,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        },
    )


PROVIDERS: dict[str, tuple[Callable, Callable]] = {
    "claude": (_claude_request, _claude_parse),
    "codex": (_codex_request, _openai_compat_parse),
    "deepseek": (_deepseek_request, _openai_compat_parse),
}


class AdvisorService:
    """One consultation at a time; state machine idle -> running -> done|error."""

    def __init__(self, context_fn: Callable[[], dict],
                 keys_path: str = KEYS_PATH,
                 post_fn: Callable = _default_post) -> None:
        self._context_fn = context_fn
        self._keys_path = keys_path
        self._post = post_fn
        self._lock = threading.Lock()
        self._state: dict = {"status": "idle"}

    def summon(self, provider: str) -> tuple[bool, str]:
        """Start a consultation. Returns (accepted, message) immediately."""
        if provider not in PROVIDERS:
            return False, f"unknown provider '{provider}' (have: {', '.join(sorted(PROVIDERS))})"
        with self._lock:
            if self._state.get("status") == "running":
                return False, f"a {self._state.get('provider')} consultation is already running"
            self._state = {
                "status": "running",
                "provider": provider,
                "started_at": time.time(),
            }
        threading.Thread(
            target=self._consult, args=(provider,),
            daemon=True, name=f"advisor-{provider}",
        ).start()
        return True, f"{provider} consultation started"

    def report(self) -> dict:
        with self._lock:
            return dict(self._state)

    # ── worker thread ────────────────────────────────────────────────────

    def _consult(self, provider: str) -> None:
        started = time.time()
        try:
            prompt = self._build_prompt()
            build, parse = PROVIDERS[provider]
            url, headers, body = build(self._keys_path, prompt)
            text = parse(self._post(url, headers, body)).strip()
            if not text:
                raise RuntimeError("provider returned an empty reply")
            result = {"status": "done", "text": text}
        except Exception as e:  # any failure is a report, never a crash
            result = {"status": "error", "error": str(e)[:500]}
        with self._lock:
            self._state = {
                "provider": provider,
                "started_at": started,
                "duration_sec": round(time.time() - started, 1),
                **result,
            }

    def _build_prompt(self) -> str:
        ctx = self._context_fn() or {}
        events = ctx.get("events")
        if isinstance(events, list) and len(events) > EVENTS_TAIL:
            ctx["events"] = events[-EVENTS_TAIL:]
        return (
            "Current WaveCam snapshot (JSON):\n"
            + json.dumps(ctx, default=str)[:24000]
            + "\n\nAssess the system now."
        )
