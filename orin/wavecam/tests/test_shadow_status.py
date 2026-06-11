"""Verify that /status includes shadow_mode and /events passes shadow records."""
import types
import json
from fastapi.testclient import TestClient
from test_control_api import DummyPipeline
from wavecam.web import build_app
from wavecam.events import EventRing


def test_status_includes_shadow_mode_false_when_no_estimator():
    pipeline = DummyPipeline()
    pipeline.estimator = None
    client = TestClient(build_app(pipeline))
    body = client.get("/api/v1/status").json()
    assert "shadow_mode" in body
    assert body["shadow_mode"] is False


def test_status_includes_shadow_mode_true_when_estimator_active():
    pipeline = DummyPipeline()
    # Simulate an active shadow-mode estimator
    pipeline.estimator = types.SimpleNamespace()  # truthy
    pipeline._est_active_shadow = True
    client = TestClient(build_app(pipeline))
    body = client.get("/api/v1/status").json()
    assert body["shadow_mode"] is True


def test_events_includes_shadow_records():
    pipeline = DummyPipeline()
    pipeline.events = EventRing(maxlen=100)
    pipeline.events.record("shadow", {
        "t": 1000.0, "bearing_deg": 246.0, "dist_m": 200.0,
        "pan_enc_would": 8200, "tilt_enc_would": -10,
        "bearing_std_deg": 0.8, "owner_actual": "gps_tracker",
        "gps_updated": True, "vision_updated": False,
    })
    client = TestClient(build_app(pipeline))
    body = client.get("/api/v1/events").json()
    shadow = [e for e in body["events"] if e["kind"] == "shadow"]
    assert len(shadow) == 1
    assert shadow[0]["detail"]["bearing_deg"] == 246.0
