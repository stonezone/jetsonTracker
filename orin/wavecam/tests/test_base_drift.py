"""Unit tests for the base drift monitor — pure, no I/O. (Plan v3 Phase 1)

Core dual-trigger cases adapted from Kimi's Phase-B draft; extended for the
5-state model, the quality gate (unknown preserves lock), suspect/confirmed
split, and sticky unlock.
"""
import pytest

from wavecam.base_drift import (
    DISABLED, LOCKED, SUSPECT, UNKNOWN, UNLOCKED, BaseDriftMonitor,
)

THRESHOLD_M = 2.0
MIN_TREND_M = 1.0
WINDOW = 5


def _mon(**kw):
    defaults = dict(threshold_m=THRESHOLD_M, min_trend_m=MIN_TREND_M,
                    window_size=WINDOW, min_consecutive=WINDOW)
    defaults.update(kw)
    return BaseDriftMonitor(**defaults)


# --- core dual-trigger behavior (adapted from Kimi) -------------------------

def test_disabled_preserves_locked():
    m = _mon(enabled=False)
    m.latch(21.6, -158.0, 2.0)
    r = m.update(21.7, -158.0, 2.0, t=1.0, currently_locked=True)
    assert r.state == DISABLED
    assert r.locked is True
    assert r.alert is False


def test_no_latch_is_unknown_and_preserves_locked():
    m = _mon()
    r = m.update(21.6, -158.0, 2.0, t=1.0, currently_locked=True)
    assert r.state == UNKNOWN
    assert r.locked is True
    assert r.samples == 0


def test_stationary_stays_locked():
    m = _mon()
    m.latch(21.6, -158.0, 2.0)
    for i in range(WINDOW):
        r = m.update(21.6, -158.0, 2.0, t=float(i))
    assert r.state == LOCKED
    assert r.locked is True
    assert r.mean_distance_m == pytest.approx(0.0, abs=1e-6)


def test_single_jump_under_threshold_stays_locked():
    m = _mon()
    m.latch(21.6, -158.0, 2.0)
    r = m.update(21.600010, -158.0, 2.0, t=1.0)  # ~1.1 m, under 2 m
    assert r.state == LOCKED
    assert r.locked is True


def test_sustained_movement_confirms_unlocked():
    m = _mon()
    m.latch(21.6, -158.0, 2.0)
    for i in range(WINDOW):
        lat = 21.6 + 0.00005 * (i + 1)  # monotonic march away
        r = m.update(lat, -158.0, 2.0, t=float(i))
    assert r.state == UNLOCKED
    assert r.locked is False
    assert r.alert is True
    assert r.mean_distance_m > THRESHOLD_M
    assert abs(r.trend_m) > MIN_TREND_M


def test_scatter_without_trend_stays_locked():
    m = _mon()
    m.latch(21.6, -158.0, 2.0)
    for i, d in enumerate([0.00003, -0.00003, 0.00003, -0.00003, 0.00003]):
        r = m.update(21.6 + d, -158.0, 2.0, t=float(i))
    assert r.state == LOCKED
    assert r.locked is True


def test_latch_resets_samples_and_unlock():
    m = _mon()
    m.latch(21.6, -158.0, 2.0)
    for i in range(WINDOW):
        m.update(21.6 + 0.00005 * (i + 1), -158.0, 2.0, t=float(i))
    m.latch(21.7, -158.0, 2.0)  # recalibrate
    r = m.update(21.7, -158.0, 2.0, t=100.0)
    assert r.samples == 1
    assert r.state == LOCKED


# --- 5-state model + quality gate (new for v3) ------------------------------

def test_movement_below_consecutive_is_suspect_not_unlocked():
    m = _mon(min_consecutive=5)
    m.latch(21.6, -158.0, 2.0)
    # two big monotonic steps: threshold+trend met, but only 2 < 5 samples
    r1 = m.update(21.6 + 0.0001, -158.0, 2.0, t=0.0)
    r2 = m.update(21.6 + 0.0002, -158.0, 2.0, t=1.0)
    assert r2.state == SUSPECT
    assert r2.locked is True  # suspect still trusted (not yet confirmed)
    assert r2.alert is False


def test_stale_fix_is_unknown_and_preserves_lock():
    m = _mon(max_fix_age_sec=10.0)
    m.latch(21.6, -158.0, 2.0)
    # a far, old base fix must NOT unlock — staleness != tripod movement
    r = m.update(21.7, -158.0, 2.0, t=1.0, fix_age_sec=30.0, currently_locked=True)
    assert r.state == UNKNOWN
    assert r.locked is True


def test_low_sats_is_unknown_and_preserves_lock():
    m = _mon(min_sats=6)
    m.latch(21.6, -158.0, 2.0)
    r = m.update(21.7, -158.0, 2.0, t=1.0, sats=3, currently_locked=True)
    assert r.state == UNKNOWN
    assert r.locked is True


def test_good_quality_allows_assessment():
    m = _mon(max_fix_age_sec=10.0, min_sats=6)
    m.latch(21.6, -158.0, 2.0)
    r = m.update(21.6, -158.0, 2.0, t=1.0, fix_age_sec=2.0, sats=9)
    assert r.state == LOCKED
    assert r.samples == 1


def test_unlock_is_sticky_until_relatch():
    m = _mon()
    m.latch(21.6, -158.0, 2.0)
    for i in range(WINDOW):
        m.update(21.6 + 0.00005 * (i + 1), -158.0, 2.0, t=float(i))
    # even a now-stationary fix keeps it unlocked until recalibration
    r = m.update(21.6, -158.0, 2.0, t=99.0)
    assert r.state == UNLOCKED
    assert r.locked is False
    assert r.alert is False  # alert fires once, on the confirming frame only


def test_min_consecutive_boundary_suspect_then_unlocked():
    # DeepSeek PR #96 note: exercise the exact suspect->confirmed boundary.
    m = _mon(min_consecutive=3, window_size=5)
    m.latch(21.6, -158.0, 2.0)
    states = [m.update(21.6 + 0.0001 * (i + 1), -158.0, 2.0, t=float(i)).state
              for i in range(3)]
    assert states[1] == SUSPECT   # 2 samples: below the confirm floor
    assert states[2] == UNLOCKED  # 3rd sample confirms


def test_sticky_unlock_retains_distance_and_trend():
    # Kimi PR #96 note: the sticky-unlocked readout keeps the confirming values.
    m = _mon(min_consecutive=3)
    m.latch(21.6, -158.0, 2.0)
    for i in range(3):
        r = m.update(21.6 + 0.0001 * (i + 1), -158.0, 2.0, t=float(i))
    assert r.state == UNLOCKED and r.mean_distance_m > 0
    s = m.update(21.6, -158.0, 2.0, t=99.0)  # later stationary fix, still sticky
    assert s.state == UNLOCKED
    assert s.mean_distance_m == r.mean_distance_m
    assert s.trend_m == r.trend_m


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print("BASE DRIFT TESTS PASSED (%d)" % len(fns))
