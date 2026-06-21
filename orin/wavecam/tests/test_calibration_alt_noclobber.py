"""Calibration v2 Task 3: an operator-set base altitude must survive a later GPS
base-lock. The map_manual location path sets pose.alt_manual=True; capture_calibration
('base_lock') then skips the GPS altitude write so the noisy ~13 m fix can't clobber
the surveyed beach height."""
import threading
from types import SimpleNamespace

from wavecam.camera_pose import CameraPose
from wavecam.control_calibration import CalibrationManager


class _FakeStore:
    def __init__(self):
        self.steps: dict = {}
        self.reference_heading = None
        self.updated_at_unix_ms = None
        self.fov_curve: list = []

    def set_step(self, step, values):
        self.steps[step] = values

    def save(self):
        pass


class _FakeGps:
    """Returns a noisy +13 m base altitude, as a real Hawaii GPS fix does on flat water."""
    def __init__(self, alt_m=13.0):
        self._alt = alt_m

    def get_camera_position(self):
        return (21.65, -158.05, self._alt)


def _manager(gps_alt=13.0) -> CalibrationManager:
    pipeline = SimpleNamespace(
        pose=CameraPose(lat=21.6, lon=-158.0, alt_m=0.0),
        gps=_FakeGps(gps_alt),
        ptz=None,
        owner=SimpleNamespace(owner="calibrate", killed=False),
    )
    api = SimpleNamespace(revision=0, status_snapshot=lambda: {})
    return CalibrationManager(_FakeStore(), pipeline, threading.RLock(), api)


def _manual_entry(alt_m):
    return {"method": "map_manual", "lat": 21.6, "lon": -158.0, "alt_m": alt_m,
            "error_radius_m": 5.0, "sample_count": 0, "model": "manual_radius",
            "source": "test", "captured_at_unix_ms": 0}


def _averaged_entry(alt_m):
    return {"method": "base_wio_average", "lat": 21.6, "lon": -158.0, "alt_m": alt_m,
            "error_radius_m": 5.0, "sample_count": 3,
            "model": "max(hdop*UERE,h_acc,min_radius), not sample_stddev",
            "source": "test", "captured_at_unix_ms": 0}


def test_manual_commit_sets_alt_manual_flag():
    m = _manager()
    m._commit_location(_manual_entry(2.0))
    assert m.pipeline.pose.alt_m == 2.0
    assert m.pipeline.pose.alt_manual is True


def test_averaged_commit_clears_alt_manual_flag():
    m = _manager()
    m._commit_location(_averaged_entry(7.0))
    assert m.pipeline.pose.alt_m == 7.0
    assert m.pipeline.pose.alt_manual is False


def test_base_lock_does_not_clobber_manual_alt():
    m = _manager(gps_alt=13.0)
    m._commit_location(_manual_entry(2.0))          # operator sets 2 m, flag True
    assert m.pipeline.pose.alt_manual is True
    m.capture_calibration("base_lock", {})          # GPS would supply 13 m
    assert m.pipeline.pose.alt_m == 2.0             # preserved
    assert m.pipeline.pose.lat == 21.65             # lat/lon still updated from GPS


def test_base_lock_writes_alt_when_not_manual():
    m = _manager(gps_alt=13.0)
    m.pipeline.pose.alt_manual = False
    m.capture_calibration("base_lock", {})
    assert m.pipeline.pose.alt_m == 13.0            # old behavior preserved when not manual
