"""Verify that /status.ptz carries encoder fields from PtzState."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import types
from fastapi.testclient import TestClient
from test_control_api import DummyPipeline
from wavecam.web import build_app
from wavecam.ptz_state import PtzState


def _client_with_enc(enc_value):
    """Build a test client whose pipeline.ptz_state returns enc_value."""
    pipeline = DummyPipeline()
    # Inject a PtzState-compatible stub
    pipeline.ptz_state = types.SimpleNamespace(
        latest=lambda: (enc_value, 0.05 if enc_value else None)
    )
    return TestClient(build_app(pipeline))


def test_enc_fields_null_when_no_data():
    client = _client_with_enc(None)
    r = client.get("/api/v1/status")
    assert r.status_code == 200
    ptz = r.json()["ptz"]
    assert ptz["pan_enc"] is None
    assert ptz["tilt_enc"] is None
    assert ptz["enc_age_sec"] is None


def test_enc_fields_populated_when_data_available():
    client = _client_with_enc((1234, -567))
    r = client.get("/api/v1/status")
    ptz = r.json()["ptz"]
    assert ptz["pan_enc"] == 1234
    assert ptz["tilt_enc"] == -567
    assert isinstance(ptz["enc_age_sec"], float)
    assert ptz["enc_age_sec"] >= 0.0


def test_existing_ptz_fields_unchanged():
    """Confirm additive-only — existing keys must still be present."""
    client = _client_with_enc(None)
    ptz = client.get("/api/v1/status").json()["ptz"]
    for key in ("owner", "enabled", "pan_tilt_cmd", "zoom_state"):
        assert key in ptz, f"existing field '{key}' must survive the change"
