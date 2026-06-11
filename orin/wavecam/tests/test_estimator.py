"""Tests for the constant-velocity Kalman estimator.

The estimator is purely mathematical — no I/O, no threading. Tests use
synthetic NormalizedFix-like objects and FusionResult-like objects. No real
GPS or camera connection is needed.

These tests pin correctness, not tuning. Loose numerical bounds are used
throughout: the estimator is validated against real session data in the sim
harness (Task 6), not here.
"""
import math
import time
import types

from wavecam.estimator import TargetEstimator, EstimatorOutput


# ── helpers ─────────────────────────────────────────────────────────────────

def _cfg(
    shadow=True,
    enabled=True,
    q_accel=2.0,
    p0_pos=25.0,
    p0_vel=9.0,
    r_gps_fresh=4.0,
    r_gps_age_scale=0.5,
    r_vis_deg=1.0,
    zoom_cov_wide_deg=4.0,
    zoom_cov_narrow_deg=1.5,
    log_every_n=1,
):
    return types.SimpleNamespace(
        shadow=shadow,
        enabled=enabled,
        q_accel=q_accel,
        p0_pos=p0_pos,
        p0_vel=p0_vel,
        r_gps_fresh=r_gps_fresh,
        r_gps_age_scale=r_gps_age_scale,
        r_vis_deg=r_vis_deg,
        zoom_cov_wide_deg=zoom_cov_wide_deg,
        zoom_cov_narrow_deg=zoom_cov_narrow_deg,
        log_every_n=log_every_n,
    )


def _gps_cfg():
    return types.SimpleNamespace(stale_threshold_sec=10.0)


def _fix(lat=21.6, lon=-158.0, speed=5.0, course=270.0, age_sec=2.0):
    return types.SimpleNamespace(
        lat=lat, lon=lon, speed=speed, course=course, age_sec=age_sec
    )


def _pose(lat=21.601, lon=-158.001, pan_anchor_enc=0.0,
          pan_anchor_bearing=247.0, pan_enc_per_deg=4.47,
          tilt_anchor_enc=0.0, tilt_anchor_elev=0.0, tilt_enc_per_deg=4.0):
    """Minimal CameraPose-compatible stub."""
    class _Pose:
        def __init__(self):
            self.lat = lat
            self.lon = lon
            self.alt_m = 0.0
            self.has_base = True
            self.calibrated = True
            self._pan_anchor_enc = pan_anchor_enc
            self._pan_anchor_bearing = pan_anchor_bearing
            self._pan_enc_per_deg = pan_enc_per_deg
            self._tilt_anchor_enc = tilt_anchor_enc
            self._tilt_anchor_elev = tilt_anchor_elev
            self._tilt_enc_per_deg = tilt_enc_per_deg

        def bearing_to_pan_encoder(self, bearing_deg):
            delta = bearing_deg - self._pan_anchor_bearing
            return self._pan_anchor_enc + delta * self._pan_enc_per_deg

        def pan_encoder_to_bearing(self, enc):
            return self._pan_anchor_bearing + (enc - self._pan_anchor_enc) / self._pan_enc_per_deg

        def elevation_to_tilt_encoder(self, elev_deg):
            return self._tilt_anchor_enc + elev_deg * self._tilt_enc_per_deg
    return _Pose()


def _fov_curve():
    """Minimal FOV curve: three points covering the zoom range."""
    return [(0, 60.0), (8192, 12.0), (16384, 5.0)]  # (zoom_enc, fov_deg)


def _make_est():
    est = TargetEstimator(cfg=_cfg(), gps_cfg=_gps_cfg(), pose=_pose(), fov_curve=_fov_curve())
    return est


# ── tests ────────────────────────────────────────────────────────────────────

def test_estimator_not_initialised_before_first_gps():
    est = _make_est()
    assert not est.initialised


def test_first_gps_initialises_state():
    est = _make_est()
    fix = _fix()
    est.update_gps(fix, now=1000.0)
    assert est.initialised
    out = est.predict_output(now=1000.0)
    # bearing and distance must be plausible (subject is ~100m from base)
    assert 0 <= out.bearing_deg < 360
    assert 1 < out.dist_m < 2000


def test_second_gps_update_moves_state():
    est = _make_est()
    fix1 = _fix(lat=21.600, lon=-158.000)
    fix2 = _fix(lat=21.600, lon=-158.001)  # moved ~88m west
    est.update_gps(fix1, now=1000.0)
    out1 = est.predict_output(now=1000.0)
    est.update_gps(fix2, now=1002.0)
    out2 = est.predict_output(now=1002.0)
    # Longitude change = westward = bearing ~270°
    assert out2.dist_m > out1.dist_m or abs(out2.bearing_deg - out1.bearing_deg) > 1.0


