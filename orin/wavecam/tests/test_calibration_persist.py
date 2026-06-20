"""M2: a calibration save failure must be surfaced, not swallowed into an HTTP 200.

capture_calibration now returns whether the pose actually persisted to disk; the
calibration routes turn a False into a 503 refusal so the operator knows the lock
will NOT survive a restart (rather than believing a silently-volatile calibration).
"""
import threading
from types import SimpleNamespace

from wavecam.control_calibration import CalibrationManager


class _FakeStore:
    """Minimal CalibrationStore stand-in; save() can be made to fail."""

    def __init__(self, fail: bool):
        self._fail = fail
        self.steps: dict = {}
        self.saved = 0

    def set_step(self, step, values):
        self.steps[step] = values

    def save(self):
        if self._fail:
            raise OSError("simulated unwritable WAVECAM_POSE_PATH")
        self.saved += 1


def _manager(fail: bool) -> CalibrationManager:
    # step "zoom" skips every pose branch, so a bare pipeline namespace suffices.
    return CalibrationManager(
        _FakeStore(fail),
        SimpleNamespace(ptz=None, gps=None),
        threading.RLock(),
        None,
    )


def test_capture_returns_true_when_pose_persists():
    m = _manager(fail=False)
    assert m.capture_calibration("zoom", {"zoom_fov_deg": 30.0}) is True
    assert m._store.saved == 1


def test_capture_returns_false_when_save_fails():
    m = _manager(fail=True)
    assert m.capture_calibration("zoom", {"zoom_fov_deg": 30.0}) is False
