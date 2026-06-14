from __future__ import annotations

from pathlib import Path
import types
import time

from fastapi.testclient import TestClient

from wavecam.camera_pose import CameraPose
from wavecam.events import EventRing
from wavecam.health import HealthRegistry
from wavecam.ptz_owner import PtzOwner
from wavecam.control_api import map_axis
from wavecam.ptz_visca import PAN_RIGHT, TILT_DOWN, TILT_STOP, TILT_UP
from wavecam.web import build_app


class DummyState:
    def __init__(self):
        self.show_mask = True
        self.show_hud = True
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

    def inquire_pan_tilt(self):
        self.calls.append(("inquire_pan_tilt",))
        return None  # no encoder readback in tests — pose stays unmodified


class DummyRecorder:
    def __init__(self, rec_dir: Path | None = None):
        self.started_with = []
        self.stop_calls = 0
        self.config = types.SimpleNamespace(rec_dir=rec_dir or Path("/tmp/wavecam-test-recordings"))
        self.media = {
            "recording": False,
            "segment_name": None,
            "current_segment_name": None,
            "segment_pattern": None,
            "segment_prefix": None,
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
                "segment_name": None,
                "current_segment_name": None,
                "segment_pattern": "wavecam_20260601_120000_%03d.mp4",
                "segment_prefix": "wavecam_20260601_120000_",
                "segments": 0,
                "latest": [],
            }
        )
        return {
            "ok": True,
            "started": True,
            "segment_name": None,
            "segment_pattern": self.media["segment_pattern"],
            "segment_prefix": self.media["segment_prefix"],
        }

    def stop(self):
        self.stop_calls += 1
        self.media["recording"] = False
        self.media["segment_pattern"] = None
        self.media["segment_prefix"] = None
        return {"ok": True, "stopped": True}


class DummyPipeline:
    def __init__(self):
        self.state = DummyState()
        self.owner = PtzOwner()
        self.ptz = DummyPtz()
        self.recorder = DummyRecorder()
        self.zoom_suppressed = []
        self.restart_calls = []
        # P1: the adapter reads pose (calibration state) + gps (snapshot/reader health)
        self.pose = CameraPose()
        self.gps = None
        self.health = HealthRegistry()
        self.events = EventRing()
        self.arbiter = types.SimpleNamespace(lock_frames=5, grace_sec=1.0, reset_vision_state=lambda: None)
        self.ptz_state = types.SimpleNamespace(latest=lambda: (None, None))
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
                gps_boost=0.2,
                gps_boost_radius_frac=0.25,
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
            web=types.SimpleNamespace(jpeg_quality=70, show_hud=True),
            gps=types.SimpleNamespace(
                enabled=True,
                stale_threshold_sec=10.0,
                grace_sec=1.0,
                lock_frames=5,
                drive_zoom=False,
                max_pan_speed=4,
                max_tilt_speed=3,
            ),
            tracking=types.SimpleNamespace(mode="auto"),
            estimator=types.SimpleNamespace(
                shadow=True,
                enabled=True,
                q_accel=2.0,
                p0_pos=25.0,
                p0_vel=9.0,
                r_gps_fresh=4.0,
                r_gps_age_scale=0.5,
                r_vis_deg=1.0,
                zoom_cov_wide_deg=4.0,
                zoom_cov_narrow_deg=1.5,
                log_every_n=3,
            ),
            sensors=types.SimpleNamespace(
                enabled=False,
                drift_alert_deg=12.0,
            ),
        )

    def kill(self, on=True):
        self.state.set_status(killed=on, state=("KILLED" if on else "SEARCHING"))
        if on:
            self.owner.kill()
            self.ptz.stop()
            self.ptz.zoom("stop")
            self.events.record("kill", "killed")
        else:
            self.owner.resume()
            self.owner.request("testbed")
            self.events.record("kill", "resumed")

    def restart_service(self, unit):
        self.restart_calls.append(unit)

    def suppress_cinematic_zoom(self, seconds):
        self.zoom_suppressed.append(seconds)


def make_client():
    return TestClient(build_app(DummyPipeline()))


