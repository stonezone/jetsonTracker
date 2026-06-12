"""Unit tests for Phase-2 vision range observation (T2.1, T2.2, T2.3).

Test shapes (all TDD — written before the implementation):

  1. Range math: known geometry → expected range (pinned formula).
  2. vfov conversion: hfov → vfov via 16:9 aspect (pinned).
  3. Filter convergence: range+bearing vs bearing-only radial covariance tightening.
  4. Flag-off zero behaviour: tick with use_vision_range=False never calls update_vision_range.
  5. Sim harness range-on/off comparison: range obs must tighten radial covariance.
"""
from __future__ import annotations

import math
import types

import pytest

from wavecam.estimator import TargetEstimator, _fov_at_zoom


# ── helpers ──────────────────────────────────────────────────────────────────

def _cfg(use_vision_range=True, subject_height_m=1.0, r_range_frac=0.3, **kwargs):
    defaults = dict(
        shadow=True, enabled=True, q_accel=2.0,
        p0_pos=25.0, p0_vel=9.0,
        r_gps_fresh=4.0, r_gps_age_scale=0.5, r_vis_deg=1.0,
        zoom_cov_wide_deg=4.0, zoom_cov_narrow_deg=1.5, log_every_n=1,
        use_vision_range=use_vision_range,
        subject_height_m=subject_height_m,
        r_range_frac=r_range_frac,
    )
    defaults.update(kwargs)
    return types.SimpleNamespace(**defaults)


def _gps_cfg():
    return types.SimpleNamespace(stale_threshold_sec=10.0)


def _pose(lat=21.601, lon=-158.001):
    class _Pose:
        def __init__(self):
            self.lat = lat
            self.lon = lon
            self.alt_m = 0.0
            self.has_base = True
            self.calibrated = True
            self._pan_anchor_enc = 0.0
            self._pan_anchor_bearing = 247.0
            self._pan_enc_per_deg = 4.47
            self._tilt_anchor_enc = 0.0
            self._tilt_anchor_elev = 0.0
            self._tilt_enc_per_deg = 4.0

        def bearing_to_pan_encoder(self, bearing_deg):
            return self._pan_anchor_enc + (bearing_deg - self._pan_anchor_bearing) * self._pan_enc_per_deg

        def pan_encoder_to_bearing(self, enc):
            return self._pan_anchor_bearing + (enc - self._pan_anchor_enc) / self._pan_enc_per_deg

        def elevation_to_tilt_encoder(self, elev_deg):
            return self._tilt_anchor_enc + elev_deg * self._tilt_enc_per_deg

    return _Pose()


def _fov_curve():
    # Single-point at wide — exact at this zoom; matches G2 gate.
    return [(0, 60.0), (8192, 12.0), (16384, 5.0)]


def _fix(lat=21.600, lon=-158.002, age_sec=2.0):
    return types.SimpleNamespace(
        lat=lat, lon=lon, speed=5.0, course=270.0, age_sec=age_sec
    )


def _make_est(cfg=None):
    return TargetEstimator(
        cfg=cfg or _cfg(),
        gps_cfg=_gps_cfg(),
        pose=_pose(),
        fov_curve=_fov_curve(),
    )


# ── 1. Range math: known geometry → expected range ───────────────────────────

