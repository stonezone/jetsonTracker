"""Package 5: decode-latency bench tool smoke test.

No network connections made. Verifies:
  - Module imports without error
  - build_parser() returns a valid ArgumentParser
  - Default arg values are correct
  - --path choices accepted / unknown rejected
  - run_bench does not crash when cv2.VideoCapture fails to open (non-fatal error path)
"""
from __future__ import annotations
import argparse
import sys

import pytest


def test_module_imports():
    """tools/measure_decode.py imports without error."""
    import importlib
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
    mod = importlib.import_module("measure_decode")
    assert hasattr(mod, "build_parser")
    assert hasattr(mod, "run_bench")
    assert hasattr(mod, "main")


def test_build_parser_returns_argparser():
    """build_parser() returns a valid ArgumentParser."""
    import importlib
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
    mod = importlib.import_module("measure_decode")
    p = mod.build_parser()
    assert isinstance(p, argparse.ArgumentParser)


def test_default_args():
    """Default URL, frames, path, and codec match spec."""
    import importlib
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
    mod = importlib.import_module("measure_decode")
    p = mod.build_parser()
    args = p.parse_args([])
    assert args.url == "rtsp://192.168.100.88:554/2"
    assert args.frames == 300
    assert args.path == "both"
    assert args.codec == "h264"


def test_path_choices_accepted():
    """--path a, b, and both are accepted."""
    import importlib
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
    mod = importlib.import_module("measure_decode")
    p = mod.build_parser()
    for choice in ("a", "b", "both"):
        args = p.parse_args(["--path", choice])
        assert args.path == choice


def test_unknown_path_rejected():
    """--path with an invalid value raises SystemExit."""
    import importlib
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
    mod = importlib.import_module("measure_decode")
    p = mod.build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["--path", "c"])


def test_run_bench_non_fatal_on_open_failure(capsys):
    """run_bench logs an error and continues when VideoCapture fails to open
    (no network available in CI)."""
    import importlib
    import os
    from unittest.mock import patch, MagicMock
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
    mod = importlib.import_module("measure_decode")

    # Patch _open_cv2 to raise RuntimeError (simulates no network / camera offline)
    with patch.object(mod, "_open_cv2", side_effect=RuntimeError("connection refused")):
        # Should not raise — error is printed and bench continues
        mod.run_bench("rtsp://127.0.0.1:554/2", n_frames=5, paths=["a"])

    captured = capsys.readouterr()
    assert "ERROR" in captured.err or "ERROR" in captured.out
