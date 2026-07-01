"""H6 (audit 2026-07-01): GPS pointing lead must track the fix's actual age.

A fixed 0.65 s lead with a drive gate admitting fixes up to 8 s old aimed
~60 m behind an 8 m/s foiler — several FOV-widths at tele, so GPS coarse-point
placed vision somewhere it could never re-acquire. Now:

    lead_s = min(fix.age_sec + gps.lead_margin_s, gps.lead_cap_s)

with both knobs hot-configurable and in the /config snapshot; predict_lead
keeps its >=0.1 m/s speed gate (a stationary fix never extrapolates).
"""
from __future__ import annotations

import types

from wavecam.config import GpsCfg
from wavecam.gps_geo import GeoPoint
from wavecam.gps_pointing import compute_target
from wavecam.gps_stub import NormalizedFix
from wavecam.pipeline import Pipeline


BASE_LAT, BASE_LON = 21.6, -158.0


def _pipe(lead_margin_s=None, lead_cap_s=None):
    pipe = Pipeline.__new__(Pipeline)
    gps_kw = {"max_pan_speed": 4, "max_tilt_speed": 3, "drive_zoom": False}
    if lead_margin_s is not None:
        gps_kw["lead_margin_s"] = lead_margin_s
    if lead_cap_s is not None:
        gps_kw["lead_cap_s"] = lead_cap_s
    pipe.cfg = types.SimpleNamespace(
        ptz=types.SimpleNamespace(enabled=False, command_min_interval=0.0),
        gps=types.SimpleNamespace(**gps_kw),
    )
    from wavecam.camera_pose import CameraPose
    pose = CameraPose(lat=BASE_LAT, lon=BASE_LON, alt_m=2.0)
    pose.calibrate_pan_aim(enc=0.0, bearing_deg=0.0, enc_per_deg=14.4)
    pipe.pose = pose
    pipe.gps = None
    pipe._last_abs_cmd_key = None
    pipe._last_abs_cmd_time = 0.0
    return pipe


def _fix(age_sec, speed=8.0, course=90.0):
    # target ~110 m north of base, moving east — lead rotates the bearing
    return NormalizedFix(lat=BASE_LAT + 0.001, lon=BASE_LON, course=course,
                         speed=speed, ts=1000.0, age_sec=age_sec, src="lora")


def _expected_pan_enc(pipe, fix, lead_s):
    base = GeoPoint(lat=pipe.pose.lat, lon=pipe.pose.lon, alt_m=pipe.pose.alt_m)
    target = GeoPoint(lat=fix.lat, lon=fix.lon,
                      speed_mps=fix.speed, course_deg=fix.course)
    return int(compute_target(base, target, pipe.pose, lead_s=lead_s).pan_enc)


def test_defaults_pinned():
    cfg = GpsCfg()
    assert cfg.lead_margin_s == 0.65
    assert cfg.lead_cap_s == 4.0


def test_lead_uses_fix_age_plus_margin():
    """Pinning test: a 3 s-old fix leads by 3.65 s, NOT the old fixed 0.65 s."""
    pipe = _pipe()
    cmd = pipe._gps_pointing_cmd(_fix(age_sec=3.0), calibration_valid=True)
    assert cmd is not None
    assert cmd.pan_enc == _expected_pan_enc(pipe, _fix(3.0), lead_s=3.65)
    assert cmd.pan_enc != _expected_pan_enc(pipe, _fix(3.0), lead_s=0.65), \
        "an aged fix must lead farther than the old fixed 0.65 s"


def test_lead_capped():
    """Age 5 s + 0.65 margin would be 5.65 s — capped at lead_cap_s=4.0 to
    bound course-extrapolation error."""
    pipe = _pipe()
    cmd = pipe._gps_pointing_cmd(_fix(age_sec=5.0), calibration_valid=True)
    assert cmd.pan_enc == _expected_pan_enc(pipe, _fix(5.0), lead_s=4.0)
    assert cmd.pan_enc != _expected_pan_enc(pipe, _fix(5.0), lead_s=5.65)


def test_lead_knobs_configurable():
    pipe = _pipe(lead_margin_s=1.0, lead_cap_s=10.0)
    cmd = pipe._gps_pointing_cmd(_fix(age_sec=5.0), calibration_valid=True)
    assert cmd.pan_enc == _expected_pan_enc(pipe, _fix(5.0), lead_s=6.0)


def test_speed_gate_survives():
    """predict_lead's >=0.1 m/s gate: a near-stationary fix never extrapolates,
    no matter its age (GPS jitter must not become a phantom heading)."""
    pipe = _pipe()
    slow = pipe._gps_pointing_cmd(_fix(age_sec=6.0, speed=0.05),
                                  calibration_valid=True)
    assert slow.pan_enc == _expected_pan_enc(pipe, _fix(6.0, speed=0.05),
                                             lead_s=0.0)


# --- hot-config + snapshot plumbing ------------------------------------------

def test_lead_keys_registered_hot():
    from wavecam.control_utils import HOT_CONFIG_KEYS
    assert "gps.lead_margin_s" in HOT_CONFIG_KEYS
    assert "gps.lead_cap_s" in HOT_CONFIG_KEYS


def test_lead_keys_apply_and_reject_out_of_range():
    from types import SimpleNamespace
    from wavecam.control_config import ConfigManager

    gps_cfg = GpsCfg()

    class FakePipeline:
        cfg = SimpleNamespace(gps=gps_cfg,
                              fusion=SimpleNamespace(lock_threshold=0.6,
                                                     unlock_threshold=0.35),
                              ptz=SimpleNamespace())
        arbiter = None
        color = None
        state = SimpleNamespace()

    class FakeApi:
        revision = 0

        def refusal(self, code, msg, status=422):
            return {"error": code, "message": msg}

    mgr = ConfigManager(FakePipeline(), FakeApi())
    assert mgr.apply_hot_key("gps.lead_margin_s", 1.5) is None
    assert gps_cfg.lead_margin_s == 1.5
    assert mgr.apply_hot_key("gps.lead_cap_s", 6.0) is None
    assert gps_cfg.lead_cap_s == 6.0
    assert mgr.apply_hot_key("gps.lead_margin_s", -0.1) is not None
    assert mgr.apply_hot_key("gps.lead_cap_s", 20.0) is not None


def test_lead_keys_in_config_snapshot():
    from types import SimpleNamespace
    from wavecam.config import (CameraAiCfg, CameraCfg, ColorCfg, Config,
                                DetectorCfg, FusionCfg, LoopCfg, PtzCfg, WebCfg)
    from wavecam.control_snapshots import build_config_snapshot

    cfg = Config(camera=CameraCfg(), ptz=PtzCfg(), camera_ai=CameraAiCfg(),
                 color=ColorCfg(), detector=DetectorCfg(), fusion=FusionCfg(),
                 web=WebCfg(), loop=LoopCfg())
    pipeline = SimpleNamespace(cfg=cfg,
                               state=SimpleNamespace(show_mask=True, show_hud=True))
    snap = build_config_snapshot(pipeline, revision=1)
    assert snap["current"]["gps"]["lead_margin_s"] == 0.65
    assert snap["current"]["gps"]["lead_cap_s"] == 4.0
