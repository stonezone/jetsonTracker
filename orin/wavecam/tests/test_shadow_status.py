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


def test_maybe_init_estimator_picks_up_late_store(tmp_path):
    """Regression: run() starts before ControlApiAdapter wires pipeline._store
    (control_api.py:587), so a start-time-only G2 check never fires on the rig.
    _maybe_init_estimator must no-op cleanly without the store, then succeed
    when re-invoked after the store (or a mid-session calibration) appears."""
    from wavecam.pipeline import Pipeline
    calls = []
    p = types.SimpleNamespace(
        cfg=types.SimpleNamespace(
            estimator=types.SimpleNamespace(enabled=True, shadow=True),
            shadow_log_dir=str(tmp_path)),
        estimator=None,
        _shadow_writer=None,
    )
    p._init_estimator = lambda fov: (calls.append(fov), setattr(p, "estimator", object()))

    Pipeline._maybe_init_estimator(p)            # store not wired yet -> no-op
    assert p.estimator is None and calls == []

    p._store = types.SimpleNamespace(fov_curve=[(0, 63.7)])
    Pipeline._maybe_init_estimator(p)            # store arrived -> init fires
    assert calls == [[(0, 63.7)]]
    assert p.estimator is not None
    assert p._shadow_writer is not None

    Pipeline._maybe_init_estimator(p)            # idempotent once active
    assert len(calls) == 1


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
