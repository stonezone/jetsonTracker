# tests/test_config_persist.py
from __future__ import annotations

import os
import sys

import yaml
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))
from test_control_api import DummyPipeline  # noqa: E402
from wavecam.config import Config, FusionCfg, GpsCfg, load_config, persist_hot_values, _overlay_path
from wavecam.web import build_app


# ---------------------------------------------------------------------------
# persist_hot_values — now writes to config.local.yaml, not the main YAML
# ---------------------------------------------------------------------------

def test_persist_hot_values_writes_to_overlay(tmp_path):
    """persist_hot_values writes to config.local.yaml, not the main YAML."""
    p = tmp_path / "config.yaml"
    p.write_text("gps:\n  enabled: true\n  stale_threshold_sec: 10\nfusion:\n  gps_boost: 0.2\n")
    persist_hot_values(str(p), {"gps.stale_threshold_sec": 7, "fusion.gps_boost": 0.3})

    overlay = tmp_path / "config.local.yaml"
    assert overlay.exists(), "overlay file must be created by persist_hot_values"
    data = yaml.safe_load(overlay.read_text())
    assert data["gps"]["stale_threshold_sec"] == 7
    assert data["fusion"]["gps_boost"] == 0.3


def test_persist_hot_values_main_yaml_untouched(tmp_path):
    """The main YAML file must never be written by persist_hot_values."""
    p = tmp_path / "config.yaml"
    original = "gps:\n  enabled: true\n  stale_threshold_sec: 10\n"
    p.write_text(original)
    persist_hot_values(str(p), {"gps.stale_threshold_sec": 45})
    assert p.read_text() == original, "main YAML must not be modified by persist"


def test_persist_hot_values_round_trips(tmp_path):
    """Existing untouched keys in the overlay survive a second persist call."""
    p = tmp_path / "config.yaml"
    p.write_text("gps:\n  enabled: true\n  stale_threshold_sec: 10\nfusion:\n  gps_boost: 0.2\n")
    # First call
    persist_hot_values(str(p), {"gps.stale_threshold_sec": 7, "fusion.gps_boost": 0.3})
    # Second call — only update one key
    persist_hot_values(str(p), {"gps.stale_threshold_sec": 45})

    overlay = tmp_path / "config.local.yaml"
    data = yaml.safe_load(overlay.read_text())
    assert data["gps"]["stale_threshold_sec"] == 45
    assert data["fusion"]["gps_boost"] == 0.3  # survives second call


def test_persist_creates_missing_section(tmp_path):
    """persist_hot_values creates the section in the overlay if absent."""
    p = tmp_path / "config.yaml"
    p.write_text("gps:\n  enabled: true\n")
    persist_hot_values(str(p), {"fusion.gps_boost": 0.25})
    overlay = tmp_path / "config.local.yaml"
    assert yaml.safe_load(overlay.read_text())["fusion"]["gps_boost"] == 0.25


# ---------------------------------------------------------------------------
# load_config — overlay merge
# ---------------------------------------------------------------------------

def test_load_config_overlay_absent_unchanged(tmp_path):
    """With no config.local.yaml, load_config behaves exactly as before."""
    y = tmp_path / "config.yaml"
    y.write_text("fusion:\n  gps_boost: 0.2\n  lock_threshold: 0.6\n  unlock_threshold: 0.35\n")
    cfg = load_config(str(y))
    assert cfg.fusion.gps_boost == 0.2
    assert cfg.fusion.lock_threshold == 0.6


def test_load_config_overlay_merges_over_main(tmp_path):
    """Overlay values supersede main-YAML values for the same keys."""
    y = tmp_path / "config.yaml"
    y.write_text("gps:\n  stale_threshold_sec: 10\n  enabled: true\nfusion:\n  gps_boost: 0.2\n")
    ov = tmp_path / "config.local.yaml"
    ov.write_text("gps:\n  stale_threshold_sec: 45\n")
    cfg = load_config(str(y))
    assert cfg.gps.stale_threshold_sec == 45  # overridden by overlay
    assert cfg.gps.enabled is True             # main-YAML key not in overlay survives
    assert cfg.fusion.gps_boost == 0.2         # untouched section preserved


def test_load_config_overlay_unknown_section_ignored(tmp_path, capsys):
    """An unknown section in the overlay is ignored with a warning, no crash."""
    y = tmp_path / "config.yaml"
    y.write_text("fusion:\n  gps_boost: 0.2\n")
    ov = tmp_path / "config.local.yaml"
    ov.write_text("future_section:\n  some_key: 1\n")
    cfg = load_config(str(y))  # must not raise
    out = capsys.readouterr().out
    assert "unknown section" in out