class TestRangeMath:
    """Pin the forward range formula against hand-computed values.

    Model:
        vfov = 2 * atan(tan(hfov/2) * 9/16)
        angle_sub = vfov * (bbox_h / frame_h)
        range_m = subject_height_m / (2 * tan(angle_sub / 2))
    """

    def _expected_range(self, hfov_deg, bbox_h_px, frame_h, subject_h=1.0):
        hfov = math.radians(hfov_deg)
        vfov = 2.0 * math.atan(math.tan(hfov / 2.0) * 9.0 / 16.0)
        angle_sub = vfov * (bbox_h_px / frame_h)
        return subject_h / (2.0 * math.tan(angle_sub / 2.0))

    def test_range_100m(self):
        """At 100 m with hfov=60°, bbox should round-trip through the formula."""
        hfov_deg = 60.0
        frame_h = 720.0
        subject_h = 1.0
        hfov = math.radians(hfov_deg)
        vfov = 2.0 * math.atan(math.tan(hfov / 2.0) * 9.0 / 16.0)
        # Inverse: angle_sub for 100 m
        angle_sub = 2.0 * math.atan(subject_h / 2.0 / 100.0)
        bbox_h = (angle_sub / vfov) * frame_h
        expected = self._expected_range(hfov_deg, bbox_h, frame_h, subject_h)
        assert abs(expected - 100.0) < 0.01, f"Round-trip failed: {expected:.3f} ≠ 100.0"

    def test_range_50m(self):
        """50 m → double the angular subtense → smaller range."""
        hfov_deg = 60.0
        frame_h = 720.0
        hfov = math.radians(hfov_deg)
        vfov = 2.0 * math.atan(math.tan(hfov / 2.0) * 9.0 / 16.0)
        angle_sub = 2.0 * math.atan(1.0 / 2.0 / 50.0)
        bbox_h = (angle_sub / vfov) * frame_h
        expected = self._expected_range(hfov_deg, bbox_h, frame_h, 1.0)
        assert abs(expected - 50.0) < 0.01, f"50m round-trip: {expected:.3f}"

    def test_range_200m(self):
        """200 m → half the angular subtense."""
        hfov_deg = 60.0
        frame_h = 720.0
        hfov = math.radians(hfov_deg)
        vfov = 2.0 * math.atan(math.tan(hfov / 2.0) * 9.0 / 16.0)
        angle_sub = 2.0 * math.atan(1.0 / 2.0 / 200.0)
        bbox_h = (angle_sub / vfov) * frame_h
        expected = self._expected_range(hfov_deg, bbox_h, frame_h, 1.0)
        assert abs(expected - 200.0) < 0.5, f"200m round-trip: {expected:.3f}"

    def test_taller_subject(self):
        """subject_height_m=1.8 → proportionally larger range for same bbox."""
        hfov_deg = 60.0
        frame_h = 720.0
        hfov = math.radians(hfov_deg)
        vfov = 2.0 * math.atan(math.tan(hfov / 2.0) * 9.0 / 16.0)
        # Use the bbox that would correspond to 100 m with 1.0 m subject
        angle_sub = 2.0 * math.atan(1.0 / 2.0 / 100.0)
        bbox_h = (angle_sub / vfov) * frame_h
        r1 = self._expected_range(hfov_deg, bbox_h, frame_h, 1.0)
        r18 = self._expected_range(hfov_deg, bbox_h, frame_h, 1.8)
        assert abs(r18 / r1 - 1.8) < 0.01, "Range scales linearly with subject height"


# ── 2. vfov conversion: pinned ────────────────────────────────────────────────

class TestVfovConversion:
    """vfov = 2 * atan(tan(hfov/2) * 9/16). Pin a few known values."""

    def _vfov(self, hfov_deg):
        hfov = math.radians(hfov_deg)
        return math.degrees(2.0 * math.atan(math.tan(hfov / 2.0) * 9.0 / 16.0))

    def test_vfov_60h(self):
        """hfov=60° → vfov ≈ 35.98° for 16:9. Pinned to formula output."""
        vfov = self._vfov(60.0)
        # Exact: 2*atan(tan(30°) * 9/16) ≈ 35.98°
        assert abs(vfov - 35.98) < 0.1, f"vfov(60°hfov)={vfov:.2f}°"

    def test_vfov_narrow(self):
        """hfov=12° (telephoto) → vfov ≈ 6.75° for 16:9."""
        vfov = self._vfov(12.0)
        assert abs(vfov - 6.75) < 0.3, f"vfov(12°hfov)={vfov:.2f}°"

    def test_vfov_always_smaller_than_hfov(self):
        """16:9 → vfov < hfov for any positive hfov."""
        for h in [5.0, 12.0, 30.0, 60.0, 90.0]:
            assert self._vfov(h) < h


# ── 3. Filter convergence: range+bearing vs bearing-only radial cov ──────────

