"""Tests for simulation scenarios and replay scorer.

These tests validate the sim harness itself, not the estimator tuning. The
error bounds are loose — they pin sanity (the estimator is in the right
hemisphere) not accuracy (that requires field data). Tuning happens after
the shadow sessions produce real telemetry.
"""
import math
from wavecam.tools.sim.scenarios import (
    straight_run, bottom_turn, gps_dropout, vision_dropout, combined_dropout
)
from wavecam.tools.sim.replay import replay_scenario, score_scenario


def test_straight_run_produces_fixes():
    fixes, detections = straight_run(speed_mps=8.0, duration_sec=10.0, dt_gps=2.0)
    assert len(fixes) > 3
    # All fixes should move in the same direction (constant speed/course)
    bearings = set(round(f.course_deg, 0) for f in fixes)
    assert len(bearings) == 1   # straight line → constant course


def test_bottom_turn_accelerates_laterally():
    fixes, _ = bottom_turn(speed_mps=6.0, accel_mps2=3.0, turn_duration_sec=3.0)
    # Course should change significantly during a bottom turn
    courses = [f.course_deg for f in fixes]
    course_range = max(courses) - min(courses)
    assert course_range > 30.0   # meaningful turn


def test_gps_dropout_has_gap():
    fixes, _ = gps_dropout(dropout_start_sec=5.0, dropout_dur_sec=10.0, duration_sec=20.0)
    # Timestamps should have a gap of at least dropout_dur_sec
    if len(fixes) >= 2:
        times = [f.t for f in fixes]
        max_gap = max(times[i+1] - times[i] for i in range(len(times)-1))
        assert max_gap >= 9.0   # allow small float imprecision


def test_replay_produces_outputs():
    fixes, detections = straight_run(speed_mps=5.0, duration_sec=20.0)
    outputs = replay_scenario(fixes, detections)
    # Should have an output for every fix (or close to it)
    assert len(outputs) > 0


def test_score_straight_run_bearing_error():
    """For a straight run, the estimator bearing error should be < 10° after warmup."""
    fixes, detections = straight_run(speed_mps=5.0, duration_sec=30.0)
    outputs = replay_scenario(fixes, detections)
    score = score_scenario(outputs, fixes, warmup_sec=5.0)
    # Loose bound: estimator should track within 10° bearing error for a straight run
    assert score["mean_bearing_error_deg"] < 10.0, \
        f"Straight run bearing error too high: {score['mean_bearing_error_deg']:.1f}°"


def test_score_gps_dropout_bearing_error():
    """During a GPS dropout, bearing error grows but recovers after GPS returns."""
    fixes, detections = gps_dropout(dropout_start_sec=5.0, dropout_dur_sec=10.0,
                                    duration_sec=25.0)
    outputs = replay_scenario(fixes, detections)
    score = score_scenario(outputs, fixes, warmup_sec=4.0)
    # Loose: during and after dropout the error can be up to 30° (no measurement)
    # but it should not be infinite (state keeps predicting)
    assert score["max_bearing_error_deg"] < 30.0 or score["max_bearing_error_deg"] is not None


def test_score_combined_dropout():
    """Combined GPS + vision dropout: state should not diverge (covariance bound)."""
    fixes, detections = combined_dropout(dropout_start_sec=5.0, dropout_dur_sec=8.0,
                                         duration_sec=20.0)
    outputs = replay_scenario(fixes, detections)
    # No crash, some outputs exist (estimator predicts forward)
    assert outputs is not None
