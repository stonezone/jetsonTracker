# tests/test_calibration_store.py
from __future__ import annotations

import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))
from wavecam.calibration_store import CalibrationStore


def test_reference_heading_survives_restart(tmp_path):
    p = str(tmp_path / "calibration.json")
    s = CalibrationStore.load(p)
    s.pose.calibrate_pan_aim(enc=100.0, bearing_deg=247.0, enc_per_deg=4.47)
    s.set_step("heading", {"heading_deg": 247.0})
    s.save()
    s2 = CalibrationStore.load(p)                  # simulated restart
    assert s2.pose.calibrated
    assert s2.reference_heading == 247.0           # was None after restart pre-fix
    assert s2.steps["heading"]["heading_deg"] == 247.0


def test_heading_capture_with_enc_none_persists_reference_heading(tmp_path, monkeypatch):
    """Regression: DummyPtz.inquire_pan_tilt returns None (VISCA timeout / test path).

    Before the fix, capture_calibration gated _store.save() on pose_changed, which
    required enc != None.  With enc=None the store's set_step() still set
    reference_heading IN MEMORY, but save() was never called — so a simulated
    restart (second adapter reading the same WAVECAM_POSE_PATH) saw
    reference_heading == None even though the heading capture succeeded.
    """
    calib_path = str(tmp_path / "calibration.json")
    monkeypatch.setenv("WAVECAM_POSE_PATH", calib_path)

    # Import here so the patched env var is picked up by ControlApiAdapter.__init__
    from test_control_api import DummyPipeline  # noqa: E402 — local import for env isolation
    from wavecam.web import build_app

    # --- First "session": drive the heading endpoint ---
    pipe1 = DummyPipeline()
    client1 = TestClient(build_app(pipe1))
    # DummyPtz.inquire_pan_tilt() returns None — the exact enc=None bug path
    resp = client1.post(
        "/api/v1/calibration/heading",
        json={
            "requested_owner": "manual",
            "takeover": True,
            "heading_deg": 247.0,
        },
    )
    assert resp.status_code == 200, f"Calibration POST failed: {resp.json()}"
    # In-memory reference_heading must be set regardless of enc
    assert client1.get("/api/v1/calibration").json()["calibration"]["reference_heading"] == 247.0

    # --- Simulated restart: second adapter reads the same path ---
    pipe2 = DummyPipeline()
    client2 = TestClient(build_app(pipe2))
    state_after_restart = client2.get("/api/v1/calibration").json()["calibration"]
    # This assertion failed before the fix (was None because save() was gated on pose_changed)
    assert state_after_restart["reference_heading"] == 247.0, (
        "reference_heading was lost across restart — save() was only called when enc != None"
    )


def test_load_migrates_legacy_pose_only_json(tmp_path):
    p = tmp_path / "calibration.json"
    p.write_text(json.dumps({"lat": 21.6, "lon": -158.0, "alt_m": 3.0,
                             "pan_anchor_enc": 0.0, "pan_anchor_bearing": 0.0,
                             "pan_enc_per_deg": 4.47, "tilt_anchor_enc": 0.0,
                             "tilt_anchor_elev": 0.0, "tilt_enc_per_deg": 0.0}))
    s = CalibrationStore.load(str(p))
    assert s.pose.has_base and s.pose.calibrated
