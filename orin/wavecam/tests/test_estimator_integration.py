"""End-to-end integration: GPS fix → estimator → shadow event → JSONL.

No camera, no pipeline thread. Drives the estimator, shadow writer, and event
ring directly in the same pattern the pipeline loop uses. Asserts the full
signal chain from measurement input to persisted shadow record.
"""
import json
import os
import types
import time

from wavecam.estimator import TargetEstimator, EstimatorOutput
from wavecam.events import EventRing
from wavecam.shadow_writer import ShadowWriter


def _cfg():
    return types.SimpleNamespace(
        shadow=True, enabled=True, q_accel=2.0,
        p0_pos=25.0, p0_vel=9.0,
        r_gps_fresh=4.0, r_gps_age_scale=0.5, r_vis_deg=1.0,
        zoom_cov_wide_deg=4.0, zoom_cov_narrow_deg=1.5, log_every_n=1,
    )


def _pose():
    class _P:
        lat = 21.601; lon = -158.001; alt_m = 0.0
        has_base = True; calibrated = True
        pan_anchor_enc = 0.0; pan_anchor_bearing = 247.0; pan_enc_per_deg = 4.47
        tilt_anchor_enc = 0.0; tilt_anchor_elev = 0.0; tilt_enc_per_deg = 4.0
        def bearing_to_pan_encoder(self, b):
            return self.pan_anchor_enc + (b - self.pan_anchor_bearing) * self.pan_enc_per_deg
        def pan_encoder_to_bearing(self, enc):
            return self.pan_anchor_bearing + (enc - self.pan_anchor_enc) / self.pan_enc_per_deg
        def elevation_to_tilt_encoder(self, e):
            return self.tilt_anchor_enc + e * self.tilt_enc_per_deg
    return _P()


def _fov():
    return [(0, 60.0), (8192, 12.0), (16384, 5.0)]


def _fix(lat=21.600, lon=-158.002, age_sec=2.0):
    return types.SimpleNamespace(lat=lat, lon=lon, speed=5.0, course=270.0, age_sec=age_sec)


def test_full_chain_gps_to_jsonl(tmp_path):
    est = TargetEstimator(cfg=_cfg(), gps_cfg=types.SimpleNamespace(stale_threshold_sec=10.0),
                          pose=_pose(), fov_curve=_fov())
    events = EventRing(maxlen=100)
    writer = ShadowWriter(log_dir=str(tmp_path), session_id="integ")

    fix = _fix()
    est.update_gps(fix, now=1000.0)
    out = est.predict_output(now=1000.0)
    assert out is not None

    record = {
        "t": 1000.0,
        "e": round(out.e, 2), "n": round(out.n, 2),
        "cov_trace": round(sum(out.cov[i][i] for i in range(4)), 4),
        "bearing_deg": round(out.bearing_deg, 2), "dist_m": round(out.dist_m, 1),
        "pan_enc_would": out.pan_enc_would, "tilt_enc_would": out.tilt_enc_would,
        "bearing_std_deg": round(out.bearing_std_deg, 3),
        "owner_actual": "gps_tracker", "cmd_actual": "GPS abs",
        "gps_updated": True, "vision_updated": False,
    }
    events.record("shadow", record)
    writer.write(record)
    writer.close()

    # Event ring has the record
    shadow_events = [e for e in events.since(0) if e["kind"] == "shadow"]
    assert len(shadow_events) == 1

    # JSONL file has the record
    files = list(tmp_path.glob("session_integ.jsonl"))
    assert files
    obj = json.loads(files[0].read_text().strip())
    assert obj["bearing_deg"] == record["bearing_deg"]
    assert obj["gps_updated"] is True


def test_multiple_fixes_velocity_plausible(tmp_path):
    """After two GPS fixes separated in time and space, velocity estimate should be
    in the right order of magnitude for surf-speed motion."""
    import math
    est = TargetEstimator(cfg=_cfg(), gps_cfg=types.SimpleNamespace(stale_threshold_sec=10.0),
                          pose=_pose(), fov_curve=_fov())
    fix1 = _fix(lat=21.600, lon=-158.000)
    fix2 = _fix(lat=21.600, lon=-158.002)   # ~177 m west
    est.update_gps(fix1, now=1000.0)
    est.update_gps(fix2, now=1020.0)         # 20 seconds later
    out = est.predict_output(now=1020.0)
    speed = math.hypot(out.ve, out.vn)
    # ~177m / 20s ≈ 8.9 m/s; loose bounds for the Kalman lag
    assert 2.0 < speed < 20.0


