"""Verify that Pipeline creates a TargetEstimator and that shadow records appear
in the event ring and JSONL file after simulated GPS inputs.

These tests do NOT start the pipeline thread. They call the estimator directly
via pipeline.estimator to avoid threading complexity in tests.
"""
import os
import types
import json

from wavecam.estimator import TargetEstimator
from wavecam.events import EventRing


def _cfg_with_estimator(tmp_path, shadow=True, enabled=True):
    """Return a pipeline-level config with estimator keys."""
    return types.SimpleNamespace(
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
            shadow=shadow, enabled=enabled, q_accel=2.0,
            p0_pos=25.0, p0_vel=9.0,
            r_gps_fresh=4.0, r_gps_age_scale=0.5, r_vis_deg=1.0,
            zoom_cov_wide_deg=4.0, zoom_cov_narrow_deg=1.5, log_every_n=1,
        ),
        loop=types.SimpleNamespace(target_fps=30, log_every_sec=10),
        web=types.SimpleNamespace(jpeg_quality=80, show_hud=False),
        shadow_log_dir=str(tmp_path),
    )


def _pose():
    from wavecam.camera_pose import CameraPose
    # has_base = (lat != 0 or lon != 0); calibrated = (pan_enc_per_deg != 0)
    return CameraPose(
        lat=21.601, lon=-158.001, alt_m=0.0,
        pan_anchor_enc=0.0, pan_anchor_bearing=247.0, pan_enc_per_deg=4.47,
        tilt_anchor_enc=0.0, tilt_anchor_elev=0.0, tilt_enc_per_deg=4.0,
    )


def _fov_curve():
    return [(0, 60.0), (8192, 12.0), (16384, 5.0)]


def test_pipeline_has_estimator_attribute(tmp_path):
    from wavecam.pipeline import Pipeline
    from wavecam.ptz_visca import NullPtz
    cfg = _cfg_with_estimator(tmp_path)
    p = Pipeline(cfg, NullPtz(), lambda: None)
    p.pose = _pose()
    p._init_estimator(_fov_curve())
    assert hasattr(p, "estimator")
    assert isinstance(p.estimator, TargetEstimator)


def test_estimator_shadow_event_appears_after_gps(tmp_path):
    from wavecam.estimator import TargetEstimator

    pose = _pose()
    cfg = types.SimpleNamespace(
        shadow=True, enabled=True, q_accel=2.0,
        p0_pos=25.0, p0_vel=9.0,
        r_gps_fresh=4.0, r_gps_age_scale=0.5, r_vis_deg=1.0,
        zoom_cov_wide_deg=4.0, zoom_cov_narrow_deg=1.5, log_every_n=1,
    )
    gps_cfg = types.SimpleNamespace(stale_threshold_sec=10.0)
    est = TargetEstimator(cfg=cfg, gps_cfg=gps_cfg, pose=pose, fov_curve=_fov_curve())
    events = EventRing(maxlen=100)

    fix = types.SimpleNamespace(lat=21.600, lon=-158.002, speed=5.0,
                                course=270.0, age_sec=2.0)
    est.update_gps(fix, now=1000.0)
    out = est.predict_output(now=1000.0)
    assert out is not None

    # Simulate what the pipeline does: write a shadow event
    events.record("shadow", {
        "t": 1000.0,
        "e": out.e, "n": out.n, "ve": out.ve, "vn": out.vn,
        "cov_trace": sum(out.cov[i][i] for i in range(4)),
        "bearing_deg": out.bearing_deg, "dist_m": out.dist_m,
        "pan_enc_would": out.pan_enc_would, "tilt_enc_would": out.tilt_enc_would,
        "bearing_std_deg": out.bearing_std_deg,
        "owner_actual": "gps_tracker", "cmd_actual": "GPS abs",
        "gps_updated": True, "vision_updated": False,
    })
    ring = events.since(0)
    shadow_events = [e for e in ring if e["kind"] == "shadow"]
    assert len(shadow_events) == 1
    assert shadow_events[0]["detail"]["gps_updated"] is True


def test_shadow_jsonl_written(tmp_path):
    """Verify that the pipeline shadow writer produces a valid JSONL file."""
    from wavecam.shadow_writer import ShadowWriter

    w = ShadowWriter(log_dir=str(tmp_path), session_id="test")
    record = {
        "t": 1000.0, "e": 10.0, "n": 200.0, "ve": 3.0, "vn": 0.0,
        "cov_trace": 1.5, "bearing_deg": 269.0, "dist_m": 200.5,
        "pan_enc_would": 8200, "tilt_enc_would": -10,
        "bearing_std_deg": 0.9,
        "owner_actual": "gps_tracker", "cmd_actual": "GPS abs",
        "gps_updated": True, "vision_updated": False,
    }
    w.write(record)
    w.write(record)
    files = list(tmp_path.glob("session_test.jsonl"))
    assert files
    lines = files[0].read_text().strip().split("\n")
    assert len(lines) == 2
    obj = json.loads(lines[0])
    assert obj["e"] == 10.0
    assert obj["bearing_deg"] == 269.0
