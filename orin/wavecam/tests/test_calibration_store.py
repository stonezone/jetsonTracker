# tests/test_calibration_store.py
from __future__ import annotations

import json
import os
import sys

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


def test_load_migrates_legacy_pose_only_json(tmp_path):
    p = tmp_path / "calibration.json"
    p.write_text(json.dumps({"lat": 21.6, "lon": -158.0, "alt_m": 3.0,
                             "pan_anchor_enc": 0.0, "pan_anchor_bearing": 0.0,
                             "pan_enc_per_deg": 4.47, "tilt_anchor_enc": 0.0,
                             "tilt_anchor_elev": 0.0, "tilt_enc_per_deg": 0.0}))
    s = CalibrationStore.load(str(p))
    assert s.pose.has_base and s.pose.calibrated
