"""Unit tests for DriveZoom — pure, no I/O."""
import pytest

from wavecam.zoom_curve import DriveZoom, ZoomCurveConfig


def _cfg(**kw):
    defaults = dict(
        enabled=True, near_m=40.0, far_m=250.0,
        max_frac=0.60, max_enc=16384.0, min_enc=0.0, rate_limit=300.0,
    )
    defaults.update(kw)
    return ZoomCurveConfig(**defaults)


def _drv(**kw):
    return DriveZoom(_cfg(**kw))


# --- enabled gate ---------------------------------------------------------

def test_disabled_returns_none():
    dz = _drv(enabled=False)
    assert dz.compute(100.0) is None


def test_enabled_returns_value():
    dz = _drv(enabled=True)
    result = dz.compute(100.0)
    assert result is not None
    assert result > 0


# --- distance mapping -----------------------------------------------------

def test_near_distance_is_wide():
    dz = _drv(near_m=40.0)
    result = dz.compute(40.0)
    assert result == pytest.approx(0.0, abs=1.0)


def test_far_distance_is_tele():
    dz = _drv(far_m=250.0, max_frac=0.60, max_enc=16384.0)
    result = dz.compute(250.0)
    assert result == pytest.approx(0.60 * 16384.0, abs=1.0)


def test_beyond_far_clamped():
    dz = _drv(far_m=250.0, max_frac=0.60, max_enc=16384.0)
    result = dz.compute(500.0)
    assert result == pytest.approx(0.60 * 16384.0, abs=1.0)


def test_below_near_is_wide():
    dz = _drv(near_m=40.0)
    result = dz.compute(10.0)
    assert result == pytest.approx(0.0, abs=1.0)


def test_mid_range_is_linear():
    dz = _drv(near_m=40.0, far_m=250.0, max_frac=0.60, max_enc=16384.0)
    result = dz.compute(145.0)  # exactly halfway
    expected = 0.5 * 0.60 * 16384.0
    assert result == pytest.approx(expected, abs=1.0)


# --- calibrated limits ----------------------------------------------------

def test_min_enc_clamp():
    dz = _drv(min_enc=2000.0, near_m=40.0)
    result = dz.compute(40.0)  # would be 0, clamped to 2000
    assert result == pytest.approx(2000.0, abs=1.0)


def test_max_enc_clamp():
    dz = _drv(max_enc=8000.0, max_frac=1.0, far_m=100.0)
    result = dz.compute(500.0)
    assert result == pytest.approx(8000.0, abs=1.0)


# --- rate limiting --------------------------------------------------------

def test_rate_limit_smooths_transition():
    dz = _drv(rate_limit=100.0, near_m=40.0, far_m=250.0, max_frac=0.60, max_enc=16384.0)
    first = dz.compute(40.0)   # near → wide
    assert first is not None and first < 100.0
    far = dz.compute(250.0)    # far → tele, should be rate-limited
    assert far is not None
    delta = far - first
    assert delta <= 100.0 + 1.0  # within rate limit + float tolerance


def test_rate_limit_converges():
    dz = _drv(rate_limit=200.0, near_m=40.0, far_m=250.0, max_frac=0.60, max_enc=16384.0)
    dz.compute(40.0)           # seed near
    target_zoom = 0.60 * 16384.0
    for _ in range(50):
        dz.compute(250.0)      # march toward far
    assert dz.current == pytest.approx(target_zoom, abs=1.0)


# --- reset -----------------------------------------------------------------

def test_reset_clears_state():
    dz = _drv()
    dz.compute(100.0)
    assert dz.current is not None
    dz.reset()
    assert dz.current is None


def test_after_reset_no_rate_limit_on_first_call():
    dz = _drv(rate_limit=10.0)
    dz.compute(50.0)           # set state
    dz.reset()
    result = dz.compute(250.0) # fresh start, full jump allowed
    assert result is not None
    assert result > 100.0      # not rate-limited (first call after reset)


# --- edge cases -----------------------------------------------------------

def test_none_distance():
    dz = _drv()
    assert dz.compute(None) is None


def test_zero_distance():
    dz = _drv()
    assert dz.compute(0.0) is None


def test_negative_distance():
    dz = _drv()
    assert dz.compute(-50.0) is None


def test_conservative_max_frac():
    dz = _drv(max_frac=0.40, max_enc=16384.0, far_m=200.0)
    result = dz.compute(300.0)
    assert result == pytest.approx(0.40 * 16384.0, abs=1.0)


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"ZOOM CURVE TESTS PASSED ({len(fns)})")