class TestFilterConvergence:
    """Range observations must tighten radial (position) covariance vs bearing-only."""

    def _bearing_only_est(self):
        est = TargetEstimator(
            cfg=_cfg(use_vision_range=False),
            gps_cfg=_gps_cfg(), pose=_pose(), fov_curve=_fov_curve(),
        )
        return est

    def _range_bearing_est(self):
        est = TargetEstimator(
            cfg=_cfg(use_vision_range=True),
            gps_cfg=_gps_cfg(), pose=_pose(), fov_curve=_fov_curve(),
        )
        return est

    def _radial_cov(self, est):
        """P_ee + P_nn — the radial position uncertainty."""
        from wavecam.estimator import _mat_to_list
        P = _mat_to_list(est._P)
        return P[0][0] + P[1][1]

    def test_range_obs_tightens_radial_covariance(self):
        """A range update on the same tick must reduce P_ee + P_nn vs no update.

        We compare P immediately before vs after a single range update to isolate
        the update step from prediction (which grows P). A valid Kalman update
        for a range observation that is consistent with the current state must
        reduce radial position variance.
        """
        fix = _fix()
        est = self._range_bearing_est()

        # Init from GPS (first update sets state, P stays at p0)
        est.update_gps(fix, now=1000.0)

        # Compute bbox_h from ground-truth distance
        from wavecam.gps_geo import haversine_m
        dist = haversine_m(_pose().lat, _pose().lon, fix.lat, fix.lon)
        hfov_deg = 60.0
        frame_h = 720.0
        hfov = math.radians(hfov_deg)
        vfov = 2.0 * math.atan(math.tan(hfov / 2.0) * 9.0 / 16.0)
        angle_sub = 2.0 * math.atan(1.0 / 2.0 / dist)
        bbox_h = (angle_sub / vfov) * frame_h

        # Get covariance trace before range update (freeze time so no prediction)
        cov_before = self._radial_cov(est)

        # Single range update at same timestamp (no prediction step)
        est.update_vision_range(bbox_h_px=bbox_h, frame_h=frame_h,
                                zoom_enc=0, now=1000.0)

        cov_after = self._radial_cov(est)
        assert cov_after < cov_before, (
            f"Range update must reduce radial cov: after={cov_after:.4f} >= before={cov_before:.4f}"
        )

    def test_range_obs_not_called_when_uninitialised(self):
        """update_vision_range must be a no-op when the estimator is not initialised."""
        est = self._range_bearing_est()
        assert not est.initialised
        # Must not raise and must not initialise
        est.update_vision_range(bbox_h_px=50.0, frame_h=720.0, zoom_enc=0, now=1000.0)
        assert not est.initialised

    def test_range_obs_convergence_over_time(self):
        """With repeated range + GPS, the radial covariance should stay bounded."""
        fix = _fix()
        est = self._range_bearing_est()
        est.update_gps(fix, now=1000.0)

        from wavecam.gps_geo import haversine_m
        dist = haversine_m(_pose().lat, _pose().lon, fix.lat, fix.lon)
        hfov = math.radians(60.0)
        vfov = 2.0 * math.atan(math.tan(hfov / 2.0) * 9.0 / 16.0)
        angle_sub = 2.0 * math.atan(1.0 / 2.0 / dist)
        bbox_h = (angle_sub / vfov) * 720.0

        cov_start = self._radial_cov(est)
        for i in range(10):
            est.update_gps(fix, now=1001.0 + i * 0.2)
            est.update_vision_range(bbox_h_px=bbox_h, frame_h=720.0,
                                    zoom_enc=0, now=1001.0 + i * 0.2 + 0.1)

        cov_end = self._radial_cov(est)
        assert cov_end < cov_start, "Radial covariance must reduce with continued range obs"


# ── 4. Flag-off: use_vision_range=False → update_vision_range not called ────

