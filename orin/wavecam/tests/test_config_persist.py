# tests/test_config_persist.py
from __future__ import annotations

import os
import sys

import yaml
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))
from test_control_api import DummyPipeline  # noqa: E402
from wavecam.config import Config, GpsCfg, persist_hot_values
from wavecam.web import build_app


def test_persist_hot_values_round_trips(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("gps:\n  enabled: true\n  stale_threshold_sec: 10\nfusion:\n  gps_boost: 0.2\n")
    persist_hot_values(str(p), {"gps.stale_threshold_sec": 7, "fusion.gps_boost": 0.3})
    data = yaml.safe_load(p.read_text())
    assert data["gps"]["stale_threshold_sec"] == 7
    assert data["fusion"]["gps_boost"] == 0.3
    assert data["gps"]["enabled"] is True          # untouched keys survive


def test_persist_creates_missing_section(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("gps:\n  enabled: true\n")
    persist_hot_values(str(p), {"fusion.gps_boost": 0.25})
    assert yaml.safe_load(p.read_text())["fusion"]["gps_boost"] == 0.25


def test_hot_config_endpoint_persists_to_yaml(tmp_path, monkeypatch):
    """POST config/hot writes the value back to cfg.source_path."""
    p = tmp_path / "config.yaml"
    p.write_text("fusion:\n  gps_boost: 0.2\n  gps_boost_radius_frac: 0.25\n")

    pipe = DummyPipeline()
    pipe.cfg.source_path = str(p)

    client = TestClient(build_app(pipe))
    resp = client.post("/api/v1/config/hot", json={"patch": {"fusion.gps_boost": 0.35}})
    assert resp.status_code == 200

    data = yaml.safe_load(p.read_text())
    assert data["fusion"]["gps_boost"] == 0.35
