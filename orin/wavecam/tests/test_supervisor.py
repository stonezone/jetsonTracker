from __future__ import annotations

from wavecam.supervisor import (
    SupervisorConfig,
    build_health,
    poll_once,
    read_health,
    service_ok,
    snapshot_services,
    write_health,
)


def test_service_ok():
    assert service_ok("active") is True
    assert service_ok(" active ") is True
    assert service_ok("inactive") is False
    assert service_ok("failed") is False


def test_build_health_api_up():
    status = {"session": {"state": "TRACKING"}, "safety": {"killed": False}}
    health = build_health(
        True, status, {"wavecam.service": "active", "dashboard.service": "inactive"}, 1000
    )
    assert health["supervisor"] == "running"
    assert health["api_ok"] is True
    assert health["session_state"] == "TRACKING"
    assert health["killed"] is False
    assert health["services"]["wavecam.service"]["ok"] is True
    assert health["services"]["dashboard.service"]["ok"] is False
    assert health["all_services_ok"] is False
    assert health["checked_at_unix_ms"] == 1000


def test_build_health_api_down_nulls_session_but_keeps_services():
    health = build_health(False, None, {"wavecam.service": "active"}, 2000)
    assert health["api_ok"] is False
    assert health["session_state"] is None
    assert health["killed"] is None
    assert health["all_services_ok"] is True


def test_poll_once_offline_is_graceful():
    # Unreachable API + bogus unit must not raise; api_ok False, service still reported.
    cfg = SupervisorConfig(api_base="http://127.0.0.1:9/api/v1", units=("nope.service",))
    health = poll_once(cfg, 3000)
    assert health["api_ok"] is False
    assert "nope.service" in health["services"]


def test_write_health_atomic_roundtrip(tmp_path):
    import json

    path = str(tmp_path / "sub" / "supervisor.json")
    payload = {"supervisor": "running", "api_ok": True}
    write_health(path, payload)
    assert json.loads(open(path, encoding="utf-8").read()) == payload


def test_snapshot_services_no_health_is_all_unknown():
    services = snapshot_services(None)
    assert services == {
        "wavecam": "unknown", "gps_server": "unknown", "dashboard": "unknown",
        "cloudflared": "unknown", "supervisor": "unknown",
    }


def test_snapshot_services_maps_units_to_short_names():
    health = {
        "supervisor": "running",
        "services": {
            "wavecam.service": {"state": "active", "ok": True},
            "gps-server.service": {"state": "inactive", "ok": False},
        },
    }
    services = snapshot_services(health)
    assert services["wavecam"] == "active"
    assert services["gps_server"] == "inactive"
    assert services["dashboard"] == "unknown"  # absent from health -> unknown
    assert services["supervisor"] == "running"


def test_read_health_roundtrip(tmp_path):
    import json

    path = tmp_path / "supervisor.json"
    path.write_text(json.dumps({"supervisor": "running"}))
    assert read_health(str(path)) == {"supervisor": "running"}
    assert read_health(str(tmp_path / "nope.json")) is None
