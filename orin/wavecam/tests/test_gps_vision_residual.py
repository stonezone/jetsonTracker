"""GPS↔vision bearing residual — observe-only measurement (Part B, 2026-06-25 spec).

bearing_residual returns the angular disagreement (deg) + pixel offset between where VISION
sees the subject (screen x-fraction, 0.5 = center) and where GPS says it is (bearing).
0 = perfect agreement. Pure. It must NEVER feed pointing/fusion — it is published only."""
from wavecam.gps_bearing_cue import bearing_residual

# zoom 0 -> 60deg hfov (wide); narrows at tele. Tests use zoom_enc=0 -> hfov 60.
FOV = [(0, 60.0), (16384, 6.0)]


def test_residual_zero_when_vision_center_matches_gps_bearing():
    deg, px = bearing_residual(target_bearing_deg=100.0, current_bearing_deg=100.0,
                               vision_target_x_frac=0.5, fov_curve=FOV, zoom_enc=0, frame_w=1280)
    assert abs(deg) < 1e-6
    assert abs(px) < 1e-6


def test_residual_from_vision_offset_right_of_center():
    # vision sees the subject at x=0.75 (right of center by 0.25 of a 60deg FOV = +15deg);
    # GPS bearing == current aim. residual = +15deg, +320px (0.25*1280).
    deg, px = bearing_residual(100.0, 100.0, 0.75, FOV, 0, 1280)
    assert abs(deg - 15.0) < 1e-6
    assert abs(px - 320.0) < 1e-6


def test_residual_from_gps_bearing_offset():
    # vision dead center -> vision bearing = current aim (100); GPS says 110. residual = -10deg.
    deg, _px = bearing_residual(110.0, 100.0, 0.5, FOV, 0, 1280)
    assert abs(deg - (-10.0)) < 1e-6


def test_residual_wrap_safe_across_north():
    # current 359, vision center -> 359; gps 1. normalize_180(359 - 1) = -2deg, not +358.
    deg, _px = bearing_residual(1.0, 359.0, 0.5, FOV, 0, 1280)
    assert abs(deg - (-2.0)) < 1e-6


# --- /status exposure (the measurement is published, not just computed) ---------------------
import os  # noqa: E402
import sys  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
from test_control_api import DummyPipeline  # noqa: E402
from wavecam.web import build_app  # noqa: E402


def test_status_exposes_gps_vision_residual():
    pipe = DummyPipeline()
    pipe._gps_vision_residual = {"deg": 1.5, "px": 32.0, "n": 10, "abs_max_deg": 3.2}
    r = TestClient(build_app(pipe)).get("/api/v1/status")
    assert r.status_code == 200
    assert r.json()["tracking"]["gps_vision_residual"] == {
        "deg": 1.5, "px": 32.0, "n": 10, "abs_max_deg": 3.2}


def test_status_gps_vision_residual_null_when_absent():
    """No fresh-GPS+lock coincidence (or a fresh process) → the field is present but null."""
    pipe = DummyPipeline()
    r = TestClient(build_app(pipe)).get("/api/v1/status")
    assert r.json()["tracking"]["gps_vision_residual"] is None
