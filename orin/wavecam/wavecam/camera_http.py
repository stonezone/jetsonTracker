"""
Force the camera's onboard auto-track OFF so the Orin owns the loop.

Best-effort: hits the vendor HTTP CGI. The exact query for set_aimode is
camera-specific — reconcile camera_ai.off_path in config.yaml with your working
CGI (you already have get_aimode/set_aimode). Non-fatal: if it fails, the rig
still runs — just confirm AI-track is OFF in the camera web UI.
"""
from __future__ import annotations
import urllib.request


def disable_onboard_ai(cfg_ai) -> bool:
    if not cfg_ai.disable_on_start:
        return False
    url = (cfg_ai.http_base or "").rstrip("/") + (cfg_ai.off_path or "")
    if not cfg_ai.http_base or not cfg_ai.off_path:
        print("[camera_ai] no CGI configured; confirm AI-track is OFF in the camera web UI")
        return False
    try:
        with urllib.request.urlopen(url, timeout=2.0) as r:
            ok = 200 <= r.status < 300
        print(f"[camera_ai] set_aimode off -> {'ok' if ok else r.status}  ({url})")
        return ok
    except Exception as e:
        print(f"[camera_ai] set_aimode off failed ({e}); "
              f"confirm AI-track is OFF in the camera web UI")
        return False
