"""Test isolation fixtures for the wavecam test suite."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_calibration_file(tmp_path, monkeypatch):
    """Route CalibrationStore away from the real camera_pose.json.

    Without this, tests that drive calibration endpoints via DummyPipeline
    (whose DummyPtz.inquire_pan_tilt returns None) would write the default
    orin/camera_pose.json even when enc=None prevented pose_changed from
    being set — the exact bug that Issue 1 fixes.  Isolating the path here
    means every test in the suite gets its own throwaway file automatically.
    """
    monkeypatch.setenv("WAVECAM_POSE_PATH", str(tmp_path / "calibration.json"))
