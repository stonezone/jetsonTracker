"""Config-gated LAN bearer-token auth + role gate for the WaveCam Control API.

DISABLED by default: with no auth config present, every request passes, preserving
the open bring-up/dev behavior so existing clients (incl. the shipped iOS app that
has no token set yet) keep working unchanged. When an auth config with tokens is
loaded, each request must carry ``Authorization: Bearer <token>`` whose role
permits the endpoint's action class.

Tokens are read from a LOCAL JSON file (path via ``$WAVECAM_AUTH_FILE``) only --
no network or internet dependency, so auth works in the field with no uplink.

Availability note: a missing or unreadable auth file yields a DISABLED config
(fail-open). On a private camera LAN, operator lockout is a worse failure mode than
an open local port; tighten to fail-closed if the threat model ever includes the
local network.
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
    "operator": frozenset({READ, SAFETY, PTZ, CONFIG}),
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
    """Load auth from a local JSON file; missing/unreadable -> disabled (fail-open).

    File shape: ``{"enabled": true, "tokens": {"<token>": "operator", ...}}``
    """
    path = path or os.environ.get("WAVECAM_AUTH_FILE")
    if not path or not os.path.exists(path):
        return AuthConfig()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return AuthConfig()
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


def require(action: str):
    """FastAPI dependency factory enforcing `action` against the request's role."""

    def dependency(request: Request) -> None:
        auth = getattr(request.app.state, "auth", None) or AuthConfig()
        authorize(auth, bearer_token(request.headers), action)

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
    """Load auth config onto app.state and register the AuthError -> JSON handler."""
    app.state.auth = load_auth(path)

    @app.exception_handler(AuthError)
    async def _auth_error_handler(_: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse(
            {"ok": False, "code": exc.code, "message": exc.message},
            status_code=exc.status_code,
        )
