"""Wave 2 (audit 2026-07-01): M13, M14, M21, L3, Wave-1 box_ttl_sec follow-up,
C2 cross-wave support, and the M9 pipeline gate.

M13: web.show_mask survives a restart (WebCfg field + Pipeline.__init__ init,
mirroring show_hud).

M14: estimator.use_vision_range/subject_height_m/r_range_frac are visible in
HOT_CONFIG_KEYS + the /config current.estimator snapshot (setters already
existed in control_config.py).

M21: DetectorCfg.model defaults to yolo11n.pt, not the documented-unusable
yolo26n.pt; both non-servo checked-in YAMLs match.

L3: config.orin.servo.yaml declares an explicit direct_lora source and drops
the Meshtastic-era remote_id; run.py rejects unknown gps.source loudly.

Wave-1 follow-up: config.orin.servo.yaml box_ttl_sec is 0.2, not the stale 0.6.

C2 (cross-wave support): AgentCfg.allow_unauthenticated exists, default False.

M9 (pipeline gate): a poor-accuracy GPS fix must not count as "fresh" for
drive authority even though it's recent; an unknown accuracy (None) must not
regress existing behavior.
"""
from __future__ import annotations

import os

import pytest

import run
from wavecam.config import AgentCfg, DetectorCfg, GpsCfg, WebCfg, load_config
from wavecam.control_snapshots import build_config_snapshot
from wavecam.control_utils import HOT_CONFIG_KEYS
from wavecam.gps_stub import NormalizedFix
from wavecam.pipeline import Pipeline
from wavecam.ptz_visca import NullPtz

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
WAVECAM_DIR = os.path.join(REPO_ROOT, "orin", "wavecam")


# --- M13: web.show_mask ------------------------------------------------------

def test_web_cfg_show_mask_field_defaults_true():
    assert WebCfg().show_mask is True


def test_pipeline_init_seeds_show_mask_from_config(tmp_path, monkeypatch):
    monkeypatch.setenv("WAVECAM_POSE_PATH", str(tmp_path / "pose.json"))
    y = tmp_path / "cfg.yaml"
    y.write_text(
        "camera:\n  source: 0\n"
        "detector:\n  enabled: false\n"
        "color:\n  enabled: false\n"
        "ptz:\n  enabled: false\n"
        "web:\n  show_mask: false\n"
    )
    cfg = load_config(str(y))
    assert cfg.web.show_mask is False
    pipe = Pipeline(cfg, NullPtz(), detector_factory=lambda: None)
    # Before this fix, SharedState always started show_mask=True regardless of
    # config, so a hot-persisted False silently reset on every restart.
    assert pipe.state.show_mask is False


def test_pipeline_init_show_mask_defaults_true_without_config_key(tmp_path, monkeypatch):
    monkeypatch.setenv("WAVECAM_POSE_PATH", str(tmp_path / "pose.json"))
    y = tmp_path / "cfg.yaml"
    y.write_text(
        "camera:\n  source: 0\n"
        "detector:\n  enabled: false\n"
        "color:\n  enabled: false\n"
        "ptz:\n  enabled: false\n"
    )
    cfg = load_config(str(y))
    pipe = Pipeline(cfg, NullPtz(), detector_factory=lambda: None)
    assert pipe.state.show_mask is True


# --- M14: estimator hot keys visible to feature detection + presets ---------

@pytest.mark.parametrize("key", [
    "estimator.use_vision_range",
    "estimator.subject_height_m",
    "estimator.r_range_frac",
])
def test_estimator_key_registered_in_hot_config_keys(key):
    assert key in HOT_CONFIG_KEYS


def test_config_snapshot_estimator_block_includes_the_three_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("WAVECAM_POSE_PATH", str(tmp_path / "pose.json"))
    y = tmp_path / "cfg.yaml"
    y.write_text(
        "camera:\n  source: 0\n"
        "detector:\n  enabled: false\n"
        "color:\n  enabled: false\n"
        "ptz:\n  enabled: false\n"
        "estimator:\n  use_vision_range: true\n  subject_height_m: 1.4\n  r_range_frac: 0.22\n"
    )
    cfg = load_config(str(y))
    pipe = Pipeline(cfg, NullPtz(), detector_factory=lambda: None)
    snapshot = build_config_snapshot(pipe, revision=1, calibration=None)
    est = snapshot["current"]["estimator"]
    assert est["use_vision_range"] is True
    assert est["subject_height_m"] == pytest.approx(1.4)
    assert est["r_range_frac"] == pytest.approx(0.22)


# --- M21: default detector model ---------------------------------------------

def test_detector_cfg_default_model_is_yolo11n():
    assert DetectorCfg().model == "yolo11n.pt"


@pytest.mark.parametrize("relpath", ["config.yaml", "config.orin.yaml"])
def test_checked_in_yaml_default_model_is_yolo11n(relpath):
    cfg = load_config(os.path.join(WAVECAM_DIR, relpath))
    assert cfg.detector.model in ("yolo11n.pt", "yolo11n.engine")
    assert "yolo26" not in cfg.detector.model


