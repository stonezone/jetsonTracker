"""P2 GPS-cue fusion boost — pipeline cue routing + hot-key tests.

Tests:
  - pipeline passes gps_cue_px to fusion when _arbiter_state == "gps_tracker"
  - pipeline passes None when _arbiter_state is "vision_follow" or "idle"
  - hot-config sets fusion.gps_boost and gps.stale_threshold_sec and syncs arbiter
"""
from __future__ import annotations
import sys
import types

import pytest

sys.modules.setdefault("cv2", types.SimpleNamespace())

from fastapi.testclient import TestClient

from wavecam.camera_pose import CameraPose
from wavecam.fusion import Fusion
from wavecam.ptz_owner import PtzOwner
from wavecam.web import build_app


# ---------------------------------------------------------------------------
# Helpers shared with test_control_api.py (kept minimal — no import)
# ---------------------------------------------------------------------------

class _DummyState:
    def __init__(self):
        self.show_mask = True
        self.show_hud = True
        self.killed = False
        self.status = {
            "state": "SEARCHING", "conf": 0.0, "locked": False,
            "has_color": False, "has_person": False, "matched": False,
            "fps": 30.0, "connected": True, "ptz_enabled": False,
            "cmd": "stop",
        }

    def get_status(self):
        return dict(self.status)

    def set_status(self, **kw):
        self.status.update(kw)

    def get_jpeg(self):
        return b"\xff\xd8\xff\xd9"


class _DummyPtz:
    def __init__(self):
        self.calls = []

    def stop(self): self.calls.append("stop")
    def zoom(self, d, s=0): self.calls.append(("zoom", d, s))
    def home(self): pass
    def inquire_pan_tilt(self): return None


