"""Config-gated LAN bearer-token auth + role gate for the WaveCam Control API.

DISABLED by default: with no auth config present, every request passes, preserving
the open bring-up/dev behavior so existing clients (incl. the shipped iOS app that
has no token set yet) keep working unchanged. When an auth config with tokens is
loaded, each request must carry ``Authorization: Bearer <token>`` whose role
permits the endpoint's action class.

Tokens are read from a LOCAL JSON file (path via ``$WAVECAM_AUTH_FILE``) only --
no network or internet dependency, so auth works in the field with no uplink.

Availability note: NO auth file configured (``$WAVECAM_AUTH_FILE`` unset) yields a
DISABLED config (fail-open) -- on a private camera LAN, operator lockout is a worse
failure mode than an open local port. But once an operator has gone to the trouble
of SETTING ``$WAVECAM_AUTH_FILE``, a missing/unreadable/malformed file at that path
fails CLOSED (raises at install time, refusing to boot): silently falling back to
open auth there would be a config typo away from serving an unauthenticated agent
shell to the LAN (audit 2026-07-01 C2) with no boot-time signal that it happened.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse

# Action classes an endpoint can require.
READ = "read"
SAFETY = "safety"
PTZ = "ptz"
CONFIG = "config"
SERVICE = "service"

# Role -> permitted action classes (mirrors the Control API spec role matrix).
# `agent` is read-only at the API in v1: Codex acts on-demand and only gains write
# actions behind the operator's explicit "agent control" gate (not built yet).
ROLE_ACTIONS: dict[str, frozenset[str]] = {
    "operator": frozenset({READ, SAFETY, PTZ, CONFIG, SERVICE}),
    "viewer": frozenset({READ}),
    "supervisor": frozenset({READ, SAFETY, CONFIG, SERVICE}),
    "agent": frozenset({READ}),
}


@dataclass(frozen=True)
class AuthConfig:
    enabled: bool = False
    tokens: dict[str, str] = field(default_factory=dict)  # token -> role

    def role_for(self, token: str | None) -> str | None:
        return self.tokens.get(token) if token else None


class AuthError(Exception):
    """Raised by the auth gate; rendered to the API's {ok:false, code, message} shape."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def load_auth(path: str | None = None) -> AuthConfig:
    """Load auth from a local JSON file.

    No path configured at all (neither *path* nor ``$WAVECAM_AUTH_FILE``) ->
    disabled (fail-open); this is the unconfigured/dev-bringup case.

    A path IS configured but the file is missing/unreadable/invalid JSON ->
    raises ``RuntimeError`` (fail CLOSED). An operator who set
    ``$WAVECAM_AUTH_FILE`` intended auth to be enforced; silently degrading to
    open auth on a typo'd path or a corrupted file is the worse failure mode
    (audit 2026-07-01 C2/M17) -- the service should refuse to boot instead.

    File shape: ``{"enabled": true, "tokens": {"<token>": "operator", ...}}``
    """
    configured_path = path or os.environ.get("WAVECAM_AUTH_FILE")
    if not configured_path:
        return AuthConfig()
    if not os.path.exists(configured_path):
        raise RuntimeError(
            f"WAVECAM_AUTH_FILE is set to {configured_path!r} but the file does not "
            "exist; refusing to start with auth silently disabled."
        )
    try:
        with open(configured_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"WAVECAM_AUTH_FILE {configured_path!r} could not be read/parsed "
            f"({exc}); refusing to start with auth silently disabled."
        ) from exc
    tokens = {str(k): str(v) for k, v in dict(data.get("tokens", {})).items()}
    return AuthConfig(enabled=bool(data.get("enabled", True)), tokens=tokens)


def bearer_token(headers) -> str | None:
    value = headers.get("authorization") or headers.get("Authorization")
    if not value:
        return None
    parts = value.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip() or None
    return None


def authorize(auth: AuthConfig, token: str | None, action: str) -> None:
    """Raise AuthError unless `action` is permitted. No-op when auth is disabled."""
    if not auth.enabled:
        return
    role = auth.role_for(token)
    if role is None:
        raise AuthError("unauthorized", "Missing or invalid bearer token.", 401)
    if action not in ROLE_ACTIONS.get(role, frozenset()):
        raise AuthError("forbidden", f"Role '{role}' may not perform '{action}'.", 403)


def require(action: str, allow_query_token: bool = False):
    """FastAPI dependency factory enforcing `action` against the request's role.

    When *allow_query_token* is True, a ``?token=…`` query parameter is accepted
    as a fallback bearer token so streaming endpoints that cannot attach headers
    (e.g. iOS URLSessionDataTask MJPEG) can authenticate."""

    def dependency(request: Request) -> None:
        auth = getattr(request.app.state, "auth", None) or AuthConfig()
        token = bearer_token(request.headers)
        if token is None and allow_query_token:
            token = request.query_params.get("token") or None
        authorize(auth, token, action)

    return dependency


def websocket_authorized(websocket: WebSocket, action: str = READ) -> bool:
    """True if the websocket may proceed (same bearer header, or ?token= fallback)."""
    auth = getattr(websocket.app.state, "auth", None) or AuthConfig()
    if not auth.enabled:
        return True
    token = bearer_token(websocket.headers) or websocket.query_params.get("token")
    try:
        authorize(auth, token, action)
        return True
    except AuthError:
        return False


def install_auth(app: FastAPI, path: str | None = None) -> None:
    """Load auth config onto app.state and register the AuthError -> JSON handler.

    Logs the resulting auth state loudly at boot (C2) -- an operator staring at
    the service log should never have to guess whether the LAN port is open.
    """
    auth = load_auth(path)
    app.state.auth = auth
    if auth.enabled:
        print(f"[auth] ENABLED — {len(auth.tokens)} token(s) configured.")
    else:
        print("[auth] DISABLED — every request is accepted with no bearer token "
              "(open on the LAN/tether). Set WAVECAM_AUTH_FILE to enable.")

    @app.exception_handler(AuthError)
    async def _auth_error_handler(_: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse(
            {"ok": False, "code": exc.code, "message": exc.message},
            status_code=exc.status_code,
        )