def test_servo_yaml_model_is_already_yolo11n_engine():
    """config.orin.servo.yaml was already pointed at a yolo11n engine (not part
    of M21's dataclass-default fix, but confirms no yolo26 regression)."""
    cfg = load_config(os.path.join(WAVECAM_DIR, "config.orin.servo.yaml"))
    assert "yolo11n" in cfg.detector.model
    assert "yolo26" not in cfg.detector.model


# --- Wave-1 follow-up: box_ttl_sec in config.orin.servo.yaml ----------------

def test_servo_yaml_box_ttl_sec_is_point_two():
    cfg = load_config(os.path.join(WAVECAM_DIR, "config.orin.servo.yaml"))
    assert cfg.detector.box_ttl_sec == pytest.approx(0.2)


# --- L3: servo yaml gps source + remote_id, run.py fatal on unknown source --

def test_servo_yaml_gps_source_is_explicit_direct_lora():
    cfg = load_config(os.path.join(WAVECAM_DIR, "config.orin.servo.yaml"))
    assert cfg.gps.source == "direct_lora"


def test_servo_yaml_has_no_meshtastic_remote_id():
    with open(os.path.join(WAVECAM_DIR, "config.orin.servo.yaml"), encoding="utf-8") as f:
        raw = f.read()
    assert "remote_id" not in raw


def test_run_rejects_unknown_gps_source():
    cfg = type("Cfg", (), {"gps": GpsCfg(enabled=True, source="bogus_transport")})()
    with pytest.raises(ValueError):
        run.start_gps_reader(cfg)


def test_run_still_accepts_explicit_meshtastic_source(monkeypatch):
    """meshtastic remains a valid (legacy) explicit choice — only unrecognized
    strings must fail loud."""
    import sys
    import types as _types

    made = {}

    class FakeMesh:
        def __init__(self, dev_path, remote_id):
            made["dev_path"] = dev_path
            made["remote_id"] = remote_id

        def connect(self):
            made["connected"] = True
            return True

    monkeypatch.setitem(
        sys.modules,
        "wavecam.gps_meshtastic",
        _types.SimpleNamespace(MeshtasticGps=FakeMesh),
    )
    cfg = type("Cfg", (), {"gps": GpsCfg(enabled=True, source="meshtastic")})()
    gps = run.start_gps_reader(cfg)
    assert isinstance(gps, FakeMesh)
    assert made["connected"] is True


# --- C2 cross-wave support: AgentCfg.allow_unauthenticated ------------------

def test_agent_cfg_allow_unauthenticated_defaults_false():
    assert AgentCfg().allow_unauthenticated is False


def test_servo_yaml_agent_allow_unauthenticated_stays_false_by_default():
    cfg = load_config(os.path.join(WAVECAM_DIR, "config.orin.servo.yaml"))
    assert cfg.agent.allow_unauthenticated is False


# --- M9 (pipeline): a poor-accuracy fix must not count as drive-fresh -------

def _gps_fresh(fix, drive_stale_sec: float, max_h_acc_m: float) -> bool:
    """Mirrors the gps_fresh computation added to pipeline.py's arbiter-handoff
    block (mirrors the existing test_drive_stale_gps.py convention of testing
    the gate logic directly rather than driving the full loop)."""
    if fix is None:
        return False
    h_acc_ok = fix.h_acc_m is None or fix.h_acc_m <= max_h_acc_m
    return fix.age_sec < drive_stale_sec and h_acc_ok


def test_gps_cfg_max_h_acc_m_default():
    assert GpsCfg().max_h_acc_m == pytest.approx(15.0)


def test_poor_accuracy_fix_is_not_drive_fresh():
    fix = NormalizedFix(lat=21.6, lon=-158.0, course=0.0, speed=0.0,
                        ts=0.0, age_sec=1.0, h_acc_m=30.0)
    assert _gps_fresh(fix, drive_stale_sec=8.0, max_h_acc_m=15.0) is False


def test_good_accuracy_fix_is_drive_fresh():
    fix = NormalizedFix(lat=21.6, lon=-158.0, course=0.0, speed=0.0,
                        ts=0.0, age_sec=1.0, h_acc_m=5.0)
    assert _gps_fresh(fix, drive_stale_sec=8.0, max_h_acc_m=15.0) is True


def test_unknown_accuracy_fix_does_not_regress_existing_behavior():
    """h_acc_m=None (older firmware/sources) must NOT withhold authority --
    unknown accuracy is not the same as bad accuracy."""
    fix = NormalizedFix(lat=21.6, lon=-158.0, course=0.0, speed=0.0,
                        ts=0.0, age_sec=1.0, h_acc_m=None)
    assert _gps_fresh(fix, drive_stale_sec=8.0, max_h_acc_m=15.0) is True


def test_pipeline_source_contains_the_h_acc_gate():
    """Regression guard tying the mirrored _gps_fresh() helper above to the
    actual inline gate in pipeline.py's arbiter-handoff block (that block runs
    inside the main loop, so it's exercised via the mirror-helper convention
    already established by test_drive_stale_gps.py rather than driving a full
    frame through _run())."""
    import inspect
    src = inspect.getsource(Pipeline._run)
    assert "gps_h_acc_ok" in src
    assert "max_h_acc_m" in src