def test_load_config_overlay_unknown_key_ignored(tmp_path):
    """Unknown keys within a known section in the overlay are silently ignored."""
    y = tmp_path / "config.yaml"
    y.write_text("gps:\n  stale_threshold_sec: 10\n")
    ov = tmp_path / "config.local.yaml"
    ov.write_text("gps:\n  stale_threshold_sec: 30\n  nonexistent_key: 99\n")
    cfg = load_config(str(y))  # must not raise
    assert cfg.gps.stale_threshold_sec == 30


# ---------------------------------------------------------------------------
# Round-trip: hot-apply → persist → load_config sees value
# ---------------------------------------------------------------------------

def test_hot_config_endpoint_persists_to_overlay(tmp_path):
    """POST config/hot writes the value to config.local.yaml (overlay), not the main YAML."""
    p = tmp_path / "config.yaml"
    p.write_text("fusion:\n  gps_boost: 0.2\n  gps_boost_radius_frac: 0.25\n")
    original_main = p.read_text()

    pipe = DummyPipeline()
    pipe.cfg.source_path = str(p)

    client = TestClient(build_app(pipe))
    resp = client.post("/api/v1/config/hot", json={"patch": {"fusion.gps_boost": 0.35}})
    assert resp.status_code == 200

    # Overlay must exist and have the new value
    overlay = tmp_path / "config.local.yaml"
    assert overlay.exists(), "config.local.yaml must be created after hot-apply"
    data = yaml.safe_load(overlay.read_text())
    assert data["fusion"]["gps_boost"] == 0.35

    # Main YAML must be untouched
    assert p.read_text() == original_main, "main YAML must not be written by hot-config persist"


def test_persist_then_load_config_round_trip(tmp_path):
    """Full round-trip: persist a value, then load_config reflects it."""
    p = tmp_path / "config.yaml"
    p.write_text("gps:\n  stale_threshold_sec: 10\n  enabled: true\n")
    persist_hot_values(str(p), {"gps.stale_threshold_sec": 45})
    cfg = load_config(str(p))
    assert cfg.gps.stale_threshold_sec == 45


def test_hot_config_string_value_persists_as_coerced_type(tmp_path):
    """Regression: set_float coerces "0.3" (str) to 0.3 (float) in memory.
    The persisted overlay must contain a Python float, not the raw request string."""
    p = tmp_path / "config.yaml"
    p.write_text("fusion:\n  gps_boost: 0.2\n")

    pipe = DummyPipeline()
    pipe.cfg.source_path = str(p)

    client = TestClient(build_app(pipe))
    resp = client.post("/api/v1/config/hot", json={"patch": {"fusion.gps_boost": "0.3"}})
    assert resp.status_code == 200, f"apply should accept a numeric string; got: {resp.json()}"

    overlay = tmp_path / "config.local.yaml"
    data = yaml.safe_load(overlay.read_text())
    persisted = data["fusion"]["gps_boost"]
    assert isinstance(persisted, float), (
        f"Expected float in overlay after coercion, got {type(persisted).__name__!r}: {persisted!r}"
    )
    assert persisted == 0.3


# ---------------------------------------------------------------------------
# Inverted hysteresis guard — must fire AFTER overlay merge
# ---------------------------------------------------------------------------

def test_load_config_resets_inverted_fusion_hysteresis(tmp_path, capsys):
    """Regression: a YAML with unlock >= lock must be reset to defaults."""
    y = tmp_path / "bad.yaml"
    y.write_text("fusion:\n  lock_threshold: 0.25\n  unlock_threshold: 0.5\n")
    cfg = load_config(str(y))
    d = FusionCfg()
    assert cfg.fusion.lock_threshold == d.lock_threshold
    assert cfg.fusion.unlock_threshold == d.unlock_threshold
    assert "INVALID fusion hysteresis" in capsys.readouterr().out


def test_load_config_keeps_valid_fusion_hysteresis(tmp_path):
    y = tmp_path / "ok.yaml"
    y.write_text("fusion:\n  lock_threshold: 0.7\n  unlock_threshold: 0.4\n")
    cfg = load_config(str(y))
    assert cfg.fusion.lock_threshold == 0.7
    assert cfg.fusion.unlock_threshold == 0.4


def test_inverted_hysteresis_via_overlay_resets_to_defaults(tmp_path, capsys):
    """An overlay that introduces an inverted fusion pair must also be caught and reset.
    The hysteresis guard runs AFTER the overlay merge."""
    y = tmp_path / "config.yaml"
    # Main YAML has valid thresholds
    y.write_text("fusion:\n  lock_threshold: 0.6\n  unlock_threshold: 0.35\n")
    # Overlay inverts them
    ov = tmp_path / "config.local.yaml"
    ov.write_text("fusion:\n  lock_threshold: 0.3\n  unlock_threshold: 0.7\n")
    cfg = load_config(str(y))
    d = FusionCfg()
    assert cfg.fusion.lock_threshold == d.lock_threshold
    assert cfg.fusion.unlock_threshold == d.unlock_threshold
    assert "INVALID fusion hysteresis" in capsys.readouterr().out
