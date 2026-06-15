"""Phase 1b wiring tests: camera_pose runtime base_locked flag + the pipeline
_update_base_drift observer. Pure-ish: drives the method directly with stubs.
"""
from __future__ import annotations
import dataclasses
import types

from wavecam.base_drift import BaseDriftMonitor
from wavecam.camera_pose import CameraPose
from wavecam.pipeline import Pipeline


# --- camera_pose runtime flag (not persisted) -------------------------------

def test_base_locked_defaults_true():
    assert CameraPose(lat=21.6, lon=-158.0).base_locked is True


def test_base_locked_is_not_persisted(tmp_path):
    pose = CameraPose(lat=21.6, lon=-158.0, alt_m=2.0)
    assert "base_locked" not in dataclasses.asdict(pose)
    pose.base_locked = False  # transient drift
    f = tmp_path / "pose.json"
    pose.save(str(f))
    loaded = CameraPose.load(str(f))
    assert loaded.base_locked is True  # resets to trusted on load


# --- pipeline _update_base_drift observer -----------------------------------

class _Gps:
    def __init__(self, cam, age=1.0):
        self._cam = cam
        self._age = age
    def get_camera_position(self):
        return self._cam
    def get_camera_age(self):
        return self._age


class _Events:
    def __init__(self):
        self.rec = []
    def record(self, kind, detail):
        self.rec.append((kind, detail))


def _pipe(enabled=True, cam=(21.6, -158.0, 2.0), interval=0.0,
          threshold=2.0, trend=1.0, mc=5, window=5):
    p = Pipeline.__new__(Pipeline)
    p.cfg = types.SimpleNamespace(gps=types.SimpleNamespace(base_drift_enabled=enabled))
    p.pose = CameraPose(lat=21.6, lon=-158.0, alt_m=2.0)
    p.gps = _Gps(cam)
    p.events = _Events()
    p._base_drift = BaseDriftMonitor(threshold_m=threshold, min_trend_m=trend,
                                     window_size=window, min_consecutive=mc)
    p._base_drift_interval_sec = interval
    p._base_drift_last_run = 0.0
    p._base_drift_latched_at = None
    p._base_drift_last_result = None
    return p


def test_disabled_restores_lock():
    p = _pipe(enabled=False)
    p.pose.base_locked = False  # pretend previously unlocked
    assert p._update_base_drift(now=10.0) is None
    assert p.pose.base_locked is True


def test_no_gps_leaves_lock():
    p = _pipe()
    p.gps = None
    assert p._update_base_drift(now=10.0) is None
    assert p.pose.base_locked is True


def test_stationary_stays_locked():
    p = _pipe(cam=(21.6, -158.0, 2.0))
    for t in range(6):
        p._update_base_drift(now=float(t))
    assert p.pose.base_locked is True
    assert not any(k == "base_drift" for k, _ in p.events.rec)


def test_sustained_drift_unlocks_and_records_event():
    p = _pipe(threshold=2.0, trend=1.0, mc=5, window=5)
    for i in range(5):
        p.gps = _Gps((21.6 + 0.00005 * (i + 1), -158.0, 2.0))
        p._update_base_drift(now=float(i))
    assert p.pose.base_locked is False
    assert any(k == "base_drift" for k, _ in p.events.rec)


def test_throttle_skips_within_interval():
    p = _pipe(interval=5.0)
    p._update_base_drift(now=10.0)  # 10-0 >= 5 -> runs
    assert p._base_drift_last_run == 10.0
    p._update_base_drift(now=12.0)  # 12-10 < 5 -> skipped
    assert p._base_drift_last_run == 10.0


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        try:
            fn()
        except TypeError:
            pass  # tmp_path fixture tests skipped in __main__
    print("BASE DRIFT WIRING TESTS PASSED")
