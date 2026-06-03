from __future__ import annotations

import types
import time

from fastapi.testclient import TestClient

from wavecam.ptz_owner import PtzOwner
from wavecam.control_api import map_axis
from wavecam.ptz_visca import PAN_RIGHT, TILT_DOWN, TILT_STOP, TILT_UP
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

    def home(self):
        self.calls.append(("home",))


class DummyRecorder:
    def __init__(self):
        self.started_with = []
        self.stop_calls = 0
        self.media = {
            "recording": False,
            "segment_name": None,
            "free_gb": 123.4,
            "segments": 0,
            "latest": [],
        }

    def status(self):
        return dict(self.media)

    def start(self, segment_seconds=None):
        self.started_with.append(segment_seconds)
        self.media.update(
            {
                "recording": True,
                "segment_name": "wavecam_20260601_120000_%03d.mp4",
                "segments": 1,
                "latest": ["wavecam_20260601_120000_000.mp4"],
            }
        )
        return {"ok": True, "started": True, "segment_name": self.media["segment_name"]}

    def stop(self):
        self.stop_calls += 1
        self.media["recording"] = False
        return {"ok": True, "stopped": True}


class DummyPipeline:
    def __init__(self):
        self.state = DummyState()
        self.owner = PtzOwner()
        self.ptz = DummyPtz()
        self.recorder = DummyRecorder()
        self.zoom_suppressed = []
        self.restart_calls = []
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
            fusion=types.SimpleNamespace(
                lock_threshold=0.60,
                unlock_threshold=0.35,
                require_person=False,
                match_dist=120,
                person_aim_x=0.5,
                person_aim_y=0.5,
            ),
            color=types.SimpleNamespace(
                enabled=True,
                preset="orange_red",
                min_area=60,
                max_area=200000,
                hsv_ranges={},
                morph_kernel=5,
            ),
            detector=types.SimpleNamespace(
                enabled=True,
                conf=0.35,
                imgsz=640,
                person_class=0,
                every_n=3,
                box_ttl_sec=0.6,
            ),
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

    def restart_service(self, unit):
        self.restart_calls.append(unit)

    def suppress_cinematic_zoom(self, seconds):
        self.zoom_suppressed.append(seconds)


def make_client():
    return TestClient(build_app(DummyPipeline()))


def wait_until(predicate, timeout_sec=0.5):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_api_v1_status_maps_legacy_state_to_release_contract():
    client = make_client()

    response = client.get("/api/v1/status")

    assert response.status_code == 200
    body = response.json()
    assert body["session"]["state"] == "TRACKING"
    assert body["session"]["mode"] == "vision"
    assert body["safety"]["killed"] is False
    assert body["ptz"]["owner"] == "idle"
    assert body["ptz"]["enabled"] is True
    assert body["tracking"]["confidence"] == 0.72
    assert body["tracking"]["fps"] == 24.5
    assert body["media"]["recording"] is False
    assert body["media"]["free_gb"] == 123.4
    assert isinstance(body["revision"], int)


def test_api_v1_status_reports_pipeline_gps_snapshot_when_available():
    client = make_client()
    pipe = client.app.state.pipeline
    pipe.gps_status = lambda: {
        "source": "watch",
        "target_age_sec": 0.8,
        "base_age_sec": 5.2,
        "distance_m": 184.2,
        "bearing_deg": 247.1,
        "stale": False,
    }

    response = client.get("/api/v1/status")

    assert response.status_code == 200
    gps = response.json()["gps"]
    assert gps["source"] == "watch"
    assert gps["target_age_sec"] == 0.8
    assert gps["base_age_sec"] == 5.2
    assert gps["distance_m"] == 184.2
    assert gps["bearing_deg"] == 247.1
    assert gps["stale"] is False


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


def test_api_v1_safety_kill_cancels_manual_deadman_before_resume():
    client = make_client()
    pipe = client.app.state.pipeline

    moving = client.post(
        "/api/v1/ptz/velocity",
        json={
            "requested_owner": "manual",
            "pan": 0.5,
            "tilt": 0.0,
            "deadman_ms": 100,
        },
    )
    assert moving.status_code == 200
    assert pipe.owner.owner == "manual"

    killed = client.post("/api/v1/safety/kill", json={"reason": "deadman_cancel"})
    assert killed.status_code == 200
    resumed = client.post("/api/v1/safety/resume", json={"source": "test"})
    assert resumed.status_code == 200

    calls_after_resume = list(pipe.ptz.calls)
    time.sleep(0.16)

    assert pipe.owner.owner == "idle"
    assert pipe.owner.killed is False
    assert pipe.ptz.calls == calls_after_resume


