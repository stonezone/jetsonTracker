"""LLM advisor — the brain behind /api/v1/agent/summon.

SUPERVISE-ONLY by construction: the advisor receives a read-only context
snapshot (status, recent events) and returns prose diagnostics. It is
given no tools, no endpoints, and no way to move the camera — the hard
rule lives in the architecture, not in the prompt alone.

Three providers, selected per-summon from the iOS app. Auth policy
(operator directive 2026-06-12): OpenAI and Anthropic go through OAuth
ONLY — never API keys; DeepSeek is API-key (it has no OAuth).

  claude    Anthropic Messages API, OAuth bearer (Claude Code token —
            shares the operator's subscription quota).
  codex     ChatGPT-plan OAuth against the Codex backend
            (chatgpt.com/backend-api/codex/responses, SSE). Access
            tokens expire; on 401/403 the provider refreshes via
            auth.openai.com using the stored refresh_token and persists
            the rotated tokens back to the keys file, exactly as the
            Codex CLI does. Plan accounts serve `gpt-5.5` (the
            `-codex` model variants are CLI-only — verified live).
  deepseek  DeepSeek chat completions (OpenAI-compatible), API key.

All transport is stdlib urllib: the rig's Python environment is a frozen,
pinned set (documented 2026-06-11) and a diagnostics feature does not
justify new network-stack dependencies. Request/response shapes and model
IDs (claude-opus-4-8, gpt-5.5, deepseek-v4-flash — deepseek-chat retires
2026-07-24) were all verified with live calls on 2026-06-12.

Credentials live in a rig-owned 0600 file outside the repo and outside
rsync (same pattern as auth.json / camera_pose.json). Never logged,
never echoed back through the API.

Threading contract (the 2026-06-08 lesson): summon() spawns a daemon
thread and returns immediately; report() is a lock-guarded snapshot read.
Nothing here may ever block the HTTP request thread or the vision loop.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Callable

KEYS_PATH = "/data/projects/gimbal/agent_keys.json"

REQUEST_TIMEOUT_SEC = 60.0
MAX_REPLY_TOKENS = 1500
EVENTS_TAIL = 30

CODEX_BACKEND_URL = "https://chatgpt.com/backend-api/codex/responses"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"  # the Codex CLI public client

# Claude (Anthropic) OAuth refresh — mirrors the Codex flow. VERIFY LIVE before
# deploy: confirm the token endpoint + public client_id against a real Claude Code /
# `ant auth login` session and prove one refresh round-trip on the rig (the same
# discipline that verified the Codex flow on 2026-06-12). The offline test exercises
# the refresh LOGIC via an injected post, independent of these exact values.
CLAUDE_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
CLAUDE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"  # Claude Code public client — VERIFY LIVE

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


class ProviderHTTPError(RuntimeError):
    """HTTP failure from a provider, carrying the status code so callers
    can distinguish auth expiry (refreshable) from everything else."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"HTTP {code} from provider: {message}")
        self.code = code


def _load_keys(keys_path: str) -> dict:
    try:
        with open(keys_path) as f:
            return json.load(f)
    except FileNotFoundError:
        raise RuntimeError(
            f"agent keys file missing on this host ({keys_path}); "
            "deploy it before summoning"
        )


def _require(keys: dict, name: str, keys_path: str) -> str:
    value = keys.get(name)
    if not value:
        raise RuntimeError(f"no '{name}' key in {keys_path}")
    return value


def _save_keys(keys_path: str, keys: dict) -> None:
    """Atomic 0600 rewrite — rotated codex tokens must survive a crash."""
    tmp = keys_path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(keys, f)
    os.replace(tmp, keys_path)


def _default_post(url: str, headers: dict, body: dict,
                  timeout: float = REQUEST_TIMEOUT_SEC) -> str:
    """POST json, return the raw response body text (json or SSE)."""
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"content-type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode()
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode())
            msg = ((detail.get("error") or {}).get("message")
                   or detail.get("detail") or str(detail)[:200])
        except Exception:
            msg = ""
        raise ProviderHTTPError(e.code, msg) from e


# ── providers: each is consult(keys_path, prompt, post) -> reply text ───────

