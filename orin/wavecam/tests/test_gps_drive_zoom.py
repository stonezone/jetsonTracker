"""Phase 4: GPS-driven zoom now reads its curve from GpsCfg (was hardcoded) and
defaults to a conservative max_frac. No torch required.

NOTE: the zoom curve itself (gps_pointing.ZoomCurve / distance_to_zoom_encoder) and
its wiring into _gps_pointing_cmd already existed; this phase makes it configurable +
conservative. DeepSeek's PR #98 DriveZoom was a parallel reimplementation and is
superseded; its rate-limit/min_enc remain a possible follow-up on this curve.
"""
from __future__ import annotations
import sys
import types

sys.modules.setdefault("cv2", types.SimpleNamespace())

from wavecam.camera_pose import CameraPose
from wavecam.config import GpsCfg
from wavecam.pipeline import Pipeline


def _gps_cfg(drive_zoom=True, **kw):
    cfg = GpsCfg()
    cfg.drive_zoom = drive_zoom
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def _pipe(drive_zoom=True, **kw):
    p = Pipeline.__new__(Pipeline)
    p.cfg = types.SimpleNamespace(gps=_gps_cfg(drive_zoom, **kw))
    pose = CameraPose(lat=1.0, lon=1.0, alt_m=0.0)
    pose.calibrate_pan_aim(enc=0.0, bearing_deg=0.0, enc_per_deg=14.4)
    p.pose = pose
    p.gps = None
    return p


def _fix(lat=2.0, lon=1.0):
    # default ~111 km north — well beyond far_m so the curve saturates at max
    return types.SimpleNamespace(lat=lat, lon=lon, speed=0.0, course=0.0)


def test_conservative_default_max_frac():
    assert GpsCfg().drive_zoom_max_frac <= 0.6


def test_drive_zoom_off_leaves_zoom_unset():
    cmd = _pipe(drive_zoom=False)._gps_pointing_cmd(_fix(), calibration_valid=True)
    assert cmd is not None
    assert cmd.zoom_enc is None


def test_drive_zoom_on_caps_at_config_max_frac():
    cmd = _pipe(drive_zoom=True, drive_zoom_max_frac=0.6,
                drive_zoom_max_enc=16384.0)._gps_pointing_cmd(_fix(), calibration_valid=True)
    assert cmd is not None
    assert cmd.zoom_enc == int(0.6 * 16384.0)  # far target saturates at the conservative cap


def test_drive_zoom_respects_lower_cap():
    cmd = _pipe(drive_zoom=True, drive_zoom_max_frac=0.35,
                drive_zoom_max_enc=16384.0)._gps_pointing_cmd(_fix(), calibration_valid=True)
    assert cmd.zoom_enc == int(0.35 * 16384.0)


def test_invalid_calibration_blocks_command():
    assert _pipe(drive_zoom=True)._gps_pointing_cmd(_fix(), calibration_valid=False) is None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print("GPS DRIVE ZOOM TESTS PASSED (%d)" % len(fns))