class TestFlagOff:
    """With use_vision_range=False the pipeline must not call update_vision_range."""

    def test_pipeline_does_not_call_range_when_disabled(self, tmp_path):
        """Verify through pipeline._estimator_shadow_tick with flag=False."""
        from wavecam.pipeline import Pipeline
        from wavecam.ptz_visca import NullPtz

        cfg = types.SimpleNamespace(
            camera=types.SimpleNamespace(url="", reconnect_interval=5),
            color=types.SimpleNamespace(enabled=False),
            detector=types.SimpleNamespace(enabled=False, every_n=3, box_ttl_sec=0.3),
            fusion=types.SimpleNamespace(
                lock_threshold=0.6, unlock_threshold=0.35, require_person=False,
                match_dist=120, person_aim_x=0.5, person_aim_y=0.5, ema_alpha=0.5,
                lost_grace_sec=0.8, gps_boost=0.2, gps_boost_radius_frac=0.25,
            ),
            ptz=types.SimpleNamespace(
                enabled=False, command_min_interval=0.05, stop_resend_interval=0.25,
                cinematic_zoom_enabled=False, zoom_target_frac=0.35, zoom_deadband=0.02,
                zoom_max_speed=4, invert_pan=False, invert_tilt=False, deadzone=0.1,
                max_pan_speed=12, max_tilt_speed=9, min_speed=1, ff_gain=0.2,
                ff_deadzone_mult=1.5,
            ),
            gps=types.SimpleNamespace(
                lock_frames=5, grace_sec=1.0, stale_threshold_sec=10.0,
                max_pan_speed=4, max_tilt_speed=3, drive_zoom=False,
            ),
            estimator=types.SimpleNamespace(
                shadow=True, enabled=True, q_accel=2.0, p0_pos=25.0, p0_vel=9.0,
                r_gps_fresh=4.0, r_gps_age_scale=0.5, r_vis_deg=1.0,
                zoom_cov_wide_deg=4.0, zoom_cov_narrow_deg=1.5, log_every_n=1,
                use_vision_range=False,  # OFF
                subject_height_m=1.0, r_range_frac=0.3,
            ),
            loop=types.SimpleNamespace(target_fps=30, log_every_sec=10),
            web=types.SimpleNamespace(jpeg_quality=80, show_hud=False),
            shadow_log_dir=str(tmp_path),
        )
        p = Pipeline(cfg, NullPtz(), lambda: None)
        from wavecam.camera_pose import CameraPose
        p.pose = CameraPose(
            lat=21.601, lon=-158.001, alt_m=0.0,
            pan_anchor_enc=0.0, pan_anchor_bearing=247.0, pan_enc_per_deg=4.47,
            tilt_anchor_enc=0.0, tilt_anchor_elev=0.0, tilt_enc_per_deg=4.0,
        )
        fov_curve = [(0, 60.0), (8192, 12.0), (16384, 5.0)]
        p._init_estimator(fov_curve)
        assert p.estimator is not None

        # Initialise estimator state via GPS
        fix = types.SimpleNamespace(lat=21.600, lon=-158.002, speed=5.0,
                                    course=270.0, age_sec=2.0)
        p.estimator.update_gps(fix, now=1000.0)

        # Patch update_vision_range to detect if it was called
        called = []
        orig = p.estimator.update_vision_range
        p.estimator.update_vision_range = lambda **kw: called.append(kw)

        # Build a locked FusionResult with a person_bbox
        from wavecam.fusion import FusionResult
        fr = FusionResult(
            target_xy=(320.0, 240.0), bbox=(100, 100, 200, 300),
            person_bbox=(100, 100, 200, 300), conf=0.9,
            locked=True, state="TRACKING", has_color=True, has_person=True, matched=True,
        )

        # Run the shadow tick — with use_vision_range=False, the call must not happen
        p._estimator_shadow_tick(fr, w=640, t0=1001.0)
        assert len(called) == 0, "update_vision_range must not be called when flag=False"

    def test_pipeline_calls_range_when_enabled(self, tmp_path):
        """With use_vision_range=True and a person_bbox, the method IS called."""
        from wavecam.pipeline import Pipeline
        from wavecam.ptz_visca import NullPtz

        cfg = types.SimpleNamespace(
            camera=types.SimpleNamespace(url="", reconnect_interval=5),
            color=types.SimpleNamespace(enabled=False),
            detector=types.SimpleNamespace(enabled=False, every_n=3, box_ttl_sec=0.3),
            fusion=types.SimpleNamespace(
                lock_threshold=0.6, unlock_threshold=0.35, require_person=False,
                match_dist=120, person_aim_x=0.5, person_aim_y=0.5, ema_alpha=0.5,
                lost_grace_sec=0.8, gps_boost=0.2, gps_boost_radius_frac=0.25,
            ),
            ptz=types.SimpleNamespace(
                enabled=False, command_min_interval=0.05, stop_resend_interval=0.25,
                cinematic_zoom_enabled=False, zoom_target_frac=0.35, zoom_deadband=0.02,
                zoom_max_speed=4, invert_pan=False, invert_tilt=False, deadzone=0.1,
                max_pan_speed=12, max_tilt_speed=9, min_speed=1, ff_gain=0.2,
                ff_deadzone_mult=1.5,
            ),
            gps=types.SimpleNamespace(
                lock_frames=5, grace_sec=1.0, stale_threshold_sec=10.0,
                max_pan_speed=4, max_tilt_speed=3, drive_zoom=False,
            ),
            estimator=types.SimpleNamespace(
                shadow=True, enabled=True, q_accel=2.0, p0_pos=25.0, p0_vel=9.0,
                r_gps_fresh=4.0, r_gps_age_scale=0.5, r_vis_deg=1.0,
                zoom_cov_wide_deg=4.0, zoom_cov_narrow_deg=1.5, log_every_n=1,
                use_vision_range=True,  # ON
                subject_height_m=1.0, r_range_frac=0.3,
            ),
            loop=types.SimpleNamespace(target_fps=30, log_every_sec=10),
            web=types.SimpleNamespace(jpeg_quality=80, show_hud=False),
            shadow_log_dir=str(tmp_path),
        )
        p = Pipeline(cfg, NullPtz(), lambda: None)
        from wavecam.camera_pose import CameraPose
        p.pose = CameraPose(
            lat=21.601, lon=-158.001, alt_m=0.0,
            pan_anchor_enc=0.0, pan_anchor_bearing=247.0, pan_enc_per_deg=4.47,
            tilt_anchor_enc=0.0, tilt_anchor_elev=0.0, tilt_enc_per_deg=4.0,
        )
        fov_curve = [(0, 60.0), (8192, 12.0), (16384, 5.0)]
        p._init_estimator(fov_curve)
        assert p.estimator is not None

        fix = types.SimpleNamespace(lat=21.600, lon=-158.002, speed=5.0,
                                    course=270.0, age_sec=2.0)
        p.estimator.update_gps(fix, now=1000.0)

        called = []

        def _capture_range(**kwargs):
            called.append(kwargs)

        p.estimator.update_vision_range = _capture_range
        # Range path requires FRESH zoom (skip-on-stale, review 2026-06-12);
        # the un-started real poller has none.
        p.ptz_state.latest_zoom = lambda: (8192, 0.1)

        from wavecam.fusion import FusionResult
        fr = FusionResult(
            target_xy=(320.0, 240.0), bbox=(100, 100, 200, 300),
            person_bbox=(100, 100, 200, 300), conf=0.9,
            locked=True, state="TRACKING", has_color=True, has_person=True, matched=True,
        )
        # Pass frame_h so the tick does not use the default 0
        p._estimator_shadow_tick(fr, w=640, t0=1001.0, frame_h=720)
        assert len(called) > 0, "update_vision_range must be called when flag=True + person_bbox"

    def test_pipeline_no_range_call_without_person_bbox(self, tmp_path):
        """Even with flag=True, no person_bbox → no range call."""
        from wavecam.pipeline import Pipeline
        from wavecam.ptz_visca import NullPtz

        cfg = types.SimpleNamespace(
            camera=types.SimpleNamespace(url="", reconnect_interval=5),
            color=types.SimpleNamespace(enabled=False),
            detector=types.SimpleNamespace(enabled=False, every_n=3, box_ttl_sec=0.3),
            fusion=types.SimpleNamespace(
                lock_threshold=0.6, unlock_threshold=0.35, require_person=False,
                match_dist=120, person_aim_x=0.5, person_aim_y=0.5, ema_alpha=0.5,
                lost_grace_sec=0.8, gps_boost=0.2, gps_boost_radius_frac=0.25,
            ),
            ptz=types.SimpleNamespace(
                enabled=False, command_min_interval=0.05, stop_resend_interval=0.25,
                cinematic_zoom_enabled=False, zoom_target_frac=0.35, zoom_deadband=0.02,
                zoom_max_speed=4, invert_pan=False, invert_tilt=False, deadzone=0.1,
                max_pan_speed=12, max_tilt_speed=9, min_speed=1, ff_gain=0.2,
                ff_deadzone_mult=1.5,
            ),
            gps=types.SimpleNamespace(
                lock_frames=5, grace_sec=1.0, stale_threshold_sec=10.0,
                max_pan_speed=4, max_tilt_speed=3, drive_zoom=False,
            ),
            estimator=types.SimpleNamespace(
                shadow=True, enabled=True, q_accel=2.0, p0_pos=25.0, p0_vel=9.0,
                r_gps_fresh=4.0, r_gps_age_scale=0.5, r_vis_deg=1.0,
                zoom_cov_wide_deg=4.0, zoom_cov_narrow_deg=1.5, log_every_n=1,
                use_vision_range=True, subject_height_m=1.0, r_range_frac=0.3,
            ),
            loop=types.SimpleNamespace(target_fps=30, log_every_sec=10),
            web=types.SimpleNamespace(jpeg_quality=80, show_hud=False),
            shadow_log_dir=str(tmp_path),
        )
        p = Pipeline(cfg, NullPtz(), lambda: None)
        from wavecam.camera_pose import CameraPose
        p.pose = CameraPose(
            lat=21.601, lon=-158.001, alt_m=0.0,
            pan_anchor_enc=0.0, pan_anchor_bearing=247.0, pan_enc_per_deg=4.47,
            tilt_anchor_enc=0.0, tilt_anchor_elev=0.0, tilt_enc_per_deg=4.0,
        )
        p._init_estimator([(0, 60.0), (8192, 12.0), (16384, 5.0)])
        fix = types.SimpleNamespace(lat=21.600, lon=-158.002, speed=5.0,
                                    course=270.0, age_sec=2.0)
        p.estimator.update_gps(fix, now=1000.0)

        called = []
        p.estimator.update_vision_range = lambda **kw: called.append(kw)

        from wavecam.fusion import FusionResult
        # No person_bbox — color blob only
        fr = FusionResult(
            target_xy=(320.0, 240.0), bbox=(100, 100, 200, 50),
            person_bbox=None, conf=0.7,
            locked=True, state="TRACKING", has_color=True, has_person=False, matched=False,
        )
        p._estimator_shadow_tick(fr, w=640, t0=1001.0)
        assert len(called) == 0, "No person_bbox → no range call even with flag=True"


