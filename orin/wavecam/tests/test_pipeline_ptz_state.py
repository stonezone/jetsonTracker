"""Verify that Pipeline wires up PtzState and exposes it correctly."""
import types, threading
from unittest.mock import MagicMock
# Import the minimal pipeline fixture pattern from the existing test suite:
# DummyPipeline is defined in test_control_api.py; here we test the real
# Pipeline class with a null PTZ so we can inspect ptz_state after init.
from wavecam.pipeline import Pipeline
from wavecam.ptz_state import PtzState


def _null_cfg():
    """Minimal cfg that satisfies Pipeline.__init__ without a real camera."""
    ptz_cfg = types.SimpleNamespace(
        enabled=False, command_min_interval=0.05,
        stop_resend_interval=0.25, cinematic_zoom_enabled=False,
        zoom_target_frac=0.35, zoom_deadband=0.02, zoom_max_speed=4,
        invert_pan=False, invert_tilt=False, deadzone=0.1,
        max_pan_speed=12, max_tilt_speed=9, min_speed=1, ff_gain=0.2,
        ff_deadzone_mult=1.5,
    )
    return types.SimpleNamespace(
        camera=types.SimpleNamespace(url="", reconnect_interval=5),
        color=types.SimpleNamespace(enabled=False),
        detector=types.SimpleNamespace(enabled=False, every_n=3, box_ttl_sec=0.3),
        fusion=types.SimpleNamespace(
            lock_threshold=0.6, unlock_threshold=0.35, require_person=False,
            match_dist=120, person_aim_x=0.5, person_aim_y=0.5, ema_alpha=0.5,
            lost_grace_sec=0.8, gps_boost=0.2, gps_boost_radius_frac=0.25,
        ),
        ptz=ptz_cfg,
        gps=types.SimpleNamespace(
            lock_frames=5, grace_sec=1.0, stale_threshold_sec=10.0,
            max_pan_speed=4, max_tilt_speed=3, drive_zoom=False,
        ),
        loop=types.SimpleNamespace(target_fps=30, log_every_sec=10),
        web=types.SimpleNamespace(jpeg_quality=80, show_hud=True),
    )


def _null_ptz():
    from wavecam.ptz_visca import NullPtz
    return NullPtz()


def test_pipeline_has_ptz_state_after_init():
    p = Pipeline(_null_cfg(), _null_ptz(), lambda: None)
    assert hasattr(p, "ptz_state"), "Pipeline must have a ptz_state attribute"
    assert isinstance(p.ptz_state, PtzState)


def test_ptz_state_latest_returns_none_before_start():
    p = Pipeline(_null_cfg(), _null_ptz(), lambda: None)
    enc, age = p.ptz_state.latest()
    assert enc is None and age is None


def test_ptz_state_not_started_when_ptz_disabled():
    p = Pipeline(_null_cfg(), _null_ptz(), lambda: None)
    # disabled PTZ — poller exists but its thread must NOT auto-start
    assert not p.ptz_state.is_alive()