def _claude_call(keys: dict, prompt: str, post: Callable) -> str:
    raw = post(
        "https://api.anthropic.com/v1/messages",
        {
            "Authorization": f"Bearer {keys['claude_oauth_token']}",
            "anthropic-version": "2023-06-01",
            # OAuth bearer tokens are rejected on /v1/messages without this.
            "anthropic-beta": "oauth-2025-04-20",
        },
        {
            "model": "claude-opus-4-8",
            "max_tokens": MAX_REPLY_TOKENS,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    resp = json.loads(raw)
    return "".join(
        b.get("text", "") for b in resp.get("content", [])
        if b.get("type") == "text"
    )


def _claude_refresh(keys_path: str, keys: dict, post: Callable) -> dict:
    """Exchange the Claude refresh token; persist rotated tokens (mirrors
    _codex_refresh). The access token stays under the existing
    ``claude_oauth_token`` key so the request shape is unchanged."""
    raw = post(
        CLAUDE_TOKEN_URL, {},
        {
            "client_id": CLAUDE_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": _require(keys, "claude_refresh_token", keys_path),
        },
    )
    fresh = json.loads(raw)
    keys["claude_oauth_token"] = fresh["access_token"]
    if fresh.get("refresh_token"):
        keys["claude_refresh_token"] = fresh["refresh_token"]
    _save_keys(keys_path, keys)
    return keys


def _consult_claude(keys_path: str, prompt: str, post: Callable) -> str:
    """OAuth bearer on /v1/messages; refresh the access token on expiry and retry
    once — mirrors _consult_codex so Summon survives token rotation."""
    keys = _load_keys(keys_path)
    _require(keys, "claude_oauth_token", keys_path)
    try:
        return _claude_call(keys, prompt, post)
    except ProviderHTTPError as e:
        if e.code not in (401, 403):
            raise
        keys = _claude_refresh(keys_path, keys, post)
        return _claude_call(keys, prompt, post)


def _consult_deepseek(keys_path: str, prompt: str, post: Callable) -> str:
    key = _require(_load_keys(keys_path), "deepseek_api_key", keys_path)
    raw = post(
        "https://api.deepseek.com/chat/completions",
        {"Authorization": f"Bearer {key}"},
        {
            "model": "deepseek-v4-flash",
            "max_tokens": MAX_REPLY_TOKENS,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        },
    )
    choices = json.loads(raw).get("choices") or [{}]
    return choices[0].get("message", {}).get("content", "")


def _codex_refresh(keys_path: str, keys: dict, post: Callable) -> dict:
    """Exchange the refresh token; persist rotated tokens (CLI behavior)."""
    raw = post(
        CODEX_TOKEN_URL, {},
        {
            "client_id": CODEX_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": _require(keys, "codex_refresh_token", keys_path),
            "scope": "openid profile email",
        },
    )
    fresh = json.loads(raw)
    keys["codex_access_token"] = fresh["access_token"]
    if fresh.get("refresh_token"):
        keys["codex_refresh_token"] = fresh["refresh_token"]
    _save_keys(keys_path, keys)
    return keys


def _codex_call(keys: dict, prompt: str, post: Callable) -> str:
    raw = post(
        CODEX_BACKEND_URL,
        {
            "Authorization": f"Bearer {keys['codex_access_token']}",
            "chatgpt-account-id": keys["codex_account_id"],
            "OpenAI-Beta": "responses=experimental",
            "originator": "codex_cli_rs",
            "accept": "text/event-stream",
        },
        {
            # ChatGPT-plan accounts serve gpt-5.5 here; -codex variants 400.
            "model": "gpt-5.5",
            "instructions": SYSTEM_PROMPT,
            "input": [{
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": prompt}],
            }],
            "stream": True,   # the backend only streams
            "store": False,
        },
    )
    parts = []
    for line in raw.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            try:
                event = json.loads(line[6:])
            except ValueError:
                continue
            if event.get("type") == "response.output_text.delta":
                parts.append(event.get("delta", ""))
    return "".join(parts)


def _consult_codex(keys_path: str, prompt: str, post: Callable) -> str:
    """ChatGPT-plan OAuth: try the stored access token, refresh on expiry."""
    keys = _load_keys(keys_path)
    _require(keys, "codex_access_token", keys_path)
    _require(keys, "codex_account_id", keys_path)
    try:
        return _codex_call(keys, prompt, post)
    except ProviderHTTPError as e:
        if e.code not in (401, 403):
            raise
        keys = _codex_refresh(keys_path, keys, post)
        return _codex_call(keys, prompt, post)


PROVIDERS: dict[str, Callable[[str, str, Callable], str]] = {
    "claude": _consult_claude,
    "codex": _consult_codex,
    "deepseek": _consult_deepseek,
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
            text = PROVIDERS[provider](self._keys_path, prompt, self._post).strip()
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
