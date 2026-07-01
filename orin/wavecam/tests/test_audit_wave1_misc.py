"""Audit 2026-07-01 Wave-1 medium/low fixes (M1, M3, M4, L9, L12).

  M1  the estimator must not re-fuse the same cached ~1 Hz GPS fix at 35 fps
  M3  the vision BEARING update skips on a stale zoom cache (like the range one)
  M4  _scalar_update applies the full (I - K h) P covariance form, symmetric
  L9  _gps_cue falls back to the center cue on stale encoder/zoom caches
  L12 TrackingArbiter.max_gps_age_sec (stored, never read) is gone
"""
from __future__ import annotations

import types

from wavecam.pipeline import Pipeline


# --- M1: GPS fix dedupe in the shadow tick ------------------------------------

def _tick_pipe(get_fix, est_stub, latest=((10, 0), 0.05), latest_zoom=(0, 0.1)):
    return types.SimpleNamespace(
        cfg=types.SimpleNamespace(estimator=types.SimpleNamespace(
            log_every_n=1000, use_vision_range=False)),
        estimator=est_stub,
        _shadow_writer=None, _est_active_shadow=True, _est_tick=0,
        gps=types.SimpleNamespace(get_fix=get_fix),
        ptz_state=types.SimpleNamespace(latest=lambda: latest,
                                        latest_zoom=lambda: latest_zoom),
    )


def _est_stub(gps_calls=None, vision_calls=None):
    return types.SimpleNamespace(
        update_gps=lambda *a, **k: (gps_calls is not None) and gps_calls.append(k),
        update_vision=lambda **k: (vision_calls is not None) and vision_calls.append(k),
        update_vision_range=lambda **k: None,
        predict_output=lambda **k: None,
    )


def test_shadow_tick_fuses_each_fix_once():
    """The reader hands back the SAME fix until the next LoRa packet; re-fusing
    it every frame collapses covariance between fixes (M1)."""
    gps_calls = []
    fix_holder = [types.SimpleNamespace(lat=1.0, lon=1.0, age_sec=0.5, ts=100.0)]
    p = _tick_pipe(lambda: fix_holder[0], _est_stub(gps_calls=gps_calls))
    fr = types.SimpleNamespace(locked=False, target_xy=None, person_bbox=None)

    for i in range(5):
        Pipeline._estimator_shadow_tick(p, fr, 640, 100.0 + i * 0.03)
    assert len(gps_calls) == 1, "an unchanged fix.ts must be fused exactly once"

    fix_holder[0] = types.SimpleNamespace(lat=1.0, lon=1.0, age_sec=0.1, ts=101.0)
    Pipeline._estimator_shadow_tick(p, fr, 640, 100.2)
    assert len(gps_calls) == 2, "a new fix.ts must be fused"


def test_shadow_tick_without_ts_keeps_fusing():
    """Fixes lacking .ts (test fakes / exotic sources) keep the old per-tick
    behavior rather than being silently dropped after the first frame."""
    gps_calls = []
    fix = types.SimpleNamespace(lat=1.0, lon=1.0, age_sec=0.5)   # no ts
    p = _tick_pipe(lambda: fix, _est_stub(gps_calls=gps_calls))
    fr = types.SimpleNamespace(locked=False, target_xy=None, person_bbox=None)
    Pipeline._estimator_shadow_tick(p, fr, 640, 100.0)
    Pipeline._estimator_shadow_tick(p, fr, 640, 100.1)
    assert len(gps_calls) == 2


# --- M3: vision bearing update requires a fresh zoom ---------------------------

def test_vision_bearing_update_skipped_when_zoom_stale():
    vision_calls = []
    p = _tick_pipe(lambda: None, _est_stub(vision_calls=vision_calls),
                   latest_zoom=(8192, 5.0))    # STALE (> ZOOM_FRESH_SEC)
    fr = types.SimpleNamespace(locked=True, target_xy=(320.0, 180.0),
                               person_bbox=None)
    Pipeline._estimator_shadow_tick(p, fr, 640, 100.0)
    assert vision_calls == [], \
        "a stale zoom cache must skip the bearing update, not assume wide FOV"


def test_vision_bearing_update_runs_with_fresh_zoom():
    vision_calls = []
    p = _tick_pipe(lambda: None, _est_stub(vision_calls=vision_calls),
                   latest_zoom=(8192, 0.1))    # fresh
    fr = types.SimpleNamespace(locked=True, target_xy=(320.0, 180.0),
                               person_bbox=None)
    Pipeline._estimator_shadow_tick(p, fr, 640, 100.0)
    assert len(vision_calls) == 1
    assert vision_calls[0]["zoom_enc"] == 8192