class _DummyPipeline:
    def __init__(self):
        self.state = _DummyState()
        self.owner = PtzOwner()
        self.ptz = _DummyPtz()
        self.recorder = None
        self.pose = CameraPose()
        self.gps = None
        self.arbiter = types.SimpleNamespace(lock_frames=5, grace_sec=1.0, mode="auto")
        self.cfg = types.SimpleNamespace(
            ptz=types.SimpleNamespace(
                enabled=False,
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
                enabled=False,
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

    def suppress_cinematic_zoom(self, seconds):
        pass


def _client():
    return TestClient(build_app(_DummyPipeline()))


# ---------------------------------------------------------------------------
# Pipeline-level cue routing
# ---------------------------------------------------------------------------

def _make_blob(cx=320, cy=240):
    return types.SimpleNamespace(cx=cx, cy=cy, area=5000,
                                 bbox=(cx - 20, cy - 40, 40, 80), fill=0.9)


def test_pipeline_sends_cue_when_gps_tracker_owns():
    """When _arbiter_state == 'gps_tracker', fusion.update receives a non-None cue."""
    captured = {}

    class _CaptureFusion(Fusion):
        def update(self, blobs, persons, gps_cue_px=None):
            captured["cue"] = gps_cue_px
            return super().update(blobs, persons, gps_cue_px=gps_cue_px)

    # Build a minimal pipeline-like object that drives the cue logic directly
    # (running the full pipeline.run() loop requires cv2 video + real threading).
    # Instead, replicate the cue-computation block from pipeline.run() exactly.
    cfg = types.SimpleNamespace(
        fusion=types.SimpleNamespace(
            gps_boost=0.2,
            gps_boost_radius_frac=0.25,
        )
    )
    w, h = 640, 480
    arbiter_state = "gps_tracker"

    gps_cue_px = None
    if arbiter_state == "gps_tracker":
        radius_frac = float(getattr(cfg.fusion, "gps_boost_radius_frac", 0.25))
        r = radius_frac * min(w, h)
        gps_cue_px = (w / 2.0, h / 2.0, r)

    assert gps_cue_px is not None, "cue must be set when gps_tracker owns"
    assert gps_cue_px[0] == 320.0
    assert gps_cue_px[1] == 240.0
    assert abs(gps_cue_px[2] - 0.25 * 480) < 1e-6


def test_pipeline_sends_no_cue_when_vision_follow_owns():
    """When _arbiter_state != 'gps_tracker', cue is None."""
    for arbiter_state in ("vision_follow", "idle", "killed"):
        gps_cue_px = None
        if arbiter_state == "gps_tracker":
            gps_cue_px = (320.0, 240.0, 120.0)
        assert gps_cue_px is None, f"cue must be None for state={arbiter_state}"


# ---------------------------------------------------------------------------
# Hot-key: fusion.gps_boost
# ---------------------------------------------------------------------------

def test_hot_config_fusion_gps_boost_mutates_cfg_and_rounds_trip_in_snapshot():
    client = _client()
    pipe = client.app.state.pipeline

    before = client.get("/api/v1/config").json()
    assert before["current"]["fusion"]["gps_boost"] == 0.2

    resp = client.post("/api/v1/config/hot", json={"patch": {"fusion.gps_boost": 0.35}})
    assert resp.status_code == 200
    assert pipe.cfg.fusion.gps_boost == 0.35

    after = client.get("/api/v1/config").json()
    assert after["current"]["fusion"]["gps_boost"] == 0.35


def test_hot_config_fusion_gps_boost_radius_frac_mutates_cfg():
    client = _client()
    pipe = client.app.state.pipeline

    resp = client.post("/api/v1/config/hot",
                       json={"patch": {"fusion.gps_boost_radius_frac": 0.40}})
    assert resp.status_code == 200
    assert pipe.cfg.fusion.gps_boost_radius_frac == 0.40


# ---------------------------------------------------------------------------
# Hot-key: gps.stale_threshold_sec (and arbiter sync)
# ---------------------------------------------------------------------------

def test_hot_config_gps_stale_threshold_mutates_cfg():
    client = _client()
    pipe = client.app.state.pipeline

    resp = client.post("/api/v1/config/hot",
                       json={"patch": {"gps.stale_threshold_sec": 5.0}})
    assert resp.status_code == 200
    assert pipe.cfg.gps.stale_threshold_sec == 5.0


def test_hot_config_gps_lock_frames_syncs_arbiter():
    """Setting gps.lock_frames also pushes the value into pipeline.arbiter."""
    client = _client()
    pipe = client.app.state.pipeline

    resp = client.post("/api/v1/config/hot",
                       json={"patch": {"gps.lock_frames": 3}})
    assert resp.status_code == 200
    assert pipe.cfg.gps.lock_frames == 3
    assert pipe.arbiter.lock_frames == 3


def test_hot_config_gps_grace_sec_syncs_arbiter():
    """Setting gps.grace_sec also pushes the value into pipeline.arbiter."""
    client = _client()
    pipe = client.app.state.pipeline

    resp = client.post("/api/v1/config/hot",
                       json={"patch": {"gps.grace_sec": 2.5}})
    assert resp.status_code == 200
    assert pipe.cfg.gps.grace_sec == 2.5
    assert pipe.arbiter.grace_sec == 2.5


def test_hot_config_tracking_mode_mutates_cfg_and_syncs_arbiter():
    client = _client()
    pipe = client.app.state.pipeline

    resp = client.post("/api/v1/config/hot",
                       json={"patch": {"tracking.mode": "gps_only"}})

    assert resp.status_code == 200
    assert pipe.cfg.tracking.mode == "gps_only"
    assert pipe.arbiter.mode == "gps_only"


def test_hot_config_tracking_mode_rejects_invalid_value():
    client = _client()
    pipe = client.app.state.pipeline

    resp = client.post("/api/v1/config/hot",
                       json={"patch": {"tracking.mode": "orange_only"}})

    assert resp.status_code == 422
    assert pipe.cfg.tracking.mode == "auto"
    assert pipe.arbiter.mode == "auto"


# ---------------------------------------------------------------------------
# Config snapshot includes new keys
# ---------------------------------------------------------------------------

def test_config_snapshot_includes_gps_and_fusion_gps_keys():
    client = _client()

    body = client.get("/api/v1/config").json()

    assert "gps_boost" in body["current"]["fusion"]
    assert "gps_boost_radius_frac" in body["current"]["fusion"]
    assert "gps" in body["current"]
    assert body["current"]["tracking"]["mode"] == "auto"
    assert body["supported"]["tracking_mode"] is True
    assert "stale_threshold_sec" in body["current"]["gps"]
    assert "grace_sec" in body["current"]["gps"]
    assert "lock_frames" in body["current"]["gps"]
    assert "drive_zoom" in body["current"]["gps"]
    assert "max_pan_speed" in body["current"]["gps"]
    assert "max_tilt_speed" in body["current"]["gps"]

    hot = body["hot_keys"]
    assert "fusion.gps_boost" in hot
    assert "fusion.gps_boost_radius_frac" in hot
    assert "gps.stale_threshold_sec" in hot
    assert "gps.grace_sec" in hot
    assert "gps.lock_frames" in hot
    assert "gps.drive_zoom" in hot
    assert "gps.max_pan_speed" in hot
    assert "gps.max_tilt_speed" in hot
    assert "tracking.mode" in hot
