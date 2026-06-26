"""End-to-end: a FULL calibration (location+subject_alt_m → heading → offset → validate →
confirm) must yield calibration_valid AND, given a viable GPS chain, make the arbiter select
owner='gps_tracker'. Plus: the offset tilt anchor must use the operator's subject_alt_m, not a
hardcoded 1 m.

Guards the two classes that slipped past the per-step unit tests this session:
  - COR2: the calibrate steps + arbiter actually compose into a tracking decision.
  - TECH5: offset_calibrate anchors tilt at pose.subject_alt_m (≠ 1 m) not the old constant.
(Ownership transitions through the aim takeover are covered by the HTTP tests in
test_control_api.py: test_calibrate_aim_release_restores_calibrate_so_capture_continues etc.)"""
import json
import math
import threading
from types import SimpleNamespace

from wavecam.camera_pose import CameraPose
from wavecam.control_calibration import CalibrationManager
from wavecam.fusion import FusionResult
from wavecam.ptz_owner import CALIBRATE
from wavecam.tracking_arbiter import TrackingArbiter


class _Store:
    def __init__(self):
        self.steps = {}
        self.reference_heading = None
        self.updated_at_unix_ms = None
        self.fov_curve = []

    def set_step(self, step, entry):
        self.steps[step] = {**entry}
        if step == "heading" and "heading_deg" in entry:
            self.reference_heading = entry["heading_deg"]

    def save(self):
        pass


class _Ptz:
    def __init__(self, pan, tilt):
        self._e = (pan, tilt)

    def inquire_pan_tilt(self):
        return self._e


def _json(resp):
    return json.loads(resp.body)


def _vision(locked: bool) -> FusionResult:
    return FusionResult(target_xy=(0.5, 0.5), bbox=None, person_bbox=None,
                        conf=0.5, locked=locked,
                        state="TRACKING" if locked else "SEARCHING",
                        has_color=True, has_person=True, matched=locked)


def _full_calibration(subject_alt_m: float):
    """Drive a complete calibrate flow at the manager level; return (manager, distance_m)."""
    pipeline = SimpleNamespace(pose=CameraPose(), gps=None, ptz=_Ptz(500.0, -40.0),
                               owner=SimpleNamespace(owner=CALIBRATE, killed=False))
    api = SimpleNamespace(revision=0, status_snapshot=lambda: {})
    m = CalibrationManager(_Store(), pipeline, threading.RLock(), api)
    m._session["active"] = True
    # 1. location lock with the operator's subject (tracker) height
    m._commit_location({"method": "map_manual", "lat": 21.6, "lon": -158.0, "alt_m": 2.0,
                        "subject_alt_m": subject_alt_m, "error_radius_m": 5.0, "sample_count": 0,
                        "model": "manual_radius", "source": "test", "captured_at_unix_ms": 0})
    # 2. heading lock (operator-accepted, landmark)
    hr = m.heading_lock({"operator_accepted": True, "bearing_deg": 0.0, "distance_m": 80.0,
                         "pan_enc": 500.0, "method": "landmark", "source": "test"})
    assert _json(hr)["ok"] is True, _json(hr)
    # 3. offset aim at the tracker (~80 m due north)
    off = m.offset_calibrate({"operator_accepted": True, "target_lat": 21.60072,
                              "target_lon": -158.0, "step3_bearing_deg": 0.0, "source": "test"})
    assert _json(off)["ok"] is True, _json(off)
    dist = _json(off)["distance_m"]
    # 4. validation + 5. confirm
    val = m.validate_heading({"bearing_deg": 0.0, "distance_m": 80.0, "pan_enc": 500.0,
                              "source": "test"})
    assert _json(val)["ok"] is True, _json(val)
    cf = m.confirm_validation({"accepted": True, "source": "test"})
    assert _json(cf)["ok"] is True, _json(cf)
    return m, dist


def test_full_calibration_makes_arbiter_select_gps_tracker():
    m, _ = _full_calibration(subject_alt_m=1.0)
    # calibration_valid as the pipeline computes it: valid AND confirmed (pipeline.py:726)
    assert m._session.get("valid") is True
    assert m._session.get("confirmed") is True
    calibration_valid = bool(m._session["valid"]) and bool(m._session["confirmed"])
    assert m.pipeline.pose.calibrated is True

    # Given the viable chain post-calibration + no vision lock + idle, the arbiter must
    # hand the camera to GPS tracking — the end the whole calibrate flow exists to reach.
    arb = TrackingArbiter()
    d = arb.decide(_vision(False), gps_fresh=True,
                   gps_calibrated=m.pipeline.pose.calibrated, base_locked=True,
                   now_sec=0.0, calibration_valid=calibration_valid)
    assert d.owner == "gps_tracker"

    # And a missing calibration_valid still fails closed (no tracking).
    d2 = TrackingArbiter().decide(_vision(False), gps_fresh=True, gps_calibrated=True,
                                  base_locked=True, now_sec=0.0, calibration_valid=False)
    assert d2.owner == "idle"


def test_offset_anchor_uses_operator_subject_alt_not_hardcoded_one():
    # TECH5: offset must anchor tilt from pose.subject_alt_m, not a hardcoded 1 m. With a
    # tracker 0.5 m below a 2 m base, the anchor elevation is atan2(0.5-2, d), not atan2(1-2, d).
    m, dist = _full_calibration(subject_alt_m=0.5)
    expected = math.degrees(math.atan2(0.5 - 2.0, dist))
    assert abs(m.pipeline.pose.tilt_anchor_elev - expected) < 1e-3
    old_hardcoded = math.degrees(math.atan2(1.0 - 2.0, dist))
    assert abs(m.pipeline.pose.tilt_anchor_elev - old_hardcoded) > 1e-4
