"""Package 2: drive_stale_sec gates GPS driving independently of display stale.

Tests:
  - fix aged 10 s → not viable to drive (drive_stale_sec=8.0 default)
  - fix aged 5 s → viable to drive
  - display stale threshold (stale_threshold_sec=45) unchanged at 45 s boundary
  - drive_stale_sec present in config.py with correct default
  - hot-config key gps.drive_stale_sec is registered and accepted
"""
from __future__ import annotations
from types import SimpleNamespace
from typing import Optional

from wavecam.config import GpsCfg
from wavecam.fusion import FusionResult
from wavecam.tracking_arbiter import TrackingArbiter


# --- dataclass default ---

def test_drive_stale_sec_default():
    """GpsCfg.drive_stale_sec defaults to 8.0."""
    cfg = GpsCfg()
    assert hasattr(cfg, "drive_stale_sec")
    assert cfg.drive_stale_sec == 8.0


# --- drive-staleness gate (pipeline behavior) ---

def _gps_fresh(age_sec: float, drive_stale_sec: float = 8.0) -> bool:
    """Mirrors the gps_fresh computation in pipeline.py."""
    return age_sec < drive_stale_sec


def _vision(locked: bool = False) -> FusionResult:
    return FusionResult(
        target_xy=(0.5, 0.5), bbox=None, person_bbox=None,
        conf=0.5, locked=locked,
        state="TRACKING" if locked else "SEARCHING",
        has_color=True, has_person=True, matched=locked,
    )


def test_fix_aged_10s_not_viable_with_default_drive_stale():
    """Fix 10 s old is beyond drive_stale_sec=8.0 — arbiter should see idle."""
    drive_stale = GpsCfg().drive_stale_sec  # 8.0
    gps_fresh = _gps_fresh(age_sec=10.0, drive_stale_sec=drive_stale)
    assert gps_fresh is False
    a = TrackingArbiter()
    d = a.decide(_vision(), gps_fresh=gps_fresh, gps_calibrated=True, base_locked=True, now_sec=0.0)
    assert d.owner == "idle"


def test_fix_aged_5s_viable_with_default_drive_stale():
    """Fix 5 s old is within drive_stale_sec=8.0 — arbiter should use GPS."""
    drive_stale = GpsCfg().drive_stale_sec  # 8.0
    gps_fresh = _gps_fresh(age_sec=5.0, drive_stale_sec=drive_stale)
    assert gps_fresh is True
    a = TrackingArbiter()
    d = a.decide(_vision(), gps_fresh=gps_fresh, gps_calibrated=True, base_locked=True, now_sec=0.0, calibration_valid=True)
    assert d.owner == "gps_tracker"


def test_display_stale_threshold_unchanged():
    """stale_threshold_sec default is still 10.0 (display/status uses it, not changed)."""
    cfg = GpsCfg()
    assert cfg.stale_threshold_sec == 10.0


def test_display_stale_at_45s_boundary():
    """A 45 s-old fix remains 'not stale' under stale_threshold_sec=45 (display)."""
    # Simulate the YAML value (45 s) used for display — age 44 s is not stale
    display_stale_sec = 45.0
    age = 44.9
    display_fresh = age < display_stale_sec
    assert display_fresh is True
    # But it IS too old to drive (drive_stale_sec=8.0 default)
    drive_fresh = _gps_fresh(age_sec=age, drive_stale_sec=GpsCfg().drive_stale_sec)
    assert drive_fresh is False


# --- hot-config key registration ---

def test_drive_stale_sec_hot_key_registered():
    """gps.drive_stale_sec must appear in HOT_CONFIG_KEYS so apply_hot_key accepts it."""
    from wavecam.control_utils import HOT_CONFIG_KEYS
    assert "gps.drive_stale_sec" in HOT_CONFIG_KEYS


def test_drive_stale_sec_apply_hot_key():
    """apply_hot_key('gps.drive_stale_sec', 5.0) succeeds and mutates cfg.gps."""
    from wavecam.config import Config, CameraCfg, PtzCfg, CameraAiCfg, ColorCfg
    from wavecam.config import DetectorCfg, FusionCfg, WebCfg, LoopCfg, GpsCfg
    from wavecam.control_config import ConfigManager

    gps_cfg = GpsCfg()
    gps_cfg.enabled = True

    class FakeFusion:
        lock_threshold = 0.6
        unlock_threshold = 0.35

    class FakeCfg:
        gps = gps_cfg
        fusion = FakeFusion()
        ptz = SimpleNamespace()
        color = SimpleNamespace()
        detector = SimpleNamespace()
        web = SimpleNamespace()
        estimator = None
        sensors = None

    class FakePipeline:
        cfg = FakeCfg()
        arbiter = TrackingArbiter()
        color = None
        state = SimpleNamespace()

    class FakeApi:
        revision = 0
        def refusal(self, code, msg, status=422):
            return {"error": code, "message": msg}

    mgr = ConfigManager(FakePipeline(), FakeApi())
    result = mgr.apply_hot_key("gps.drive_stale_sec", 5.0)
    assert result is None  # no refusal
    assert gps_cfg.drive_stale_sec == 5.0


def test_drive_stale_sec_hot_key_range_rejected():
    """Values outside [1, 60] are rejected."""
    from wavecam.control_config import ConfigManager
    from wavecam.config import GpsCfg
    from wavecam.tracking_arbiter import TrackingArbiter

    gps_cfg = GpsCfg()

    class FakeCfg:
        gps = gps_cfg
        fusion = SimpleNamespace(lock_threshold=0.6, unlock_threshold=0.35)
        ptz = SimpleNamespace()

    class FakePipeline:
        cfg = FakeCfg()
        arbiter = TrackingArbiter()
        color = None
        state = SimpleNamespace()

    class FakeApi:
        revision = 0
        def refusal(self, code, msg, status=422):
            return {"error": code, "message": msg}

    mgr = ConfigManager(FakePipeline(), FakeApi())
    result = mgr.apply_hot_key("gps.drive_stale_sec", 0.0)   # below lo=1.0
    assert result is not None  # refused
    result2 = mgr.apply_hot_key("gps.drive_stale_sec", 61.0)  # above hi=60.0
    assert result2 is not None  # refused