def test_root_web_ui_exposes_live_ptz_gps_and_ios_parity_controls():
    body = make_client().get("/").text

    assert "id=ptzJoystick" in body
    assert "/api/v1/status" in body
    assert "CINEMATIC ZOOM" in body
    assert "ptz.cinematic_zoom_enabled" in body
    assert "tracking.mode" in body
    assert "gps.drive_zoom" in body
    assert "target_sats" in body
    assert "target_battery_mv" in body


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
        "target_battery_mv": 3890,
        "target_sats": 11,
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
    assert gps["target_battery_mv"] == 3890
    assert gps["target_sats"] == 11


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
    # After the arbiter-reset fix, release no longer restores the saved
    # autonomous owner — the arbiter re-decides in the next frame.
    assert stopped.json()["status"]["ptz"]["owner"] == "idle"
    assert pipe.owner.owner == "idle"


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
    assert ("zoom", "wide", 5) in pipe.ptz.calls

    client.post("/api/v1/safety/kill", json={"reason": "test"})
    calls_after_kill = list(pipe.ptz.calls)
    killed = client.post("/api/v1/ptz/home", json={"takeover": True})

    assert killed.status_code == 409
    assert killed.json()["code"] == "killed"
    assert pipe.ptz.calls == calls_after_kill


def test_api_v1_calibration_captures_heading_tilt_zoom_state():
    client = make_client()
    pipe = client.app.state.pipeline
    pipe.owner.request("testbed")

    initial = client.get("/api/v1/calibration")

    assert initial.status_code == 200
    assert initial.json()["calibration"]["reference_heading"] is None

    heading = client.post(
        "/api/v1/calibration/heading",
        json={
            "requested_owner": "manual",
            "takeover": True,
            "heading_deg": 247.1,
            "source": "test",
            "note": "pier end",
        },
    )
    tilt = client.post(
        "/api/v1/calibration/tilt",
        json={
            "requested_owner": "manual",
            "tilt_deg": -2.5,
            "source": "test",
        },
    )
    zoom = client.post(
        "/api/v1/calibration/zoom",
        json={
            "requested_owner": "manual",
            "zoom_fov_deg": 31.5,
            "source": "test",
        },
    )

    assert heading.status_code == 200
    assert tilt.status_code == 200
    assert zoom.status_code == 200
    # Owner released back to idle after each capture (try/finally release_manual_owner)
    assert pipe.owner.owner == "idle"

    state = client.get("/api/v1/calibration").json()["calibration"]
    assert state["reference_heading"] == 247.1
    assert state["heading"]["heading_deg"] == 247.1
    assert state["heading"]["source"] == "test"
    assert state["heading"]["note"] == "pier end"
    assert state["tilt"]["tilt_deg"] == -2.5
    assert state["zoom"]["zoom_fov_deg"] == 31.5
    assert state["updated_at_unix_ms"] >= state["heading"]["captured_at_unix_ms"]

    config_state = client.get("/api/v1/config").json()
    assert config_state["current"]["calibration"]["reference_heading"] == 247.1


def test_heading_capture_stamps_measured_pan_scale():
    """The aim capture must stamp the hard-stop-measured scale (14.4 counts/deg),
    not the retired 4.47 folklore value that left every GPS slew ~3.2x short."""
    from wavecam.camera_pose import PRISUAL_PAN_ENC_PER_DEG
    assert abs(PRISUAL_PAN_ENC_PER_DEG - 14.4) < 0.01

    client = make_client()
    pipe = client.app.state.pipeline
    pipe.owner.request("testbed")
    pipe.ptz.inquire_pan_tilt = lambda: (1000.0, 0.0)   # encoder readback available

    r = client.post(
        "/api/v1/calibration/heading",
        json={"requested_owner": "manual", "takeover": True,
              "heading_deg": 90.0, "source": "test"},
    )
    assert r.status_code == 200
    assert abs(pipe.pose.pan_enc_per_deg - PRISUAL_PAN_ENC_PER_DEG) < 1e-9
    # +10 deg of bearing must move ~144 encoder counts, not ~45
    delta = pipe.pose.bearing_to_pan_encoder(100.0) - 1000.0
    assert abs(delta - 10.0 * PRISUAL_PAN_ENC_PER_DEG) < 0.5


