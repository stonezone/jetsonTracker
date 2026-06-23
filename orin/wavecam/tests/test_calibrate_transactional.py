"""Transactional calibration (field bug 2026-06-23): a bail exit (confirm:false) must restore
the pre-session pose AND camera_pose.json byte-for-byte; a commit exit (confirm:true) keeps the
new calibration. Before this, every wizard step wrote through to the live pose + persisted, so a
partial/abandoned calibration destroyed the previous good calibration and the menu couldn't be
left without a passing validation."""
import copy
import threading
from types import SimpleNamespace

from wavecam.calibration_store import CalibrationStore
from wavecam.camera_pose import CameraPose
from wavecam.control_calibration import CalibrationManager
from wavecam.ptz_owner import PtzOwner


def _prior_pose() -> CameraPose:
    """A previously-committed, fully-calibrated pose we must not lose."""
    return CameraPose(lat=21.5, lon=-158.1, alt_m=3.0, subject_alt_m=0.5,
                      pan_anchor_enc=100.0, pan_anchor_bearing=290.0, pan_enc_per_deg=14.4,
                      tilt_anchor_enc=-50.0, tilt_anchor_elev=1.0, tilt_enc_per_deg=14.4)


def _make_manager(store: CalibrationStore):
    owner = PtzOwner()  # starts IDLE → start_session claims CALIBRATE
    ptz = SimpleNamespace(stop=lambda: None, zoom=lambda *_a: None,
                          inquire_pan_tilt=lambda: (store.pose.pan_anchor_enc,
                                                    store.pose.tilt_anchor_enc))
    state = SimpleNamespace(set_status=lambda **_k: None)
    pipeline = SimpleNamespace(pose=store.pose, owner=owner, ptz=ptz, state=state, gps=None)
    api = SimpleNamespace(revision=0, status_snapshot=lambda: {})
    return CalibrationManager(store, pipeline, threading.RLock(), api), pipeline


def _mutating_location() -> dict:
    return {"method": "map_manual", "lat": 1.0, "lon": 2.0, "alt_m": 99.0,
            "subject_alt_m": 5.0, "model": "manual_radius", "source": "test",
            "captured_at_unix_ms": 0}


def test_bail_exit_restores_in_memory_pose(tmp_path):
    store = CalibrationStore(path=str(tmp_path / "camera_pose.json"), pose=_prior_pose())
    store.reference_heading = 290.0
    m, pipeline = _make_manager(store)
    before = copy.deepcopy(pipeline.pose)

    m.start_session({"requested_owner": "manual"})
    m._commit_location(_mutating_location())
    assert pipeline.pose.lat == 1.0, "sanity: a step mutates the live pose mid-session"

    m.exit_session({"confirm": False})  # bail / discard

    for f in CameraPose.__dataclass_fields__:
        assert getattr(pipeline.pose, f) == getattr(before, f), f"{f} not restored on bail"


def test_bail_exit_restores_camera_pose_json_byte_for_byte(tmp_path):
    path = str(tmp_path / "camera_pose.json")
    store = CalibrationStore(path=path, pose=_prior_pose())
    store.reference_heading = 290.0
    store.set_step("heading", {"heading_deg": 290.0, "bearing_deg": 290.0})
    store.save()  # establish the prior good file on disk
    with open(path, "rb") as f:
        before_bytes = f.read()

    m, _ = _make_manager(store)
    m.start_session({"requested_owner": "manual"})
    m._commit_location(_mutating_location())  # write-through persists → file changes
    with open(path, "rb") as f:
        assert f.read() != before_bytes, "sanity: the file changes mid-session"

    m.exit_session({"confirm": False})  # bail → restore + re-persist
    with open(path, "rb") as f:
        assert f.read() == before_bytes, "camera_pose.json must be byte-identical after a bail"


def test_commit_exit_keeps_new_calibration(tmp_path):
    """Guard the other side: confirm:true must NOT roll back — the new values commit."""
    store = CalibrationStore(path=str(tmp_path / "camera_pose.json"), pose=_prior_pose())
    m, pipeline = _make_manager(store)

    m.start_session({"requested_owner": "manual"})
    m._commit_location(_mutating_location())
    m._session["valid"] = True  # stand in for a passed validation (exit confirm:true gate)

    m.exit_session({"confirm": True})  # commit

    assert pipeline.pose.lat == 1.0, "committed values must survive a confirm exit"
    assert pipeline.pose.alt_m == 99.0


def test_cancel_session_restores_pose(tmp_path):
    """A KILL / cancel mid-calibration is un-confirmed → it must also roll back, like a bail."""
    store = CalibrationStore(path=str(tmp_path / "camera_pose.json"), pose=_prior_pose())
    m, pipeline = _make_manager(store)
    before = copy.deepcopy(pipeline.pose)

    m.start_session({"requested_owner": "manual"})
    m._commit_location(_mutating_location())
    assert pipeline.pose.lat == 1.0

    m.cancel_session("killed")

    for f in CameraPose.__dataclass_fields__:
        assert getattr(pipeline.pose, f) == getattr(before, f), f"{f} not restored on cancel"