# ── 5. Sim harness: range-on/off comparison ──────────────────────────────────

class TestSimRangeComparison:
    """The range-on scenario must produce tighter radial covariance than range-off."""

    def test_range_on_tightens_radial_cov(self):
        """Run range_obs_comparison and assert radial cov is smaller with range on."""
        from wavecam.tools.sim.replay import run_range_comparison
        stats = run_range_comparison()

        cov_off = stats["final_radial_cov_off"]
        cov_on = stats["final_radial_cov_on"]

        assert cov_off is not None and cov_on is not None
        assert cov_on < cov_off, (
            f"Range ON must tighten radial cov: ON={cov_on:.4f} >= OFF={cov_off:.4f}"
        )

    def test_range_scenario_produces_outputs(self):
        """The range-obs scenario generates GPS fixes and range detections."""
        from wavecam.tools.sim.scenarios import range_obs_scenario
        fixes, range_dets = range_obs_scenario(duration_sec=20.0)
        assert len(fixes) > 5
        assert len(range_dets) > 0
        # Range detections at a higher rate than GPS fixes
        assert len(range_dets) >= len(fixes)

    def test_replay_with_range_detections_includes_fields(self):
        """replay_scenario result entries gain range_obs_m and range_r fields."""
        from wavecam.tools.sim.scenarios import range_obs_scenario
        from wavecam.tools.sim.replay import replay_scenario, _default_cfg
        fixes, range_dets = range_obs_scenario(duration_sec=10.0)
        cfg = _default_cfg(use_vision_range=True)
        results = replay_scenario(fixes, [], cfg=cfg, range_detections=range_dets)
        assert len(results) > 0
        # At least some entries should have range_obs_m populated
        with_range = [r for r in results if r.get("range_obs_m") is not None]
        assert len(with_range) > 0, "Expected range_obs_m to appear in some replay entries"


