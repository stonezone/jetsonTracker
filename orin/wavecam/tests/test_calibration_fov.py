"""Tests for FOV curve storage and the /calibration/fov endpoint."""
import json
from fastapi.testclient import TestClient
from test_control_api import DummyPipeline
from wavecam.web import build_app
from wavecam.calibration_store import CalibrationStore


def test_calibration_store_fov_curve_defaults_empty(tmp_path):
    store = CalibrationStore.load(str(tmp_path / "cal.json"))
    assert store.fov_curve == []


def test_calibration_store_fov_curve_round_trips(tmp_path):
    p = str(tmp_path / "cal.json")
    store = CalibrationStore.load(p)
    store.fov_curve = [(0, 60.0), (8192, 12.0), (16384, 5.0)]
    store.save()
    store2 = CalibrationStore.load(p)
    assert store2.fov_curve == [(0, 60.0), (8192, 12.0), (16384, 5.0)]


def test_fov_endpoint_returns_stored_curve():
    pipeline = DummyPipeline()
    client = TestClient(build_app(pipeline))
    # _store is written back by the adapter; mutate it post-build
    pipeline._store.fov_curve = [(0, 60.0), (8192, 12.0), (16384, 5.0)]
    r = client.get("/api/v1/calibration/fov")
    assert r.status_code == 200
    body = r.json()
    assert body["fov_entries"] == [[0, 60.0], [8192, 12.0], [16384, 5.0]]


def test_fov_endpoint_post_adds_entry():
    pipeline = DummyPipeline()
    client = TestClient(build_app(pipeline))
    r = client.post("/api/v1/calibration/fov", json={"zoom_enc": 8192, "fov_deg": 12.0})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # Verify it round-trips back
    body = client.get("/api/v1/calibration/fov").json()
    assert any(e[0] == 8192 and abs(e[1] - 12.0) < 0.01 for e in body["fov_entries"])


def test_fov_endpoint_post_rejects_invalid_fov():
    pipeline = DummyPipeline()
    client = TestClient(build_app(pipeline))
    r = client.post("/api/v1/calibration/fov", json={"zoom_enc": 0, "fov_deg": 0.0})
    assert r.status_code == 422 or r.json().get("ok") is False


def test_calibration_state_includes_fov_entries():
    """GET /calibration must include fov_entries in the calibration sub-object for iOS."""
    pipeline = DummyPipeline()
    client = TestClient(build_app(pipeline))
    # _store is written back by the adapter; mutate it post-build
    pipeline._store.fov_curve = [(0, 60.0)]
    body = client.get("/api/v1/calibration").json()
    # fov_entries lives in body["calibration"], which is the iOS feature-detection path
    assert "fov_entries" in body.get("calibration", {})


def test_fov_endpoint_returns_503_on_save_failure():
    # CAL-1: a persist failure must surface as 503, not ok:true — otherwise the entry is
    # lost on restart while the operator saw success (the M2 pattern on the FOV path).
    pipeline = DummyPipeline()
    client = TestClient(build_app(pipeline))

    def boom():
        raise OSError("disk full")

    pipeline._store.save = boom
    r = client.post("/api/v1/calibration/fov", json={"zoom_enc": 8192, "fov_deg": 12.0})
    assert r.status_code == 503
    assert r.json()["ok"] is False
