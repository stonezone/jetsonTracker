"""GPS-bearing fusion cue tests — pure, no I/O. (Plan v3 Phase 3)

Core cases adapted from Kimi's Phase-B draft; extended with the off-screen gate
and an explicit pan-wrap case.
"""
import pytest

from wavecam.gps_bearing_cue import compute_bearing_cue

FOV_CURVE = [(0, 60.0), (10000, 10.0), (16384, 5.0)]


def test_no_fov_curve_returns_none():
    assert compute_bearing_cue(0.0, 0.0, [], 0, 640, 480) is None


def test_empty_frame_returns_none():
    assert compute_bearing_cue(0.0, 0.0, FOV_CURVE, 0, 0, 0) is None


def test_zero_error_centers_cue():
    c = compute_bearing_cue(90.0, 90.0, FOV_CURVE, 0, 640, 480)
    assert c is not None
    assert c.cx == pytest.approx(320.0)
    assert c.cy == pytest.approx(240.0)


def test_right_shifted_target_moves_cue_right():
    c = compute_bearing_cue(95.0, 90.0, FOV_CURVE, 0, 640, 480)
    assert c.cx > 320.0
    assert c.cy == pytest.approx(240.0)


def test_left_shifted_target_moves_cue_left():
    c = compute_bearing_cue(85.0, 90.0, FOV_CURVE, 0, 640, 480)
    assert c.cx < 320.0


def test_bearing_wrap_handled():
    # 359 vs 1 deg = +2 deg (not -358) -> cue shifts slightly right of center
    c = compute_bearing_cue(1.0, 359.0, FOV_CURVE, 0, 640, 480)
    assert c is not None
    px_per_deg = 640 / 60.0
    assert c.cx == pytest.approx(320.0 + 2.0 * px_per_deg, abs=1.0)


def test_high_uncertainty_grows_radius():
    low = compute_bearing_cue(90.0, 90.0, FOV_CURVE, 0, 640, 480, bearing_uncertainty_deg=2.0)
    high = compute_bearing_cue(90.0, 90.0, FOV_CURVE, 0, 640, 480, bearing_uncertainty_deg=10.0)
    assert high.radius_px > low.radius_px


def test_narrow_fov_tightens_pixel_offset():
    # Same 5 deg error: a narrow FOV (more px/deg) pushes the cue farther from center
    wide = compute_bearing_cue(95.0, 90.0, FOV_CURVE, 0, 640, 480)        # hfov 60
    narrow = compute_bearing_cue(95.0, 90.0, FOV_CURVE, 16384, 640, 480)  # hfov 5
    assert (narrow.cx - 320.0) > (wide.cx - 320.0)


def test_offscreen_target_is_omitted():
    # 40 deg off with hfov 60 (half=30) + tolerance 10 -> exactly at the edge; 50 deg is beyond
    assert compute_bearing_cue(140.0, 90.0, FOV_CURVE, 0, 640, 480) is None


def test_near_edge_target_still_cued():
    # 25 deg off with hfov 60 (within half=30) -> cue present, shifted well right
    c = compute_bearing_cue(115.0, 90.0, FOV_CURVE, 0, 640, 480)
    assert c is not None
    assert c.cx > 320.0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print("BEARING CUE TESTS PASSED (%d)" % len(fns))