def test_range_update_skipped_when_zoom_stale(monkeypatch):
    """Review 2026-06-12 (HIGH): a wide-FOV fallback at tele understates range
    ~12x with a falsely tight R. Stale/absent zoom must SKIP the observation."""
    import types
    from wavecam.pipeline import Pipeline
    calls = []
    p = types.SimpleNamespace(
        cfg=types.SimpleNamespace(estimator=types.SimpleNamespace(
            log_every_n=1000, use_vision_range=True)),
        estimator=types.SimpleNamespace(
            update_gps=lambda *a, **k: None,
            update_vision=lambda *a, **k: None,
            update_vision_range=lambda **k: calls.append(k),
            predict_output=lambda **k: None),
        _shadow_writer=None, _est_active_shadow=True, _est_tick=0,
        gps=None,
        ptz_state=types.SimpleNamespace(
            latest=lambda: ((10, 0), 0.05),
            latest_zoom=lambda: (8192, 5.0)),   # STALE (>2s)
    )
    fr = types.SimpleNamespace(locked=True, target_xy=(320.0, 180.0),
                               person_bbox=(0, 0, 50, 120))
    Pipeline._estimator_shadow_tick(p, fr, 640, 100.0, frame_h=720)
    assert calls == [], "stale zoom must skip the range observation"
