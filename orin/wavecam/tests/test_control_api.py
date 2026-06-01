from __future__ import annotations

import types

from fastapi.testclient import TestClient

from wavecam.ptz_owner import PtzOwner
from wavecam.ptz_visca import PAN_RIGHT, TILT_STOP
from wavecam.web import build_app


class DummyState:
    def __init__(self):
        self.show_mask = True
        self.status = {
            "state": "TRACKING",
            "conf": 0.72,
            "locked": True,
            "has_color": True,
            "has_person": False,
            "matched": False,
            "fps": 24.5,
            "connected": True,
            "ptz_enabled": True,
            "cmd": "p4/t1",
        }

    def get_status(self):
        return dict(self.status)

    def set_status(self, **kw):
        self.status.update(kw)

    def get_jpeg(self):
        return b"\xff\xd8\xff\xd9"


class DummyPtz:
    def __init__(self):
        self.calls = []

    def stop(self):
        self.calls.append(("stop",))

    def zoom(self, direction, speed=0):
        self.calls.append(("zoom", direction, speed))

    def pan_tilt(self, pan_speed, tilt_speed, pan_dir, tilt_dir):
        self.calls.append(("pan_tilt", pan_speed, tilt_speed, pan_dir, tilt_dir))


class DummyPipeline:
    def __init__(self):
        self.state = DummyState()
        self.owner = PtzOwner()
        self.ptz = DummyPtz()
        self.cfg = types.SimpleNamespace(
            ptz=types.SimpleNamespace(
                enabled=True,
                deadzone=0.08,
                max_pan_speed=10,
                max_tilt_speed=8,
                min_speed=1,
                invert_pan=False,
                invert_tilt=False,
            ),
            fusion=types.SimpleNamespace(lock_threshold=0.60, unlock_threshold=0.35),
            color=types.SimpleNamespace(min_area=60),
            web=types.SimpleNamespace(jpeg_quality=70),
        )

    def kill(self, on=True):
        self.state.set_status(killed=on, state=("KILLED" if on else "SEARCHING"))
        if on:
            self.owner.kill()
            self.ptz.stop()
            self.ptz.zoom("stop")
        else:
            self.owner.resume()
            self.owner.request("testbed")


def make_client():
    return TestClient(build_app(DummyPipeline()))


def test_api_v1_status_maps_legacy_state_to_release_contract():
    client = make_client()

    response = client.get("/api/v1/status")

    assert response.status_code == 200
    body = response.json()
    assert body["session"]["state"] == "TRACKING"
    assert body["safety"]["killed"] is False
    assert body["ptz"]["owner"] == "idle"
    assert body["ptz"]["enabled"] is True
    assert body["tracking"]["confidence"] == 0.72
    assert body["tracking"]["fps"] == 24.5
    assert isinstance(body["revision"], int)


def test_api_v1_safety_resume_does_not_restart_tracking_owner():
    client = make_client()
    pipe = client.app.state.pipeline

    killed = client.post("/api/v1/safety/kill", json={"reason": "test"}).json()
    assert killed["ok"] is True
    assert killed["status"]["safety"]["killed"] is True
    assert pipe.owner.owner == "idle"

    resumed = client.post("/api/v1/safety/resume", json={"source": "test"}).json()

    assert resumed["ok"] is True
    assert resumed["status"]["safety"]["killed"] is False
    assert resumed["status"]["ptz"]["owner"] == "idle"
    assert pipe.owner.owner == "idle"


def test_api_v1_ptz_velocity_is_owner_gated_and_normalized():
    client = make_client()
    pipe = client.app.state.pipeline

    response = client.post(
        "/api/v1/ptz/velocity",
        json={"requested_owner": "manual", "pan": 0.5, "tilt": 0.0, "zoom": 0.0},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["status"]["ptz"]["owner"] == "manual"
    assert ("pan_tilt", 5, 1, PAN_RIGHT, TILT_STOP) in pipe.ptz.calls

    client.post("/api/v1/safety/kill", json={"reason": "test"})
    blocked = client.post(
        "/api/v1/ptz/velocity",
        json={"requested_owner": "manual", "pan": 0.1, "tilt": 0.0, "zoom": 0.0},
    )

    assert blocked.status_code == 409
    assert blocked.json()["code"] == "killed"


def test_api_v1_ptz_velocity_accepts_zoom_only_manual_input():
    client = make_client()
    pipe = client.app.state.pipeline

    response = client.post(
        "/api/v1/ptz/velocity",
        json={"requested_owner": "manual", "pan": 0.0, "tilt": 0.0, "zoom": 0.5},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["status"]["ptz"]["owner"] == "manual"
    assert ("zoom", "tele", 4) in pipe.ptz.calls


def test_api_v1_config_hot_applies_known_keys_only():
    client = make_client()
    pipe = client.app.state.pipeline

    response = client.post(
        "/api/v1/config/hot",
        json={
            "patch": {
                "ptz.deadzone": 0.10,
                "ptz.max_pan_speed": 12,
                "fusion.lock_threshold": 0.70,
                "color.min_area": 120,
                "web.show_mask": False,
            }
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert pipe.cfg.ptz.deadzone == 0.10
    assert pipe.cfg.ptz.max_pan_speed == 12
    assert pipe.cfg.fusion.lock_threshold == 0.70
    assert pipe.cfg.color.min_area == 120
    assert pipe.state.show_mask is False

    refused = client.post("/api/v1/config/hot", json={"patch": {"camera.source": "rtsp://x"}})
    assert refused.status_code == 422
    assert refused.json()["code"] == "invalid_request"


if __name__ == "__main__":
    test_api_v1_status_maps_legacy_state_to_release_contract()
    test_api_v1_safety_resume_does_not_restart_tracking_owner()
    test_api_v1_ptz_velocity_is_owner_gated_and_normalized()
    test_api_v1_ptz_velocity_accepts_zoom_only_manual_input()
    test_api_v1_config_hot_applies_known_keys_only()
    print("CONTROL API TESTS PASSED")
