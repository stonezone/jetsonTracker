"""Calibration v2 Task 5: the offset-calibrate handler. One physical aim at the
tracker's stable GPS re-anchors BOTH pan (calibrate_pan_aim) and tilt (all three
fields, incl. the scale — the M1/C2 fix), and reports the offset vs the coarse
step-3 heading plus a base-height sanity warning."""
import json
import math
import threading
from types import SimpleNamespace

from wavecam.camera_pose import (CameraPose, PRISUAL_PAN_ENC_PER_DEG,
                                 PRISUAL_TILT_ENC_PER_DEG)
from wavecam.control_calibration import CalibrationManager
from wavecam.ptz_owner import CALIBRATE


class _FakeStore:
    def __init__(self):
        self.steps: dict = {}
        self.reference_heading = None
        self.updated_at_unix_ms = None
        self.fov_curve: list = []

    def set_step(self, step, entry):
        self.steps[step] = {**entry}
        if step == "heading" and "heading_deg" in entry:
            self.reference_heading = entry["heading_deg"]

    def save(self):
        pass


class _FakePtz:
    def __init__(self, pan, tilt):
        self._e = (pan, tilt)

    def inquire_pan_tilt(self):
        return self._e


def _manager(pan=500.0, tilt=-40.0) -> CalibrationManager:
    pipeline = SimpleNamespace(
        pose=CameraPose(),
        gps=None,
        ptz=_FakePtz(pan, tilt),
        owner=SimpleNamespace(owner=CALIBRATE, killed=False),
    )
    api = SimpleNamespace(revision=0, status_snapshot=lambda: {})
    m = CalibrationManager(_FakeStore(), pipeline, threading.RLock(), api)
    m._session["active"] = True
    m._commit_location({"method": "map_manual", "lat": 21.6, "lon": -158.0, "alt_m": 2.0,
                        "error_radius_m": 5.0, "sample_count": 0, "model": "manual_radius",
                        "source": "test", "captured_at_unix_ms": 0})
    return m


def _json(resp):
    return json.loads(resp.body)


def test_offset_calibrate_reanchors_pan_and_tilt():
    m = _manager(pan=500.0, tilt=-40.0)
    resp = m.offset_calibrate({"operator_accepted": True, "target_lat": 21.60072,
                               "target_lon": -158.0, "step3_bearing_deg": 0.0})
    body = _json(resp)
    assert body["ok"] is True
    # pan fully re-anchored from the captured encoder + GPS bearing
    assert m.pipeline.pose.pan_enc_per_deg == PRISUAL_PAN_ENC_PER_DEG
    assert m.pipeline.pose.pan_anchor_enc == 500.0
    # tilt re-anchored with ALL THREE fields (the freeze-bug fix)
    assert m.pipeline.pose.tilt_anchor_enc == -40.0
    assert m.pipeline.pose.tilt_enc_per_deg == PRISUAL_TILT_ENC_PER_DEG
    expected_elev = math.degrees(math.atan2(1.0 - 2.0, body["distance_m"]))
    assert abs(m.pipeline.pose.tilt_anchor_elev - expected_elev) < 1e-3
    assert "offset_deg" in body and "elev_cal_deg" in body
    assert body["base_height_warning"] is False


def test_offset_calibrate_warns_on_bad_base_height():
    m = _manager()
    # absurd 200 m base height => |elev_cal| > 30 deg at > 50 m => warn
    m._commit_location({"method": "map_manual", "lat": 21.6, "lon": -158.0, "alt_m": 200.0,
                        "error_radius_m": 5.0, "sample_count": 0, "model": "manual_radius",
                        "source": "test", "captured_at_unix_ms": 0})
    resp = m.offset_calibrate({"operator_accepted": True, "target_lat": 21.6006,
                               "target_lon": -158.0})
    assert _json(resp)["base_height_warning"] is True


def test_offset_calibrate_requires_operator_accept():
    m = _manager()
    resp = m.offset_calibrate({"target_lat": 21.6006, "target_lon": -158.0})
    assert _json(resp)["ok"] is False
    assert _json(resp)["code"] == "operator_accept_required"


def test_offset_calibrate_requires_target():
    m = _manager()
    resp = m.offset_calibrate({"operator_accepted": True})
    assert _json(resp)["ok"] is False
    assert _json(resp)["code"] == "bearing_required"


class _FakeFix:
    def __init__(self, lat, lon):
        self.lat = lat; self.lon = lon


class _FakeGps:
    def __init__(self, lat, lon):
        self._fix = _FakeFix(lat, lon)

    def get_fix(self):
        return self._fix


def test_offset_calibrate_falls_back_to_live_tracker_fix():
    m = _manager(pan=500.0, tilt=-30.0)
    m.pipeline.gps = _FakeGps(21.60072, -158.0)        # backend's live tracker fix, ~80 m N
    resp = m.offset_calibrate({"operator_accepted": True})  # no target coords supplied
    body = _json(resp)
    assert body["ok"] is True
    assert m.pipeline.pose.tilt_enc_per_deg == PRISUAL_TILT_ENC_PER_DEG
    assert body["distance_m"] > 50
