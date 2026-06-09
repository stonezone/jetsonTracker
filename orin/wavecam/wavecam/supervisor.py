"""Deterministic WaveCam supervisor.

Polls the LOCAL Control API (``/api/v1/status`` on 127.0.0.1) and systemd unit
states, then publishes a compact health snapshot to a JSON file that the iOS
Agent panel and WaveCam web UI can surface.

Design constraints (match the supervisor-layer design + option c):
- LOCAL ONLY: talks to 127.0.0.1 and ``systemctl`` -- no internet, so it keeps
  running in the field with no uplink.
- NEVER touches VISCA / motors: all camera authority stays in the WaveCam core.
  This process only observes and publishes; it is not in the real-time loop.
- The health watcher remains observe-only. The Control API may use the narrow
  restart helper below after its own auth, confirmation, and PTZ-stop gates pass.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_API = "http://127.0.0.1:8088/api/v1"
DEFAULT_UNITS = ("wavecam.service",)
DEFAULT_HEALTH_PATH = "/run/wavecam/supervisor.json"
DEFAULT_INTERVAL_SEC = 2.0

# status-snapshot short name -> systemd unit (for control_api integration)
SNAPSHOT_SERVICE_NAMES = {
    "wavecam": "wavecam.service",
}


@dataclass
class SupervisorConfig:
    api_base: str = DEFAULT_API
    units: tuple[str, ...] = DEFAULT_UNITS
    health_path: str = DEFAULT_HEALTH_PATH
    interval_sec: float = DEFAULT_INTERVAL_SEC
    token: str | None = None


# ---------------------------------------------------------------------------
# Pure logic (unit-tested; no I/O)
# ---------------------------------------------------------------------------

def service_ok(state: str) -> bool:
    return state.strip() == "active"


def build_health(api_ok: bool, api_status: dict | None, services: dict[str, str], now_ms: int) -> dict:
    """Compose the health snapshot from already-collected inputs (no I/O)."""
    session = (api_status or {}).get("session") or {}
    safety = (api_status or {}).get("safety") or {}
    return {
        "supervisor": "running",
        "checked_at_unix_ms": now_ms,
        "api_ok": api_ok,
        "session_state": session.get("state") if api_ok else None,
        "killed": bool(safety.get("killed", False)) if api_ok else None,
        "services": {name: {"state": state, "ok": service_ok(state)} for name, state in services.items()},
        "all_services_ok": all(service_ok(s) for s in services.values()) if services else False,
    }


def snapshot_services(health: dict | None) -> dict:
    """Map a supervisor health snapshot to the status-snapshot 'services' field.

    All-'unknown' when no health is available (supervisor not running / no file),
    so the status contract is unchanged when the supervisor is absent.
    """
    if not health:
        services = {short: "unknown" for short in SNAPSHOT_SERVICE_NAMES}
        services["supervisor"] = "unknown"
        return services
    reported = health.get("services", {})
    services = {
        short: str(reported.get(unit, {}).get("state", "unknown"))
        for short, unit in SNAPSHOT_SERVICE_NAMES.items()
    }
    services["supervisor"] = str(health.get("supervisor", "unknown"))
    return services


# ---------------------------------------------------------------------------
# I/O (thin wrappers around localhost + systemctl + the health file)
# ---------------------------------------------------------------------------

def poll_api(base_url: str, token: str | None, timeout: float = 4.0) -> tuple[bool, dict | None]:
    """GET <base>/status from localhost. Returns (ok, parsed) and never raises."""
    request = urllib.request.Request(f"{base_url}/status")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return True, json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError):
        return False, None


def systemd_state(unit: str) -> str:
    """`systemctl is-active <unit>` -> active/inactive/failed/unknown. Never raises."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True, text=True, timeout=4.0,
        )
        return (result.stdout.strip() or result.stderr.strip() or "unknown")
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def restart_systemd_unit(unit: str = "wavecam.service") -> None:
    """Ask systemd to restart one known unit without invoking a shell."""
    if unit != "wavecam.service":
        raise ValueError("Only wavecam.service restart is supported.")
    command = ["systemctl", "restart", unit]
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        command = ["sudo", "-n", *command]
    subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def write_health(path: str, health: dict) -> None:
    """Atomically publish the health snapshot."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(health, handle)
    os.replace(tmp, path)


def read_health(path: str | None = None) -> dict | None:
    """Read the published health snapshot; None if missing/unreadable. Never raises."""
    path = path or os.environ.get("WAVECAM_SUPERVISOR_HEALTH", DEFAULT_HEALTH_PATH)
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def poll_once(cfg: SupervisorConfig, now_ms: int) -> dict:
    api_ok, status = poll_api(cfg.api_base, cfg.token)
    services = {unit: systemd_state(unit) for unit in cfg.units}
    return build_health(api_ok, status, services, now_ms)


def run(cfg: SupervisorConfig) -> None:
    while True:
        health = poll_once(cfg, int(time.time() * 1000))
        try:
            write_health(cfg.health_path, health)
        except OSError as exc:  # pragma: no cover - depends on filesystem perms
            print(f"[supervisor] health write failed: {exc}")
        time.sleep(cfg.interval_sec)


def config_from_env() -> SupervisorConfig:
    return SupervisorConfig(
        api_base=os.environ.get("WAVECAM_API_BASE", DEFAULT_API),
        health_path=os.environ.get("WAVECAM_SUPERVISOR_HEALTH", DEFAULT_HEALTH_PATH),
        token=os.environ.get("WAVECAM_SUPERVISOR_TOKEN") or None,
    )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="WaveCam deterministic health supervisor.")
    parser.add_argument("--once", action="store_true", help="Poll once, print health JSON, exit.")
    args = parser.parse_args()

    cfg = config_from_env()
    if args.once:
        print(json.dumps(poll_once(cfg, int(time.time() * 1000)), indent=2))
        return 0
    run(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
