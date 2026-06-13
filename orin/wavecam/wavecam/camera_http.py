"""
Force the camera's onboard auto-track OFF so the Orin owns the loop.

The Prisual speaks the param.cgi family with HTTP basic auth (probed live
2026-06-12 on fw X8.03.15): ``post_aimode&off`` sets, ``get_aimode`` reads
back ("get_aimode=Off"). We VERIFY after setting rather than trusting the
status code — the previous folklore endpoint returned 500 for a year and
nobody knew. Non-fatal: if anything fails, the rig still boots; the outcome
lands in /events either way.
"""
from __future__ import annotations

import base64
import urllib.request


def _get(url: str, user: str, password: str, timeout: float = 2.0) -> str:
    req = urllib.request.Request(url)
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(errors="replace")


def disable_onboard_ai(cfg_ai, events=None, http_get=_get) -> bool:
    """Disable the onboard AI tracker and verify it reads back Off.

    Args:
        cfg_ai: CameraAiCfg — endpoints, auth, and the master switch.
        events: optional EventRing — outcome surfaces at /events.
        http_get: injectable transport for tests.
    """
    if not cfg_ai.disable_on_start:
        return False
    if not cfg_ai.http_base or not cfg_ai.off_path:
        print("[camera_ai] no CGI configured; confirm AI-track is OFF in the camera web UI")
        return False

    base = cfg_ai.http_base.rstrip("/")
    user, password = cfg_ai.http_user, cfg_ai.http_pass
    try:
        http_get(base + cfg_ai.off_path, user, password)
        if cfg_ai.verify_path:
            state = http_get(base + cfg_ai.verify_path, user, password)
            if "off" not in state.lower():
                raise RuntimeError(f"set accepted but readback was {state.strip()!r}")
            detail = "disabled (verified Off)"
        else:
            detail = "disabled (unverified — no verify_path)"
        print(f"[camera_ai] onboard AI {detail}")
        if events is not None:
            events.record("camera_ai", detail)
        return True
    except Exception as e:
        print(f"[camera_ai] disable failed ({e}); "
              f"confirm AI-track is OFF in the camera web UI")
        if events is not None:
            events.record("camera_ai",
                          "disable FAILED — onboard tracker may fight the loop")
        return False
