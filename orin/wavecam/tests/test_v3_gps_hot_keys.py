"""H6 (review 2026-06-15): the v3 GPS/fusion knobs must be hot-configurable so the
operator can tune them — and disable base-drift revalidation — in the field without
a restart. config.py comments base_drift_enabled as "hot", but it (and the Phase-4
drive_zoom curve params + the Phase-3 bearing cue) were never wired into
HOT_CONFIG_KEYS / apply_hot_key. These lock that wiring in.
"""
from types import SimpleNamespace

from wavecam.config import GpsCfg, FusionCfg, TrackingCfg
from wavecam.control_config import ConfigManager
from wavecam.control_utils import HOT_CONFIG_KEYS
from wavecam.tracking_arbiter import TrackingArbiter


V3_HOT_KEYS = (
    "gps.base_drift_enabled",
    "gps.drive_zoom_near_m",
    "gps.drive_zoom_far_m",
    "gps.drive_zoom_max_enc",
    "gps.drive_zoom_max_frac",
    "fusion.gps_bearing_cue_enabled",
)


def _mgr():
    gps_cfg = GpsCfg()
    fusion_cfg = FusionCfg()

    class FakeCfg:
        gps = gps_cfg
        fusion = fusion_cfg
        tracking = TrackingCfg()
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

    return ConfigManager(FakePipeline(), FakeApi()), gps_cfg, fusion_cfg


def test_v3_keys_registered_in_hot_config_keys():
    for key in V3_HOT_KEYS:
        assert key in HOT_CONFIG_KEYS, f"{key} missing from HOT_CONFIG_KEYS"


def test_base_drift_enabled_hot_toggle():
    """The field operator must be able to disable base-drift revalidation live."""
    mgr, gps_cfg, _ = _mgr()
    assert gps_cfg.base_drift_enabled is True
    assert mgr.apply_hot_key("gps.base_drift_enabled", False) is None
    assert gps_cfg.base_drift_enabled is False


def test_gps_bearing_cue_enabled_hot_toggle():
    mgr, _, fusion_cfg = _mgr()
    assert mgr.apply_hot_key("fusion.gps_bearing_cue_enabled", True) is None
    assert fusion_cfg.gps_bearing_cue_enabled is True


def test_drive_zoom_curve_params_hot_apply():
    mgr, gps_cfg, _ = _mgr()
    assert mgr.apply_hot_key("gps.drive_zoom_near_m", 30.0) is None
    assert gps_cfg.drive_zoom_near_m == 30.0
    assert mgr.apply_hot_key("gps.drive_zoom_far_m", 280.0) is None
    assert gps_cfg.drive_zoom_far_m == 280.0
    assert mgr.apply_hot_key("gps.drive_zoom_max_enc", 12000.0) is None
    assert gps_cfg.drive_zoom_max_enc == 12000.0
    assert mgr.apply_hot_key("gps.drive_zoom_max_frac", 0.5) is None
    assert gps_cfg.drive_zoom_max_frac == 0.5


def test_drive_zoom_max_frac_range_rejected():
    """max_frac is a fraction of full zoom — out-of-[0,1] must be refused."""
    mgr, _, _ = _mgr()
    assert mgr.apply_hot_key("gps.drive_zoom_max_frac", 1.5) is not None
    assert mgr.apply_hot_key("gps.drive_zoom_max_frac", -0.1) is not None


def test_tracking_enabled_registered_in_hot_config_keys():
    assert "tracking.enabled" in HOT_CONFIG_KEYS


def test_tracking_enabled_hot_toggle_syncs_arbiter():
    """DISABLE-PTZ latch: flipping tracking.enabled live must reach the running
    arbiter so autonomous tracking stops/resumes without a restart."""
    mgr, _, _ = _mgr()
    cfg_tracking = mgr.pipeline.cfg.tracking
    arbiter = mgr.pipeline.arbiter
    assert cfg_tracking.enabled is True and arbiter.enabled is True
    assert mgr.apply_hot_key("tracking.enabled", False) is None
    assert cfg_tracking.enabled is False
    assert arbiter.enabled is False  # synced to the live arbiter
    assert mgr.apply_hot_key("tracking.enabled", True) is None
    assert arbiter.enabled is True


def test_tracking_enabled_rejects_non_bool():
    mgr, _, _ = _mgr()
    assert mgr.apply_hot_key("tracking.enabled", "yes") is not None
