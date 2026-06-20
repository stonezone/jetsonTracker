"""N5: the detector must fail fast with a clear error when an explicit engine/
weights file is missing, instead of an opaque Ultralytics crash deep in pipeline
construction (which presents as a zombie rig — API up, vision loop dead).

These target the pure preflight helper so they need neither ultralytics nor a real
engine file (and never trigger an auto-download).
"""
import pytest

from wavecam.detector import _check_model_path


def test_missing_engine_path_raises_clear_error():
    with pytest.raises(FileNotFoundError) as ei:
        _check_model_path("/data/projects/gimbal/models/does_not_exist.engine")
    assert "does_not_exist.engine" in str(ei.value)


def test_missing_explicit_weights_path_raises():
    # Any path with a separator is "explicit" and must exist.
    with pytest.raises(FileNotFoundError):
        _check_model_path("/some/dir/missing.pt")


def test_bare_weights_name_passes_preflight():
    # A bare name (no separator, not a .engine) is left to Ultralytics auto-download;
    # the preflight must not block it.
    _check_model_path("yolov8n.pt")  # no raise