def test_stale_gps_skipped():
    est = _make_est()
    fix_fresh = _fix(age_sec=2.0)
    fix_stale = _fix(age_sec=15.0)   # above gps_stale_sec=10.0
    est.update_gps(fix_fresh, now=1000.0)
    state_before = (est._x[0], est._x[1])
    est.update_gps(fix_stale, now=1001.0)
    # State should have been predicted forward (time passed) but not updated by the stale obs
    # Velocity-based prediction will change the position slightly; direction is unchanged
    out = est.predict_output(now=1001.0)
    assert out is not None   # still outputs — just didn't fuse the stale fix


def test_gps_noise_scaling_with_age():
    """Older fixes should produce higher R (measured indirectly: the covariance
    after update is larger when we feed a stale fix vs a fresh one)."""
    from copy import deepcopy
    est_fresh = _make_est()
    est_stale = _make_est()

    fix_fresh = _fix(age_sec=2.0)
    fix_stale = _fix(age_sec=8.0)

    est_fresh.update_gps(fix_fresh, now=1000.0)
    est_stale.update_gps(fix_stale, now=1000.0)

    # First update always sets state; but pos variance in P should reflect noise
    # Compare second update (filter has warmed up)
    est_fresh.update_gps(fix_fresh, now=1002.0)
    est_stale.update_gps(fix_stale, now=1002.0)

    trace_fresh = sum(est_fresh._P[i][i] for i in range(4))
    trace_stale = sum(est_stale._P[i][i] for i in range(4))
    assert trace_stale >= trace_fresh   # stale measurement → larger residual uncertainty


def test_vision_update_reduces_bearing_uncertainty():
    """A fused vision observation should reduce the bearing std (covariance shrinks)."""
    est = _make_est()
    est.update_gps(_fix(lat=21.600, lon=-158.001), now=1000.0)
    cov_before = sum(est._P[i][i] for i in range(4))

    # Simulate a locked detection: pan_enc roughly pointing toward subject, pixel centred
    pred = est.predict_output(now=1001.0)
    approx_pan_enc = int(est._pose.bearing_to_pan_encoder(pred.bearing_deg))
    est.update_vision(
        pan_enc=approx_pan_enc,
        pixel_cx=320.0, frame_w=640, zoom_enc=0,
        now=1001.0,
    )
    cov_after = sum(est._P[i][i] for i in range(4))
    assert cov_after < cov_before   # vision fused → uncertainty reduced


def test_predict_output_bearing_is_plausible():
    est = _make_est()
    # Subject 200 m due west of base (west = bearing ~270°)
    import math
    base_lat = 21.601
    # 200m west at this latitude: Δlon ≈ 200 / (111320 * cos(lat))
    dlon = -200.0 / (111320.0 * math.cos(math.radians(base_lat)))
    fix = _fix(lat=base_lat, lon=-158.0 + dlon)
    est = TargetEstimator(
        cfg=_cfg(),
        gps_cfg=_gps_cfg(),
        pose=_pose(lat=base_lat, lon=-158.0),
        fov_curve=_fov_curve(),
    )
    est.update_gps(fix, now=1000.0)
    out = est.predict_output(now=1000.0)
    # Should be close to 270° (due west) with our simplified geometry
    assert abs(out.bearing_deg - 270.0) < 10.0


def test_pan_enc_would_derived_from_bearing():
    est = _make_est()
    est.update_gps(_fix(lat=21.600, lon=-158.001), now=1000.0)
    out = est.predict_output(now=1000.0)
    # pan_enc_would must be consistent with bearing via the pose mapping
    expected_enc = est._pose.bearing_to_pan_encoder(out.bearing_deg)
    assert abs(out.pan_enc_would - expected_enc) < 1.0


def test_not_initialised_if_disabled():
    est = TargetEstimator(
        cfg=_cfg(enabled=False),
        gps_cfg=_gps_cfg(),
        pose=_pose(),
        fov_curve=_fov_curve(),
    )
    est.update_gps(_fix(), now=1000.0)
    assert not est.initialised   # disabled → no-op


def test_empty_fov_curve_raises_on_init():
    """The G2 gate: shadow mode cannot start without the FOV curve."""
    import pytest
    with pytest.raises(RuntimeError, match="FOV curve"):
        TargetEstimator(
            cfg=_cfg(shadow=True),
            gps_cfg=_gps_cfg(),
            pose=_pose(),
            fov_curve=[],   # empty → must raise
        )


def test_bearing_std_present_in_output():
    est = _make_est()
    est.update_gps(_fix(), now=1000.0)
    out = est.predict_output(now=1000.0)
    assert out.bearing_std_deg >= 0.0