def test_api_v1_safety_kill_stops_active_recording():
    client = make_client()
    pipe = client.app.state.pipeline
    pipe.recorder.start()

    killed = client.post("/api/v1/safety/kill", json={"reason": "test"}).json()

    assert killed["ok"] is True
    assert pipe.recorder.stop_calls == 1
    assert killed["status"]["media"]["recording"] is False


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


def test_manual_tilt_axis_uses_joystick_semantics():
    cfg = types.SimpleNamespace(
        invert_tilt=False,
        max_tilt_speed=8,
        min_speed=1,
        deadzone=0.05,
    )

    up_dir, up_speed = map_axis(0.5, cfg, "tilt")
    down_dir, down_speed = map_axis(-0.5, cfg, "tilt")

    assert (up_dir, up_speed) == (TILT_UP, 4)
    assert (down_dir, down_speed) == (TILT_DOWN, 4)


def test_manual_tilt_axis_can_be_physically_inverted():
    cfg = types.SimpleNamespace(
        invert_tilt=True,
        max_tilt_speed=8,
        min_speed=1,
        deadzone=0.05,
    )

    up_dir, up_speed = map_axis(0.5, cfg, "tilt")
    down_dir, down_speed = map_axis(-0.5, cfg, "tilt")

    assert (up_dir, up_speed) == (TILT_DOWN, 4)
    assert (down_dir, down_speed) == (TILT_UP, 4)


