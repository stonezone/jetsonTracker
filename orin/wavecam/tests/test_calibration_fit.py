"""Multi-point offset refine — the pure least-squares pan-offset fit.

Fixed scale (14.4 c/deg, hard-stop-measured); the fit averages the OFFSET across
aims at varied bearings to cancel per-aim GPS-bearing error, and must handle pan
bearings that wrap across north (350°/10°) without a full-range jump."""
import math

from wavecam.calibration_fit import fit_pan_offset

S = 14.4


def _angdiff(a, b):
    return ((a - b + 180.0) % 360.0) - 180.0


def _enc(anchor_enc, anchor_bearing, bearing, s=S):
    """Encoder a perfectly-calibrated pose would read aiming at `bearing`."""
    return anchor_enc + _angdiff(bearing, anchor_bearing) * s


def _predict(res, bearing, s=S):
    return res.anchor_enc + _angdiff(bearing, res.anchor_bearing_deg) * s


def test_single_sample_reproduces_exactly():
    res = fit_pan_offset([(1000.0, 90.0)], S)
    assert abs(_predict(res, 90.0) - 1000.0) < 1e-6
    assert res.rms_residual_deg == 0.0
    assert res.sample_count == 1


def test_two_consistent_samples_zero_residual():
    s1 = (_enc(1000.0, 90.0, 80.0), 80.0)
    s2 = (_enc(1000.0, 90.0, 100.0), 100.0)
    res = fit_pan_offset([s1, s2], S)
    for enc, brg in (s1, s2):
        assert abs(_predict(res, brg) - enc) < 1e-6
    assert res.rms_residual_deg < 1e-6


def test_noisy_samples_average_out_below_single_aim_error():
    truth_enc, truth_brg = 500.0, 270.0
    brgs = [250.0, 260.0, 280.0, 290.0]
    enc_noise = [12.0, -8.0, 5.0, -10.0]   # per-aim encoder error (counts)
    samples = [(_enc(truth_enc, truth_brg, b) + n, b) for b, n in zip(brgs, enc_noise)]
    res = fit_pan_offset(samples, S)
    # the averaged anchor beats the worst single-aim error (12 counts)
    true_at_centroid = _enc(truth_enc, truth_brg, res.anchor_bearing_deg)
    assert abs(res.anchor_enc - true_at_centroid) < 6.0
    assert res.rms_residual_deg > 0.0          # noise is reported, not hidden
    assert res.worst_residual_deg >= res.rms_residual_deg


def test_wraparound_bearings_no_full_range_jump():
    # straddling north: 350° and 10° are 20° apart, NOT 340°
    samples = [(_enc(100.0, 0.0, 350.0), 350.0), (_enc(100.0, 0.0, 10.0), 10.0)]
    res = fit_pan_offset(samples, S)
    for enc, brg in samples:
        assert abs(_predict(res, brg) - enc) < 1e-6
    assert res.rms_residual_deg < 1e-6
    # centroid bearing sits near 0/360, not ~180
    assert min(abs(res.anchor_bearing_deg), abs(res.anchor_bearing_deg - 360.0)) < 1.0


def test_empty_samples_raises():
    import pytest
    with pytest.raises(ValueError):
        fit_pan_offset([], S)