def test_api_v1_calibration_is_owner_gated_kill_safe_and_validated():
    client = make_client()
    pipe = client.app.state.pipeline
    pipe.owner.request("testbed")

    busy = client.post("/api/v1/calibration/heading", json={"heading_deg": 90.0})

    assert busy.status_code == 409
    assert busy.json()["code"] == "owner_busy"

    invalid = client.post(
        "/api/v1/calibration/heading",
        json={"requested_owner": "manual", "takeover": True, "heading_deg": 361.0},
    )

    assert invalid.status_code == 422

    client.post("/api/v1/safety/kill", json={})
    killed = client.post(
        "/api/v1/calibration/heading",
        json={"requested_owner": "manual", "takeover": True, "heading_deg": 90.0},
    )

    assert killed.status_code == 409
    assert killed.json()["code"] == "killed"


def test_api_v1_calibrate_session_locks_ptz_and_requires_validation():
    client = make_client()
    pipe = client.app.state.pipeline
    assert pipe.owner.request("testbed") is True

    started = client.post(
        "/api/v1/calibration/session/start",
        json={"requested_owner": "manual", "takeover": True, "source": "test"},
    )

    assert started.status_code == 200
    assert pipe.owner.owner == "calibrate"
    assert started.json()["calibration"]["active"] is True
    assert started.json()["calibration"]["banner"] == "CALIBRATE ACTIVE"

    auto = client.post("/api/v1/ptz/auto", json={})
    manual = client.post(
        "/api/v1/ptz/velocity",
        json={"requested_owner": "manual", "takeover": True, "pan": 0.4},
    )

    assert auto.status_code == 409
    assert auto.json()["code"] == "calibrating"
    assert manual.status_code == 409
    assert manual.json()["code"] == "owner_busy"
    assert pipe.owner.owner == "calibrate"

    location = client.post(
        "/api/v1/calibration/location",
        json={
            "source": "test",
            "samples": [
                {"lat": 21.600000, "lon": -158.000000, "alt_m": 3.0,
                 "hdop": 1.2, "h_acc_m": 4.0, "fix_age_sec": 1.0,
                 "uptime_sec": 90.0, "sats": 9},
                {"lat": 21.600010, "lon": -158.000010, "alt_m": 3.2,
                 "hdop": 1.1, "h_acc_m": 4.5, "fix_age_sec": 1.0,
                 "uptime_sec": 95.0, "sats": 10},
            ],
        },
    )
    assert location.status_code == 200
    loc = location.json()["calibration"]["session"]["location"]
    assert loc["sample_count"] == 2
    assert loc["error_radius_m"] == 6.0
    assert "hdop*UERE" in loc["model"]
    assert pipe.pose.has_base is True

    level = client.post(
        "/api/v1/calibration/level",
        json={"roll_deg": 0.2, "pitch_deg": -0.1, "source": "test"},
    )
    assert level.status_code == 200

    preview_required = client.post(
        "/api/v1/calibration/heading-lock",
        json={"bearing_deg": 90.0, "distance_m": 250.0, "pan_enc": 1000.0},
    )
    assert preview_required.status_code == 409
    assert preview_required.json()["code"] == "operator_accept_required"

    heading = client.post(
        "/api/v1/calibration/heading-lock",
        json={
            "method": "landmark",
            "operator_accepted": True,
            "bearing_deg": 90.0,
            "distance_m": 250.0,
            "pan_enc": 1000.0,
            "vision_error_deg": 0.2,
            "latency_error_deg": 0.1,
            "source": "test",
        },
    )
    assert heading.status_code == 200
    heading_state = heading.json()["calibration"]["session"]["heading_lock"]
    assert heading_state["pan_enc_per_deg"] == 14.4
    assert heading_state["confidence"] > 0.0
    assert abs(pipe.pose.bearing_to_pan_encoder(91.0) - 1014.4) < 0.01

    validation = client.post(
        "/api/v1/calibration/validation",
        json={"bearing_deg": 91.0, "distance_m": 250.0, "pan_enc": 1014.4},
    )
    assert validation.status_code == 200
    assert validation.json()["calibration"]["session"]["validation"]["miss_deg"] == 0.0
    assert validation.json()["calibration"]["valid"] is False

    confirmed = client.post(
        "/api/v1/calibration/validation/confirm",
        json={"accepted": True, "source": "test"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["calibration"]["valid"] is True
    assert confirmed.json()["status"]["calibration"]["active"] is True

    exited = client.post(
        "/api/v1/calibration/session/exit",
        json={"confirm": True, "restore_prior": True, "source": "test"},
    )
    assert exited.status_code == 200
    assert exited.json()["calibration"]["active"] is False
    assert exited.json()["calibration"]["banner"] == "VALID"
    assert pipe.owner.owner == "testbed"


def test_api_v1_calibrate_heading_refuses_bad_error_budget():
    client = make_client()
    pipe = client.app.state.pipeline
    assert pipe.owner.request("testbed") is True

    assert client.post(
        "/api/v1/calibration/session/start",
        json={"requested_owner": "manual", "takeover": True},
    ).status_code == 200
    assert client.post(
        "/api/v1/calibration/location",
        json={
            "method": "manual_map_pin",
            "lat": 21.6,
            "lon": -158.0,
            "manual_error_radius_m": 15.0,
        },
    ).status_code == 200
    assert client.post(
        "/api/v1/calibration/level",
        json={"roll_deg": 0.0, "pitch_deg": 0.0},
    ).status_code == 200

    refused = client.post(
        "/api/v1/calibration/heading-lock",
        json={
            "method": "landmark",
            "operator_accepted": True,
            "bearing_deg": 90.0,
            "distance_m": 10.0,
            "pan_enc": 1000.0,
            "max_uncertainty_deg": 2.0,
        },
    )

    assert refused.status_code == 409
    body = refused.json()
    assert body["code"] == "uncertainty_too_high"
    assert body["uncertainty_deg"] > 2.0
    assert pipe.pose.calibrated is False
    assert pipe.owner.owner == "calibrate"


def test_api_v1_calibrate_kill_cancels_session():
    client = make_client()
    pipe = client.app.state.pipeline

    started = client.post(
        "/api/v1/calibration/session/start",
        json={"requested_owner": "manual", "takeover": True},
    )
    assert started.status_code == 200
    assert pipe.owner.owner == "calibrate"

    killed = client.post("/api/v1/safety/kill", json={"reason": "test"})

    assert killed.status_code == 200
    assert pipe.owner.owner == "idle"
    assert killed.json()["status"]["calibration"]["active"] is False
    assert killed.json()["status"]["calibration"]["valid"] is False


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
                "web.show_hud": False,
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
    assert pipe.state.show_hud is False

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
    assert body["current"]["web"]["show_hud"] is True
    assert body["current"]["color"]["preset"] == "orange_red"
    assert "blue" in body["supported"]["color_presets"]
    assert "ptz.cinematic_zoom_enabled" in body["hot_keys"]
    assert "ptz.zoom_target_frac" in body["hot_keys"]
    assert "web.show_hud" in body["hot_keys"]
    assert body["supported"]["calibration"] is True
    assert body["supported"]["cinematic_zoom"] is True
    assert body["supported"]["media"] is True
    assert body["supported"]["media_delete"] is True
    assert body["supported"]["ptz_home"] is True
    assert body["supported"]["presets"] is True
    assert body["supported"]["logs"] is True
    assert body["supported"]["show_hud"] is True
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


def test_api_v1_config_hot_rejects_inverted_fusion_hysteresis():
    client = make_client()
    pipe = client.app.state.pipeline
    lock_before = pipe.cfg.fusion.lock_threshold
    unlock_before = pipe.cfg.fusion.unlock_threshold
    before = client.get("/api/v1/config").json()

    # the 2026-06-11 field failure: both keys inverted in one patch
    refused = client.post(
        "/api/v1/config/hot",
        json={"patch": {"fusion.lock_threshold": 0.25, "fusion.unlock_threshold": 0.5}},
    )
    assert refused.status_code == 422
    assert refused.json()["code"] == "invalid_request"
    assert pipe.cfg.fusion.lock_threshold == lock_before
    assert pipe.cfg.fusion.unlock_threshold == unlock_before
    assert client.get("/api/v1/config").json()["revision"] == before["revision"]

    # single-key patches are checked against the live counterpart value
    refused = client.post(
        "/api/v1/config/hot",
        json={"patch": {"fusion.unlock_threshold": lock_before}},
    )
    assert refused.status_code == 422
    refused = client.post(
        "/api/v1/config/hot",
        json={"patch": {"fusion.lock_threshold": unlock_before}},
    )
    assert refused.status_code == 422

    # a valid pair still applies
    ok = client.post(
        "/api/v1/config/hot",
        json={"patch": {"fusion.lock_threshold": 0.7, "fusion.unlock_threshold": 0.4}},
    )
    assert ok.status_code == 200
    assert pipe.cfg.fusion.lock_threshold == 0.7
    assert pipe.cfg.fusion.unlock_threshold == 0.4


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


def test_api_v1_presets_list_save_apply_capture_and_delete_custom(tmp_path):
    pipe = DummyPipeline()
    pipe.preset_store_path = tmp_path / "presets.json"
    client = TestClient(build_app(pipe))

    listed = client.get("/api/v1/presets")

    assert listed.status_code == 200
    listed_body = listed.json()
    assert listed_body["ok"] is True
    assert isinstance(listed_body["request_id"], str)
    presets = {preset["name"]: preset for preset in listed_body["presets"]}
    assert presets["Default"]["builtin"] is True
    assert presets["Default"]["restart_required"] is False
    assert presets["Default"]["restart_keys"] == []
    assert presets["Tow Foil"]["values"]["ptz.max_pan_speed"] == 18
    for preset in listed_body["presets"]:
        assert isinstance(preset["name"], str)
        assert isinstance(preset["builtin"], bool)
        assert isinstance(preset["restart_required"], bool)
        assert isinstance(preset["restart_keys"], list)
        assert isinstance(preset["values"], dict)

    builtin_overwrite = client.post(
        "/api/v1/presets",
        json={"name": "Tow Foil", "values": {"ptz.deadzone": 0.12}},
    )
    assert builtin_overwrite.status_code == 409
    assert builtin_overwrite.json()["code"] == "builtin_preset"

    saved = client.post(
        "/api/v1/presets",
        json={
            "name": "WaterTest",
            "values": {
                "ptz.deadzone": 0.12,
                "detector.model": "/data/models/yolov8n.engine",
            },
        },
    )
    assert saved.status_code == 200
    assert saved.json()["preset"]["builtin"] is False

    applied = client.post("/api/v1/presets/WaterTest/apply")

    assert applied.status_code == 200
    body = applied.json()
    assert body["ok"] is True
    assert isinstance(body["request_id"], str)
    assert body["name"] == "WaterTest"
    assert body["applied"] == {"ptz.deadzone": 0.12}
    assert body["restart_required"] is True
    assert body["restart_keys"] == ["detector.model"]
    assert isinstance(body["status"], dict)
    assert pipe.cfg.ptz.deadzone == 0.12

    captured = client.post(
        "/api/v1/presets",
        json={"name": "Captured", "capture_current": True},
    )
    assert captured.status_code == 200
    assert captured.json()["preset"]["values"]["ptz.deadzone"] == 0.12

    deleted = client.delete("/api/v1/presets/WaterTest")
    builtin_delete = client.delete("/api/v1/presets/Default")

    assert deleted.status_code == 200
    assert builtin_delete.status_code == 409
    assert builtin_delete.json()["code"] == "builtin_preset"
    names_after_delete = {
        preset["name"] for preset in client.get("/api/v1/presets").json()["presets"]
    }
    assert "WaterTest" not in names_after_delete
    assert "Captured" in names_after_delete


def test_api_v1_presets_invalid_keys_do_not_mutate_or_persist(tmp_path):
    pipe = DummyPipeline()
    pipe.preset_store_path = tmp_path / "presets.json"
    client = TestClient(build_app(pipe))

    response = client.post(
        "/api/v1/presets",
        json={"name": "Bad", "values": {"camera.unknown": "rtsp://example"}},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert not pipe.preset_store_path.exists()


def test_api_v1_logs_scope_filter_and_redact_sensitive_values():
    pipe = DummyPipeline()
    pipe.log_lines = [
        {
            "ts_unix_ms": 1000,
            "level": "info",
            "source": "wavecam.service",
            "message": "started token=secret123 path=/Users/zack/.env",
        },
        {
            "ts_unix_ms": 1100,
            "level": "error",
            "source": "gps-server.service",
            "message": "must not leak",
        },
        {
            "ts_unix_ms": 1200,
            "level": "warning",
            "source": "supervisor",
            "message": "Authorization: Bearer abc123 from /home/zack/wavecam/.env",
        },
    ]
    client = TestClient(build_app(pipe))

    response = client.get("/api/v1/logs", params={"limit": 10, "since": 999})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert isinstance(body["request_id"], str)
    assert [line["source"] for line in body["lines"]] == ["wavecam.service", "supervisor"]
    for line in body["lines"]:
        assert isinstance(line["ts_unix_ms"], int)
        assert isinstance(line["level"], str)
        assert isinstance(line["source"], str)
        assert isinstance(line["message"], str)
    messages = "\n".join(line["message"] for line in body["lines"])
    assert "gps-server" not in messages
    assert "secret123" not in messages
    assert "abc123" not in messages
    assert "/Users/zack" not in messages
    assert "/home/zack" not in messages
    assert ".env" not in messages

    filtered = client.get("/api/v1/logs", params={"level": "warning", "limit": 10})
    assert filtered.status_code == 200
    assert [line["level"] for line in filtered.json()["lines"]] == ["warning"]


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
    assert body["segment_name"] is None
    assert body["current_segment_name"] is None
    assert body["segment_pattern"] == "wavecam_20260601_120000_%03d.mp4"
    assert body["segment_prefix"] == "wavecam_20260601_120000_"
    assert body["free_gb"] == 123.4


def test_api_v1_media_record_start_and_stop_control_recorder():
    client = make_client()
    pipe = client.app.state.pipeline

    started = client.post("/api/v1/media/record/start", json={"segment_seconds": 300})

    assert started.status_code == 200
    started_body = started.json()
    assert started_body["ok"] is True
    assert started_body["media"]["started"] is True
    assert started_body["media"]["segment_name"] is None
    assert started_body["media"]["segment_pattern"] == "wavecam_20260601_120000_%03d.mp4"
    assert started_body["status"]["media"]["recording"] is True
    assert started_body["status"]["media"]["segment_name"] is None
    assert pipe.recorder.started_with == [300]

    stopped = client.post("/api/v1/media/record/stop", json={})

    assert stopped.status_code == 200
    stopped_body = stopped.json()
    assert stopped_body["ok"] is True
    assert stopped_body["media"]["stopped"] is True
    assert stopped_body["status"]["media"]["recording"] is False


def test_api_v1_media_list_and_download_are_recorder_dir_scoped(tmp_path):
    client = make_client()
    pipe = client.app.state.pipeline
    rec_dir = tmp_path / "recordings"
    rec_dir.mkdir()
    pipe.recorder.config.rec_dir = rec_dir
    clip = rec_dir / "wavecam_20260604_000000_000.mp4"
    clip.write_bytes(b"mp4-bytes")
    (rec_dir / "nested").mkdir()

    listed = client.get("/api/v1/media/list")

    assert listed.status_code == 200
    listed_body = listed.json()
    assert listed_body["ok"] is True
    assert isinstance(listed_body["request_id"], str)
    assert isinstance(listed_body["status"], dict)
    assert listed_body["files"] == [
        {
            "name": clip.name,
            "size_bytes": 9,
            "ctime_unix_ms": listed_body["files"][0]["ctime_unix_ms"],
        }
    ]
    assert isinstance(listed_body["files"][0]["ctime_unix_ms"], int)

    downloaded = client.get(f"/api/v1/media/download/{clip.name}")

    assert downloaded.status_code == 200
    assert downloaded.content == b"mp4-bytes"
    assert downloaded.headers["content-type"].startswith("video/mp4")

    missing = client.get("/api/v1/media/download/nope.mp4")
    unsafe = client.get("/api/v1/media/download/%2E%2E%5Csecret.mp4")

    assert missing.status_code == 404
    assert missing.json()["code"] == "media_not_found"
    assert unsafe.status_code == 404
    assert unsafe.json()["code"] == "media_not_found"


def test_api_v1_media_delete_removes_only_recorder_dir_file(tmp_path):
    client = make_client()
    pipe = client.app.state.pipeline
    rec_dir = tmp_path / "recordings"
    rec_dir.mkdir()
    pipe.recorder.config.rec_dir = rec_dir
    clip = rec_dir / "wavecam_20260604_000000_000.mp4"
    clip.write_bytes(b"mp4-bytes")
    outside = tmp_path / "secret.mp4"
    outside.write_bytes(b"secret")

    deleted = client.delete(f"/api/v1/media/{clip.name}")
    missing = client.delete(f"/api/v1/media/{clip.name}")
    traversal = client.delete("/api/v1/media/%2E%2E/secret.mp4")
    backslash = client.delete("/api/v1/media/%2E%2E%5Csecret.mp4")

    assert deleted.status_code == 200
    deleted_body = deleted.json()
    assert deleted_body["ok"] is True
    assert deleted_body["name"] == clip.name
    assert deleted_body["freed_bytes"] == 9
    assert isinstance(deleted_body["request_id"], str)
    assert isinstance(deleted_body["status"], dict)
    assert not clip.exists()
    assert outside.read_bytes() == b"secret"
    assert missing.status_code == 404
    assert missing.json()["code"] == "media_not_found"
    assert traversal.status_code == 404
    assert backslash.status_code == 404
    assert backslash.json()["code"] == "media_not_found"


def test_guide_route_serves_html_and_assets(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    assets = docs / "guide_assets"
    assets.mkdir(parents=True)
    (docs / "WaveCam_Guide.html").write_text("<!doctype html><title>WaveCam</title>")
    (assets / "live.png").write_bytes(b"png-bytes")
    monkeypatch.setenv("WAVECAM_GUIDE_ROOT", str(docs))
    client = make_client()

    guide = client.get("/guide")
    asset = client.get("/guide_assets/live.png")

    assert guide.status_code == 200
    assert guide.headers["content-type"].startswith("text/html")
    assert b"WaveCam" in guide.content
    assert asset.status_code == 200
    assert asset.content == b"png-bytes"


def test_guide_route_missing_and_traversal_return_404(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    assets = docs / "guide_assets"
    assets.mkdir(parents=True)
    (tmp_path / "secret.txt").write_text("secret")
    monkeypatch.setenv("WAVECAM_GUIDE_ROOT", str(docs))
    client = make_client()

    missing_guide = client.get("/guide")
    traversal = client.get("/guide_assets/%2E%2E/secret.txt")

    assert missing_guide.status_code == 404
    assert missing_guide.json()["code"] == "guide_not_found"
    assert traversal.status_code == 404
    assert traversal.json()["code"] == "guide_asset_not_found"


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
    test_api_v1_calibration_captures_heading_tilt_zoom_state()
    test_api_v1_calibration_is_owner_gated_kill_safe_and_validated()
    test_api_v1_calibrate_session_locks_ptz_and_requires_validation()
    test_api_v1_calibrate_heading_refuses_bad_error_budget()
    test_api_v1_calibrate_kill_cancels_session()
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

    # GPS snapshot (P0: real distance/bearing/stale computation)
    test_gps_fix_snapshot_returns_none_when_no_fix()
    test_gps_fix_snapshot_computes_real_distance_and_bearing()
    test_gps_fix_snapshot_falls_back_when_no_camera_position()
    test_gps_fix_snapshot_marks_stale_when_target_age_exceeds_threshold()

    print("CONTROL API TESTS PASSED")



# --- GPS snapshot P0 ----------------------------------------------------------


def test_gps_fix_snapshot_returns_none_when_no_fix():
    from wavecam.control_api import gps_fix_snapshot
    assert gps_fix_snapshot(None) is None


def test_gps_fix_snapshot_computes_real_distance_and_bearing():
    from wavecam.control_api import gps_fix_snapshot
    from wavecam.gps_stub import NormalizedFix

    class FakeGps:
        def get_camera_position(self):
            return (22.0, -158.0, 0.0)

        def get_camera_age(self, now=None):
            return 2.0

    fix = NormalizedFix(lat=22.001, lon=-158.0, course=90.0, speed=5.0, ts=1000.0, age_sec=1.0, src="lora")
    gps = FakeGps()
    snap = gps_fix_snapshot(fix, gps)
    assert snap is not None
    assert snap["source"] == "lora"
    assert snap["target_age_sec"] == 1.0
    assert snap["base_age_sec"] == 2.0
    # ~111m north of (22.0, -158.0) at 1° lat ≈ 111km → ~111m
    assert 100 < snap["distance_m"] < 120
    assert -5 < snap["bearing_deg"] < 5           # due north ≈ 0°
    assert snap["stale"] is False                  # 1s age < 10s threshold


def test_gps_fix_snapshot_falls_back_when_no_camera_position():
    from wavecam.control_api import gps_fix_snapshot
    from wavecam.gps_stub import NormalizedFix

    class FakeGpsNoCam:
        def get_camera_position(self):
            return None

        def get_camera_age(self, now=None):
            return None

    fix = NormalizedFix(lat=22.0, lon=-158.0, course=45.0, speed=0.0, ts=1000.0, age_sec=3.0, src="lora")
    snap = gps_fix_snapshot(fix, FakeGpsNoCam())
    assert snap is not None
    assert snap["distance_m"] is None
    assert snap["bearing_deg"] is None              # null without camera position
    assert snap["base_age_sec"] is None
    assert snap["stale"] is False


def test_gps_fix_snapshot_marks_stale_when_target_age_exceeds_threshold():
    from wavecam.control_api import gps_fix_snapshot
    from wavecam.gps_stub import NormalizedFix

    class FakeGpsStale:
        def get_camera_position(self):
            return (22.0, -158.0, 0.0)

        def get_camera_age(self, now=None):
            return 12.0

    fix = NormalizedFix(lat=22.0, lon=-158.0, course=0.0, speed=0.0, ts=1000.0, age_sec=15.0, src="lora")
    snap = gps_fix_snapshot(fix, FakeGpsStale())
    assert snap is not None
    assert snap["stale"] is True                   # 15s > 10s threshold


def test_build_gps_includes_live_reader_target_telemetry():
    from wavecam.control_snapshots import build_gps
    from wavecam.gps_stub import NormalizedFix

    class FakeGps:
        def get_fix(self):
            return NormalizedFix(
                lat=22.001,
                lon=-158.0,
                course=90.0,
                speed=5.0,
                ts=1000.0,
                age_sec=1.0,
                src="direct_lora",
            )

        def get_camera_position(self):
            return (22.0, -158.0, 0.0)

        def get_camera_age(self, now=None):
            return 2.0

        def reader_alive(self):
            return True

        def last_poll_age_sec(self):
            return 0.2

        def get_target_telemetry(self):
            return {"target_battery_mv": 3910, "target_sats": 14}

    pipe = types.SimpleNamespace(
        cfg=types.SimpleNamespace(gps=types.SimpleNamespace(stale_threshold_sec=10.0)),
        gps=FakeGps(),
    )

    gps = build_gps(pipe, {})

    assert gps["source"] == "direct_lora"
    assert gps["target_battery_mv"] == 3910
    assert gps["target_sats"] == 14
    assert gps["reader_alive"] is True