def test_vision_update_does_not_diverge(tmp_path):
    """A vision bearing observation consistent with the GPS state must not make
    the covariance explode."""
    est = TargetEstimator(cfg=_cfg(), gps_cfg=types.SimpleNamespace(stale_threshold_sec=10.0),
                          pose=_pose(), fov_curve=_fov())
    fix = _fix(lat=21.600, lon=-158.002)
    est.update_gps(fix, now=1000.0)
    out_before = est.predict_output(now=1000.0)
    cov_before = sum(out_before.cov[i][i] for i in range(4))

    # Vision observation: pan encoder roughly pointing at subject
    pan_enc_approx = int(_pose().bearing_to_pan_encoder(out_before.bearing_deg))
    est.update_vision(pan_enc=pan_enc_approx, pixel_cx=320.0, frame_w=640,
                      zoom_enc=0, now=1001.0)
    out_after = est.predict_output(now=1001.0)
    cov_after = sum(out_after.cov[i][i] for i in range(4))

    # Covariance must not have grown by more than 10× (divergence indicator)
    assert cov_after < cov_before * 10.0


def test_stale_gps_does_not_update_state(tmp_path):
    est = TargetEstimator(cfg=_cfg(), gps_cfg=types.SimpleNamespace(stale_threshold_sec=10.0),
                          pose=_pose(), fov_curve=_fov())
    fix_fresh = _fix(age_sec=2.0)
    fix_stale = _fix(age_sec=15.0, lat=21.601, lon=-158.010)   # very far, stale
    est.update_gps(fix_fresh, now=1000.0)
    out_before = est.predict_output(now=1000.0)

    est.update_gps(fix_stale, now=1001.0)
    out_after = est.predict_output(now=1001.0)

    # Position should not have jumped to the stale fix's location (~900 m away)
    import math
    delta = math.hypot(out_after.e - out_before.e, out_after.n - out_before.n)
    assert delta < 50.0   # velocity-drift in 1s only; stale obs was skipped


def test_pan_encoder_to_bearing_real_pose_roundtrip():
    """Regression: estimator.update_vision calls pose.pan_encoder_to_bearing,
    which test fakes provided but the REAL CameraPose lacked — first locked
    frame with live encoders killed the vision loop (2026-06-11). Pin the
    inverse on the real class, round-tripped against its forward mapping."""
    from wavecam.camera_pose import CameraPose, PRISUAL_PAN_ENC_PER_DEG
    p = CameraPose()
    assert p.pan_encoder_to_bearing(123) is None          # uncalibrated -> None
    p.calibrate_pan_aim(enc=-246.0, bearing_deg=101.7,
                        enc_per_deg=PRISUAL_PAN_ENC_PER_DEG)
    for bearing in (0.0, 101.7, 245.5, 359.0):
        enc = p.bearing_to_pan_encoder(bearing)
        back = p.pan_encoder_to_bearing(enc)
        assert abs((back - bearing + 180) % 360 - 180) < 1e-6, bearing


def test_update_vision_against_real_camera_pose():
    """The integration fakes must never again hide a missing CameraPose method:
    drive update_vision with the genuine class."""
    from wavecam.camera_pose import CameraPose, PRISUAL_PAN_ENC_PER_DEG
    pose = CameraPose()
    pose.lat, pose.lon = 21.6451, -158.0501
    pose.calibrate_pan_aim(enc=0.0, bearing_deg=0.0,
                           enc_per_deg=PRISUAL_PAN_ENC_PER_DEG)
    import types
    est = TargetEstimator(cfg=_cfg(),
                          gps_cfg=types.SimpleNamespace(stale_threshold_sec=10.0),
                          pose=pose, fov_curve=[(0, 63.7)])
    fix = _fix(lat=21.6460, lon=-158.0501, age_sec=0.5)   # ~100m north
    est.update_gps(fix, now=100.0)
    est.update_vision(pan_enc=10, pixel_cx=320.0, frame_w=640, zoom_enc=0,
                      now=100.1)                           # must not raise
    out = est.predict_output(now=100.2)
    assert out is not None


def test_shadow_tick_failure_disables_shadow_not_loop():
    """A raising estimator must disable shadow and leave the loop alive."""
    import types
    from wavecam.pipeline import Pipeline

    class _Boom:
        def update_gps(self, *a, **k):
            raise RuntimeError("boom")

    p = types.SimpleNamespace(
        cfg=types.SimpleNamespace(estimator=types.SimpleNamespace(log_every_n=3)),
        estimator=_Boom(),
        _shadow_writer=object(),
        _est_active_shadow=True,
        _est_tick=0,
        gps=types.SimpleNamespace(get_fix=lambda: object()),
    )
    fr = types.SimpleNamespace(locked=False, target_xy=None)
    Pipeline._estimator_shadow_tick(p, fr, 640, 100.0)    # must NOT raise
    assert p.estimator is None
    assert p._shadow_writer is None
    assert p._est_active_shadow is False
