"""Wave 2 (audit 2026-07-01): M10, M11, M12 — CalibrationManager fixes.

M10: calibration_state()'s "base_locked" key used to mean "has a base position"
(pose.lat/lon != 0), NOT the actual BaseDriftMonitor lock the pipeline gates GPS
authority on (pose.base_locked). A confirmed tripod-drift unlock left the API
still reporting "locked" while pointing was silently withheld. Fix: has_base and
base_locked are now separate, honest fields.

M11: missing per-sample altitude was coerced via `or 0.0` and averaged in
unconditionally — a legitimate "no altitude reported" sample dragged the locked
base altitude toward sea level. Fix: average only samples that reported alt_m;
fall back to 0.0 only when none did.

M12: the heading-uncertainty model was selected by "'phone' in method or
phone_acc is not None" — any request incidentally carrying heading_acc_deg
skipped the distance-geometry error model regardless of method. Fix: branch on
method only.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

from wavecam.calibration_store import CalibrationStore
from wavecam.camera_pose import CameraPose
from wavecam.control_calibration import CalibrationManager


_SCRATCH_STORE_PATH = (
    "/tmp/claude-0/-home-user-jetsonTracker/3e8a975f-443b-56e4-a304-508c2b86a02e/"
    "scratchpad/test_audit_wave2_calibration_store.json"
)


def _manager(pose: CameraPose | None = None) -> CalibrationManager:
    store = CalibrationStore(path=_SCRATCH_STORE_PATH)
    pipeline = SimpleNamespace(
        pose=pose if pose is not None else CameraPose(),
        owner=SimpleNamespace(owner="idle", killed=False),
        gps=None,
        ptz=None,
        ptz_state=None,
    )
    api = SimpleNamespace(revision=0, status_snapshot=lambda: {})
    return CalibrationManager(store, pipeline, threading.RLock(), api)


# --- M10: has_base vs base_locked are separate, honest fields ---------------

def test_calibration_state_reports_has_base_and_base_locked_separately():
    pose = CameraPose(lat=21.6, lon=-158.0, alt_m=2.0)
    pose.base_locked = False  # confirmed tripod-drift unlock (BaseDriftMonitor)
    mgr = _manager(pose)
    state = mgr.calibration_state()
    assert state["has_base"] is True
    assert state["base_locked"] is False  # must NOT just mirror has_base


def test_calibration_state_base_locked_true_when_no_base_position():
    # Runtime default: base_locked starts True even with no base position yet —
    # calibration_state() must report the pose's honest flag either way.
    mgr = _manager(CameraPose())
    state = mgr.calibration_state()
    assert state["has_base"] is False
    assert state["base_locked"] is True


def test_calibration_state_base_locked_true_when_base_and_not_drifted():
    pose = CameraPose(lat=21.6, lon=-158.0, alt_m=2.0)
    assert pose.base_locked is True  # not drifted
    mgr = _manager(pose)
    state = mgr.calibration_state()
    assert state["has_base"] is True
    assert state["base_locked"] is True


# --- M11: altitude averaging skips samples without alt_m --------------------

def test_accepted_location_samples_keeps_missing_altitude_as_none():
    mgr = _manager()
    req = SimpleNamespace(samples=[
        {"lat": 21.6, "lon": -158.0},          # no alt_m at all
        {"lat": 21.6001, "lon": -158.0001, "alt_m": 4.0},
    ])
    accepted, rejected = mgr._accepted_location_samples(req)
    assert rejected == 0
    assert len(accepted) == 2
    assert accepted[0]["alt_m"] is None
    assert accepted[1]["alt_m"] == 4.0


def test_lock_location_averages_altitude_over_reporting_samples_only():
    """A camera genuinely at 6m must not be dragged toward 0 by samples that
    simply omitted alt_m (M11's headline bug: `or 0.0` coerced missing into a
    measurement)."""
    mgr = _manager()
    mgr._session["active"] = True
    mgr.pipeline.owner.owner = "calibrate"
    req = SimpleNamespace(
        method="base_wio_average",
        lat=None, lon=None,
        samples=[
            {"lat": 21.6000, "lon": -158.0000, "alt_m": 6.0},
            {"lat": 21.6000, "lon": -158.0000},        # no altitude reported
            {"lat": 21.6000, "lon": -158.0000, "alt_m": 6.0},
        ],
        use_live_base=False,
        offset_north_m=None, offset_east_m=None, offset_up_m=None,
        source=None,
    )
    resp = mgr.lock_location(req)
    body = resp.body.decode() if hasattr(resp, "body") else None
    assert body is not None
    import json
    payload = json.loads(body)
    assert payload["ok"] is True
    # Only the two samples that reported 6.0 feed the average -> 6.0, not 4.0
    # (which is what (6+0+6)/3 would have given under the old `or 0.0` coercion).
    assert mgr.pipeline.pose.alt_m == 6.0


def test_lock_location_falls_back_to_zero_when_no_sample_reports_altitude():
    mgr = _manager()
    mgr._session["active"] = True
    mgr.pipeline.owner.owner = "calibrate"
    req = SimpleNamespace(
        method="base_wio_average",
        lat=None, lon=None,
        samples=[
            {"lat": 21.6000, "lon": -158.0000},
            {"lat": 21.6000, "lon": -158.0000},
        ],
        use_live_base=False,
        offset_north_m=None, offset_east_m=None, offset_up_m=None,
        source=None,
    )
    resp = mgr.lock_location(req)
    import json
    payload = json.loads(resp.body.decode())
    assert payload["ok"] is True
    assert mgr.pipeline.pose.alt_m == 0.0


# --- M12: heading-uncertainty model is selected by method only -------------

def test_heading_uncertainty_uses_geometry_model_when_method_is_not_phone():
    """A non-phone method that happens to carry heading_acc_deg must NOT get
    routed into the lenient phone-compass model (the M12 bug)."""
    mgr = _manager()
    location = {"lat": 21.6, "lon": -158.0, "error_radius_m": 5.0}
    req = SimpleNamespace(
        method="landmark_bearing",
        heading_acc_deg=1.5,   # incidentally present; must be ignored by non-phone method
        base_error_radius_m=None,
        remote_error_radius_m=None,
        lever_arm_error_m=None,
        vision_error_deg=None,
        latency_error_deg=None,
        tilt_error_deg=None,
        position_error_deg=None,
    )
    # Close range (10m) -> the documented ~27 deg geometry hazard must show up,
    # NOT the small phone-compass-derived uncertainty (~1.5 deg quadrature).
    uncertainty = mgr._estimate_heading_uncertainty(req, location, distance_m=10.0)
    assert uncertainty > 10.0, (
        f"expected the distance-geometry model (~27 deg at 10m) to apply, got {uncertainty}"
    )


def test_heading_uncertainty_uses_phone_model_only_for_phone_method():
    mgr = _manager()
    location = {"lat": 21.6, "lon": -158.0, "error_radius_m": 5.0}
    req = SimpleNamespace(
        method="phone_compass",
        heading_acc_deg=1.5,
        vision_error_deg=None,
        latency_error_deg=None,
    )
    uncertainty = mgr._estimate_heading_uncertainty(req, location, distance_m=10.0)
    # quadrature([1.5, 0.5, 0.2]) ~= 1.6 deg — small, phone-model territory.
    assert uncertainty < 3.0