def test_api_v1_ptz_velocity_requires_takeover_to_preempt_autonomous_owner():
    client = make_client()
    pipe = client.app.state.pipeline
    assert pipe.owner.request("testbed") is True

    blocked = client.post(
        "/api/v1/ptz/velocity",
        json={"requested_owner": "manual", "pan": 0.5, "tilt": 0.0},
    )

    assert blocked.status_code == 409
    assert blocked.json()["code"] == "owner_busy"
    assert pipe.owner.owner == "testbed"

    response = client.post(
        "/api/v1/ptz/velocity",
        json={"requested_owner": "manual", "takeover": True, "pan": 0.5, "tilt": 0.0},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["status"]["ptz"]["owner"] == "manual"
    assert pipe.owner.owner == "manual"
    assert ("stop",) in pipe.ptz.calls
    assert ("zoom", "stop", 0) in pipe.ptz.calls
    assert ("pan_tilt", 5, 1, PAN_RIGHT, TILT_STOP) in pipe.ptz.calls


def test_api_v1_ptz_stop_restores_autonomous_owner_after_takeover():
    client = make_client()
    pipe = client.app.state.pipeline
    assert pipe.owner.request("testbed") is True

    response = client.post(
        "/api/v1/ptz/velocity",
        json={"requested_owner": "manual", "takeover": True, "pan": 0.5, "tilt": 0.0},
    )
    assert response.status_code == 200
    assert pipe.owner.owner == "manual"

    stopped = client.post("/api/v1/ptz/stop", json={"hold": False, "source": "ios_native"})

    assert stopped.status_code == 200
    assert stopped.json()["status"]["ptz"]["owner"] == "testbed"
    assert pipe.owner.owner == "testbed"


def test_api_v1_ptz_stop_holds_manual_owner_to_block_autonomous_owner():
    client = make_client()
    pipe = client.app.state.pipeline
    assert pipe.owner.request("testbed") is True

    response = client.post("/api/v1/ptz/stop", json={"source": "ios_native"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["status"]["ptz"]["owner"] == "manual"
    assert pipe.owner.owner == "manual"
    assert ("stop",) in pipe.ptz.calls
    assert ("zoom", "stop", 0) in pipe.ptz.calls


def test_api_v1_ptz_stop_release_mode_releases_manual_owner():
    client = make_client()
    pipe = client.app.state.pipeline
    assert pipe.owner.request("manual") is True

    response = client.post("/api/v1/ptz/stop", json={"hold": False, "source": "ios_native"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["status"]["ptz"]["owner"] == "idle"
    assert pipe.owner.owner == "idle"
    assert ("stop",) in pipe.ptz.calls
    assert ("zoom", "stop", 0) in pipe.ptz.calls


def test_api_v1_ptz_auto_starts_tracking_owner_from_manual_hold():
    client = make_client()
    pipe = client.app.state.pipeline
    assert pipe.owner.request("manual") is True

    response = client.post("/api/v1/ptz/auto", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["status"]["ptz"]["owner"] == "testbed"
    assert body["status"]["session"]["state"] == "SEARCHING"
    assert pipe.owner.owner == "testbed"
    assert ("stop",) in pipe.ptz.calls
    assert ("zoom", "stop", 0) in pipe.ptz.calls


def test_api_v1_ptz_auto_refuses_while_killed():
    client = make_client()
    pipe = client.app.state.pipeline

    client.post("/api/v1/safety/kill", json={"reason": "test"})
    response = client.post("/api/v1/ptz/auto", json={})

    assert response.status_code == 409
    assert response.json()["code"] == "killed"
    assert pipe.owner.owner == "idle"


def test_api_v1_ptz_home_is_owner_gated_and_kill_respecting():
    client = make_client()
    pipe = client.app.state.pipeline
    assert pipe.owner.request("testbed") is True

    blocked = client.post("/api/v1/ptz/home", json={})

    assert blocked.status_code == 409
    assert blocked.json()["code"] == "owner_busy"
    assert ("home",) not in pipe.ptz.calls
    assert pipe.owner.owner == "testbed"

    takeover = client.post(
        "/api/v1/ptz/home",
        json={"requested_owner": "manual", "takeover": True, "source": "ios_native"},
    )

    assert takeover.status_code == 200
    assert takeover.json()["ok"] is True
    assert takeover.json()["status"]["ptz"]["owner"] == "manual"
    assert ("stop",) in pipe.ptz.calls
    assert ("zoom", "stop", 0) in pipe.ptz.calls
    assert ("home",) in pipe.ptz.calls

    client.post("/api/v1/safety/kill", json={"reason": "test"})
    calls_after_kill = list(pipe.ptz.calls)
    killed = client.post("/api/v1/ptz/home", json={"takeover": True})

    assert killed.status_code == 409
    assert killed.json()["code"] == "killed"
    assert pipe.ptz.calls == calls_after_kill


def test_api_v1_ptz_zoom_endpoint_is_owner_gated():
    client = make_client()
    pipe = client.app.state.pipeline

    response = client.post(
        "/api/v1/ptz/zoom",
        json={"requested_owner": "manual", "mode": "velocity", "value": -0.5},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["status"]["ptz"]["owner"] == "manual"
    assert ("zoom", "wide", 4) in pipe.ptz.calls

    pipe.owner.release("manual")
    assert pipe.owner.request("testbed") is True
    override = client.post(
        "/api/v1/ptz/zoom",
        json={"requested_owner": "manual", "mode": "velocity", "value": 0.5},
    )

    assert override.status_code == 200
    assert override.json()["status"]["ptz"]["owner"] == "testbed"
    assert pipe.owner.owner == "testbed"
    assert ("zoom", "tele", 4) in pipe.ptz.calls
    assert pipe.zoom_suppressed

    takeover = client.post(
        "/api/v1/ptz/zoom",
        json={"requested_owner": "manual", "takeover": True, "mode": "velocity", "value": 0.5},
    )

    assert takeover.status_code == 200
    assert takeover.json()["status"]["ptz"]["owner"] == "testbed"
    assert ("zoom", "tele", 4) in pipe.ptz.calls


def test_api_v1_ptz_zoom_under_autonomous_owner_deadman_stops_zoom():
    client = make_client()
    pipe = client.app.state.pipeline
    assert pipe.owner.request("testbed") is True

    response = client.post(
        "/api/v1/ptz/zoom",
        json={
            "requested_owner": "manual",
            "mode": "velocity",
            "value": 0.5,
            "deadman_ms": 100,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"]["ptz"]["owner"] == "testbed"
    assert ("zoom", "tele", 4) in pipe.ptz.calls
    time.sleep(0.16)
    assert ("zoom", "stop", 0) in pipe.ptz.calls
    assert pipe.owner.owner == "testbed"


def test_api_v1_zoom_stop_does_not_release_manual_owner_while_pan_tilt_active():
    client = make_client()
    pipe = client.app.state.pipeline

    moving = client.post(
        "/api/v1/ptz/velocity",
        json={
            "requested_owner": "manual",
            "pan": 0.5,
            "tilt": 0.0,
            "zoom": 0.0,
            "deadman_ms": 250,
        },
    )
    assert moving.status_code == 200
    assert pipe.owner.owner == "manual"

    zoom_stop = client.post(
        "/api/v1/ptz/zoom",
        json={"requested_owner": "manual", "mode": "velocity", "value": 0.0},
    )

    assert zoom_stop.status_code == 200
    assert zoom_stop.json()["status"]["ptz"]["owner"] == "manual"
    assert pipe.owner.owner == "manual"
    assert ("zoom", "stop", 0) in pipe.ptz.calls


def test_api_v1_ptz_zoom_refuses_while_killed():
    client = make_client()

    client.post("/api/v1/safety/kill", json={"reason": "test"})
    response = client.post(
        "/api/v1/ptz/zoom",
        json={"requested_owner": "manual", "mode": "velocity", "value": 0.5},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "killed"


def test_legacy_resume_does_not_autostart_tracking_owner():
    client = make_client()
    pipe = client.app.state.pipeline

    client.post("/kill", json={})
    response = client.post("/resume", json={})

    assert response.status_code == 200
    assert response.json()["killed"] is False
    assert pipe.owner.killed is False
    assert pipe.owner.owner == "idle"


def test_legacy_zoom_routes_use_owner_gate_and_deadman():
    client = make_client()
    pipe = client.app.state.pipeline
    assert pipe.owner.request("gps_tracker") is True

    response = client.post("/ptz/zin", json={})

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert pipe.owner.owner == "gps_tracker"
    assert ("zoom", "tele", 4) in pipe.ptz.calls
    assert pipe.zoom_suppressed


def test_stale_manual_deadman_generation_cannot_release_newer_owner():
    client = make_client()
    pipe = client.app.state.pipeline
    api = client.app.state.control_api
    assert pipe.owner.request("manual") is True

    old_generation = api.schedule_manual_deadman(250)
    api.cancel_manual_deadman()
    pipe.owner.release("manual")
    assert pipe.owner.request("manual") is True
    new_generation = api.schedule_manual_deadman(250)

    api.manual_deadman_expired(old_generation)

    assert new_generation != old_generation
    assert pipe.owner.owner == "manual"


def test_api_v1_config_hot_applies_known_keys_only():
    client = make_client()
    pipe = client.app.state.pipeline

    response = client.post(
        "/api/v1/config/hot",
        json={
            "patch": {
                "ptz.deadzone": 0.10,
                "ptz.max_pan_speed": 12,
                "ptz.cinematic_zoom_enabled": True,
                "ptz.zoom_target_frac": 0.45,
                "ptz.zoom_deadband": 0.08,
                "ptz.zoom_max_speed": 4,
                "fusion.lock_threshold": 0.70,
                "fusion.require_person": True,
                "fusion.match_dist": 80,
                "fusion.person_aim_y": 0.25,
                "color.min_area": 120,
                "color.preset": "blue",
                "detector.conf": 0.55,
                "detector.every_n": 2,
                "web.show_mask": False,
            }
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert pipe.cfg.ptz.deadzone == 0.10
    assert pipe.cfg.ptz.max_pan_speed == 12
    assert pipe.cfg.ptz.cinematic_zoom_enabled is True
    assert pipe.cfg.ptz.zoom_target_frac == 0.45
    assert pipe.cfg.ptz.zoom_deadband == 0.08
    assert pipe.cfg.ptz.zoom_max_speed == 4
    assert pipe.cfg.fusion.lock_threshold == 0.70
    assert pipe.cfg.fusion.require_person is True
    assert pipe.cfg.fusion.match_dist == 80
    assert pipe.cfg.fusion.person_aim_y == 0.25
    assert pipe.cfg.color.min_area == 120
    assert pipe.cfg.color.preset == "blue"
    assert "blue_low" in pipe.cfg.color.hsv_ranges
    assert pipe.cfg.detector.conf == 0.55
    assert pipe.cfg.detector.every_n == 2
    assert pipe.state.show_mask is False

    refused = client.post("/api/v1/config/hot", json={"patch": {"camera.source": "rtsp://x"}})
    assert refused.status_code == 422
    assert refused.json()["code"] == "invalid_request"

    bad_preset = client.post("/api/v1/config/hot", json={"patch": {"color.preset": "ultraviolet"}})
    assert bad_preset.status_code == 422
    assert bad_preset.json()["code"] == "invalid_request"


def test_api_v1_config_reports_supported_tuning_surface():
    client = make_client()

    response = client.get("/api/v1/config")

    assert response.status_code == 200
    body = response.json()
    assert body["current"]["ptz"]["max_pan_speed"] == 10
    assert body["current"]["ptz"]["cinematic_zoom_enabled"] is False
    assert body["current"]["ptz"]["zoom_target_frac"] == 0.5
    assert body["current"]["color"]["preset"] == "orange_red"
    assert "blue" in body["supported"]["color_presets"]
    assert "ptz.cinematic_zoom_enabled" in body["hot_keys"]
    assert "ptz.zoom_target_frac" in body["hot_keys"]
    assert body["supported"]["ptz_home"] is True
    assert "detector.conf" in body["hot_keys"]
    assert "detector.model" in body["restart_required_keys"]


def test_api_v1_config_hot_invalid_batch_does_not_mutate_or_bump_revision():
    client = make_client()

    before = client.get("/api/v1/config").json()
    refused = client.post(
        "/api/v1/config/hot",
        json={
            "patch": {
                "ptz.deadzone": 0.11,
                "camera.source": "rtsp://x",
            }
        },
    )
    after = client.get("/api/v1/config").json()

    assert refused.status_code == 422
    assert refused.json()["code"] == "invalid_request"
    assert after["revision"] == before["revision"]
    assert after["current"]["ptz"]["deadzone"] == before["current"]["ptz"]["deadzone"]


def test_api_v1_config_hot_rejects_stale_revision_without_mutating():
    client = make_client()

    before = client.get("/api/v1/config").json()
    response = client.post(
        "/api/v1/config/hot",
        json={
            "revision": before["revision"] + 10,
            "patch": {"ptz.deadzone": 0.11},
        },
    )
    after = client.get("/api/v1/config").json()

    assert response.status_code == 409
    assert response.json()["code"] == "revision_conflict"
    assert after["revision"] == before["revision"]
    assert after["current"]["ptz"]["deadzone"] == before["current"]["ptz"]["deadzone"]


def test_api_v1_config_hot_rejects_persist_without_mutating():
    client = make_client()

    before = client.get("/api/v1/config").json()
    response = client.post(
        "/api/v1/config/hot",
        json={
            "persist": True,
            "patch": {"ptz.deadzone": 0.11},
        },
    )
    after = client.get("/api/v1/config").json()

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert after["revision"] == before["revision"]
    assert after["current"]["ptz"]["deadzone"] == before["current"]["ptz"]["deadzone"]


def test_api_v1_cinematic_zoom_hot_config_round_trips_in_snapshot():
    client = make_client()

    before = client.get("/api/v1/config").json()
    applied = client.post(
        "/api/v1/config/hot",
        json={
            "patch": {
                "ptz.cinematic_zoom_enabled": True,
                "ptz.zoom_target_frac": 0.44,
                "ptz.zoom_deadband": 0.09,
                "ptz.zoom_max_speed": 3,
            }
        },
    )
    after = client.get("/api/v1/config").json()

    assert applied.status_code == 200
    assert applied.json()["ok"] is True
    assert after["revision"] == before["revision"] + 1
    assert after["current"]["ptz"]["cinematic_zoom_enabled"] is True
    assert after["current"]["ptz"]["zoom_target_frac"] == 0.44
    assert after["current"]["ptz"]["zoom_deadband"] == 0.09
    assert after["current"]["ptz"]["zoom_max_speed"] == 3


def test_api_v1_system_restart_schedules_restart_when_idle():
    client = make_client()
    pipe = client.app.state.pipeline

    response = client.post(
        "/api/v1/system/restart",
        json={"reason": "test", "delay_seconds": 0.0},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["ok"] is True
    assert body["action"] == "restart"
    assert body["unit"] == "wavecam.service"
    assert body["status"]["session"]["state"] == "RESTARTING"
    assert ("stop",) in pipe.ptz.calls
    assert ("zoom", "stop", 0) in pipe.ptz.calls
    assert wait_until(lambda: pipe.restart_calls == ["wavecam.service"])


def test_api_v1_system_restart_requires_confirmation_while_auto_ptz_active():
    client = make_client()
    pipe = client.app.state.pipeline
    assert pipe.owner.request("testbed") is True

    blocked = client.post("/api/v1/system/restart", json={"reason": "test"})

    assert blocked.status_code == 409
    assert blocked.json()["code"] == "restart_confirmation_required"
    assert pipe.restart_calls == []

    confirmed = client.post(
        "/api/v1/system/restart",
        json={"reason": "confirmed", "confirm_moving": True, "delay_seconds": 0.0},
    )

    assert confirmed.status_code == 202
    assert confirmed.json()["ok"] is True
    assert confirmed.json()["status"]["ptz"]["owner"] == "idle"
    assert wait_until(lambda: pipe.restart_calls == ["wavecam.service"])


def test_api_v1_system_restart_refuses_duplicate_pending_request():
    client = make_client()

    first = client.post(
        "/api/v1/system/restart",
        json={"reason": "first", "delay_seconds": 0.25},
    )
    second = client.post(
        "/api/v1/system/restart",
        json={"reason": "second", "delay_seconds": 0.25},
    )

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["code"] == "restart_pending"


def test_api_v1_agent_summon_accepts_request_without_moving_camera():
    client = make_client()
    pipe = client.app.state.pipeline

    response = client.post(
        "/api/v1/agent/summon",
        json={"source": "ios_native", "reason": "operator_diagnostics"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["ok"] is True
    assert body["action"] == "agent_summon"
    assert body["accepted"] is True
    assert body["source"] == "ios_native"
    assert body["reason"] == "operator_diagnostics"
    assert body["status"]["ptz"]["owner"] == "idle"
    assert pipe.ptz.calls == []


def test_api_v1_media_status_reports_recorder_state():
    client = make_client()
    pipe = client.app.state.pipeline
    pipe.recorder.start(segment_seconds=300)

    response = client.get("/api/v1/media/status")

    assert response.status_code == 200
    body = response.json()
    assert body["recording"] is True
    assert body["segment_name"] == "wavecam_20260601_120000_%03d.mp4"
    assert body["free_gb"] == 123.4


def test_api_v1_media_record_start_and_stop_control_recorder():
    client = make_client()
    pipe = client.app.state.pipeline

    started = client.post("/api/v1/media/record/start", json={"segment_seconds": 300})

    assert started.status_code == 200
    started_body = started.json()
    assert started_body["ok"] is True
    assert started_body["media"]["started"] is True
    assert started_body["status"]["media"]["recording"] is True
    assert pipe.recorder.started_with == [300]

    stopped = client.post("/api/v1/media/record/stop", json={})

    assert stopped.status_code == 200
    stopped_body = stopped.json()
    assert stopped_body["ok"] is True
    assert stopped_body["media"]["stopped"] is True
    assert stopped_body["status"]["media"]["recording"] is False


if __name__ == "__main__":
    test_api_v1_status_maps_legacy_state_to_release_contract()
    test_api_v1_status_reports_pipeline_gps_snapshot_when_available()
    test_api_v1_safety_resume_does_not_restart_tracking_owner()
    test_api_v1_safety_kill_cancels_manual_deadman_before_resume()
    test_api_v1_safety_kill_stops_active_recording()
    test_api_v1_ptz_velocity_is_owner_gated_and_normalized()
    test_api_v1_ptz_velocity_accepts_zoom_only_manual_input()
    test_manual_tilt_axis_uses_joystick_semantics()
    test_manual_tilt_axis_can_be_physically_inverted()
    test_api_v1_ptz_velocity_requires_takeover_to_preempt_autonomous_owner()
    test_api_v1_ptz_stop_restores_autonomous_owner_after_takeover()
    test_api_v1_ptz_stop_holds_manual_owner_to_block_autonomous_owner()
    test_api_v1_ptz_stop_release_mode_releases_manual_owner()
    test_api_v1_ptz_auto_starts_tracking_owner_from_manual_hold()
    test_api_v1_ptz_auto_refuses_while_killed()
    test_api_v1_ptz_home_is_owner_gated_and_kill_respecting()
    test_api_v1_ptz_zoom_endpoint_is_owner_gated()
    test_api_v1_zoom_stop_does_not_release_manual_owner_while_pan_tilt_active()
    test_api_v1_ptz_zoom_refuses_while_killed()
    test_legacy_resume_does_not_autostart_tracking_owner()
    test_legacy_zoom_routes_use_owner_gate_and_deadman()
    test_stale_manual_deadman_generation_cannot_release_newer_owner()
    test_api_v1_config_hot_applies_known_keys_only()
    test_api_v1_config_reports_supported_tuning_surface()
    test_api_v1_cinematic_zoom_hot_config_round_trips_in_snapshot()
    test_api_v1_system_restart_schedules_restart_when_idle()
    test_api_v1_system_restart_requires_confirmation_while_auto_ptz_active()
    test_api_v1_system_restart_refuses_duplicate_pending_request()
    test_api_v1_agent_summon_accepts_request_without_moving_camera()
    test_api_v1_media_status_reports_recorder_state()
    test_api_v1_media_record_start_and_stop_control_recorder()
    print("CONTROL API TESTS PASSED")