# --- M4: full (I - Kh)P covariance update -------------------------------------

def _full_update(P, h, r_var):
    """Reference implementation: (I - K h) P with symmetric input P."""
    Pht = [sum(P[i][j] * h[j] for j in range(4)) for i in range(4)]
    S = sum(h[j] * Pht[j] for j in range(4)) + r_var
    K = [Pht[i] / S for i in range(4)]
    return [[sum(((1.0 if i == k else 0.0) - K[i] * h[k]) * P[k][j]
                 for k in range(4)) for j in range(4)] for i in range(4)]


def _bare_estimator():
    from wavecam.estimator import TargetEstimator
    cfg = types.SimpleNamespace(enabled=True, shadow=False, q_accel=2.0,
                                p0_pos=25.0, p0_vel=9.0, r_gps_fresh=4.0,
                                r_gps_age_scale=0.5, r_vis_deg=1.0)
    gps_cfg = types.SimpleNamespace(stale_threshold_sec=10.0)
    return TargetEstimator(cfg=cfg, gps_cfg=gps_cfg, pose=None, fov_curve=[])


def test_scalar_update_matches_full_ikh_form_and_stays_symmetric():
    from wavecam.estimator import _mat, _mat_to_list
    est = _bare_estimator()
    est._initialised = True
    # non-diagonal P: exposes the cross-covariance terms the old
    # diagonal-only form dropped
    P0 = [[25.0, 3.0, 2.0, 0.5],
          [3.0, 20.0, 1.0, 2.0],
          [2.0, 1.0, 9.0, 0.7],
          [0.5, 2.0, 0.7, 8.0]]
    est._P = _mat([row[:] for row in P0])
    est._x = [100.0, 50.0, 1.0, 0.5]

    h = [0.6, -0.8, 0.0, 0.0]
    est._scalar_update(h, innovation=2.0, r_var=1.5)

    got = _mat_to_list(est._P)
    want = _full_update(P0, h, 1.5)
    for i in range(4):
        for j in range(4):
            assert abs(got[i][j] - want[i][j]) < 1e-9, f"P[{i}][{j}] wrong"
            assert abs(got[i][j] - got[j][i]) < 1e-12, "P must stay symmetric"


# --- L9: _gps_cue freshness gates ----------------------------------------------

def _cue_pipe(latest, latest_zoom):
    from wavecam.camera_pose import CameraPose
    p = Pipeline.__new__(Pipeline)
    p.cfg = types.SimpleNamespace(fusion=types.SimpleNamespace(
        gps_boost_radius_frac=0.25,
        gps_bearing_cue_enabled=True,
        gps_bearing_cue_uncertainty_deg=5.0,
        gps_bearing_cue_max_offscreen_deg=10.0,
    ))
    pose = CameraPose(lat=1.0, lon=1.0)
    pose.calibrate_pan_aim(enc=0.0, bearing_deg=0.0, enc_per_deg=14.4)
    p.pose = pose
    p.gps = types.SimpleNamespace(
        get_fix=lambda: types.SimpleNamespace(lat=2.0, lon=1.0))
    p._store = types.SimpleNamespace(fov_curve=[(0, 60.0), (16384, 5.0)])
    p.ptz_state = types.SimpleNamespace(latest=lambda: latest,
                                        latest_zoom=lambda: latest_zoom)
    p._last_gps_cue = None
    return p


W, H = 640, 480
CENTER = (W / 2.0, H / 2.0, 0.25 * min(W, H))


def test_gps_cue_stale_encoder_falls_back_to_center():
    p = _cue_pipe(latest=((0, 0), 5.0), latest_zoom=(0, 0.0))   # enc 5 s old
    assert p._gps_cue(W, H) == CENTER


def test_gps_cue_stale_zoom_falls_back_to_center():
    p = _cue_pipe(latest=((0, 0), 0.0), latest_zoom=(0, 5.0))   # zoom 5 s old
    assert p._gps_cue(W, H) == CENTER


def test_gps_cue_fresh_caches_still_project():
    p = _cue_pipe(latest=((0, 0), 0.0), latest_zoom=(0, 0.1))
    cue = p._gps_cue(W, H)
    assert cue is not None and cue != CENTER   # bearing-projected, not fallback


# --- L12: dead arbiter field removed --------------------------------------------

def test_arbiter_has_no_dead_max_gps_age_field():
    from wavecam.tracking_arbiter import TrackingArbiter
    a = TrackingArbiter()
    assert not hasattr(a, "max_gps_age_sec"), \
        "staleness is the caller's gps_fresh input; the stored field was never read"
