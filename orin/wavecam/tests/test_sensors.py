"""Tests for Phase-3 T3.2: phone-on-tripod sensor ingest.

Covers:
  - Route accepts and returns 200 regardless of sensors.enabled.
  - When disabled, ingest is a no-op (sample not stored).
  - Heading drift alert fires once (hysteresis), re-arms after returning.
  - Bump alert fires immediately, rate-limited to 1/10s.
  - Baseline reset re-captures on the next valid sample.
  - Hot-config keys sensors.enabled and sensors.drift_alert_deg work.
"""
from __future__ import annotations

import time
import types

import pytest
from fastapi.testclient import TestClient

from wavecam.events import EventRing
from wavecam.sensor_hub import PhoneSample, SensorHub, _normalize_180
from wavecam.web import build_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg(enabled: bool = True, drift_alert_deg: float = 12.0):
    return types.SimpleNamespace(
        sensors=types.SimpleNamespace(enabled=enabled, drift_alert_deg=drift_alert_deg)
    )


def _sample(heading_deg=None, heading_acc=None, lat=None, lon=None,
            h_acc=None, bump=False, received_at=None):
    return PhoneSample(
        heading_deg=heading_deg,
        heading_acc=heading_acc,
        lat=lat,
        lon=lon,
        h_acc=h_acc,
        bump=bump,
        received_at=received_at if received_at is not None else time.time(),
    )


# ---------------------------------------------------------------------------
# _normalize_180 unit tests
# ---------------------------------------------------------------------------

def test_normalize_180_basic():
    assert _normalize_180(0) == 0
    assert _normalize_180(180) == -180 or _normalize_180(180) == 180
    assert abs(_normalize_180(270)) == 90   # -90


def test_normalize_180_small():
    assert abs(_normalize_180(10)) == 10
    assert abs(_normalize_180(-10)) == 10


# ---------------------------------------------------------------------------
# SensorHub unit tests
# ---------------------------------------------------------------------------

class TestSensorHubDisabled:
    """When sensors.enabled=False the hub does nothing."""

    def test_ingest_no_op(self):
        ring = EventRing()
        hub = SensorHub(ring, _cfg(enabled=False))
        s = _sample(heading_deg=90.0, heading_acc=1.0, received_at=1000.0)
        hub.ingest(s)
        assert hub.latest() is None  # nothing stored

    def test_no_events_on_bump(self):
        ring = EventRing()
        hub = SensorHub(ring, _cfg(enabled=False))
        hub.ingest(_sample(bump=True, received_at=1000.0))
        assert ring.since(0) == []


class TestSensorHubEnabled:
    """When sensors.enabled=True samples are stored and alerts fire."""

    def test_stores_sample(self):
        hub = SensorHub(EventRing(), _cfg())
        s = _sample(heading_deg=90.0, heading_acc=1.0, received_at=1000.0)
        hub.ingest(s)
        assert hub.latest() is s

    def test_baseline_captured_first_valid(self):
        hub = SensorHub(EventRing(), _cfg())
        hub.ingest(_sample(heading_deg=45.0, heading_acc=-1.0, received_at=1.0))  # invalid acc
        # Still None — invalid accuracy
        hub.ingest(_sample(heading_deg=90.0, heading_acc=2.0, received_at=2.0))
        # Baseline is now 90
        # Within-threshold sample — no event
        ring = hub._events
        assert ring.since(0) == []

    def test_drift_alert_fires_after_sustain(self):
        ring = EventRing()
        hub = SensorHub(ring, _cfg(drift_alert_deg=10.0))
        # Establish baseline at 100°
        t0 = 1000.0
        hub.ingest(_sample(heading_deg=100.0, heading_acc=1.0, received_at=t0))
        # Drift by 15° (> 10°) for 11s — alert should fire
        for i in range(12):
            hub.ingest(_sample(heading_deg=115.0, heading_acc=1.0, received_at=t0 + i))
        events = ring.since(0)
        assert any(e["kind"] == "anchor_suspect" and
                   e["detail"]["reason"] == "heading_drift" for e in events)

    def test_drift_alert_fires_only_once_per_excursion(self):
        ring = EventRing()
        hub = SensorHub(ring, _cfg(drift_alert_deg=10.0))
        t0 = 1000.0
        hub.ingest(_sample(heading_deg=100.0, heading_acc=1.0, received_at=t0))
        # Sustained drift for 20 more samples (all > threshold)
        for i in range(25):
            hub.ingest(_sample(heading_deg=120.0, heading_acc=1.0, received_at=t0 + i))
        events = [e for e in ring.since(0) if e["kind"] == "anchor_suspect"]
        assert len(events) == 1  # fired exactly once

    def test_drift_alert_rearms_after_return(self):
        ring = EventRing()
        hub = SensorHub(ring, _cfg(drift_alert_deg=10.0))
        t0 = 1000.0
        hub.ingest(_sample(heading_deg=100.0, heading_acc=1.0, received_at=t0))
        # First excursion — drift 15° for 11s → fires
        for i in range(12):
            hub.ingest(_sample(heading_deg=115.0, heading_acc=1.0, received_at=t0 + i))
        # Return within 50% of threshold (i.e., ≤ 5°)
        hub.ingest(_sample(heading_deg=102.0, heading_acc=1.0, received_at=t0 + 13))
        events_after_return = ring.since(0)
        # Second excursion — fire again
        t1 = t0 + 100.0
        hub.ingest(_sample(heading_deg=115.0, heading_acc=1.0, received_at=t1))
        for i in range(12):
            hub.ingest(_sample(heading_deg=115.0, heading_acc=1.0, received_at=t1 + i))
        events_after_second = ring.since(0)
        anchor_events = [e for e in events_after_second if e["kind"] == "anchor_suspect"
                         and e["detail"].get("reason") == "heading_drift"]
        assert len(anchor_events) == 2

    def test_drift_not_fired_before_sustain_window(self):
        ring = EventRing()
        hub = SensorHub(ring, _cfg(drift_alert_deg=10.0))
        t0 = 1000.0
        hub.ingest(_sample(heading_deg=100.0, heading_acc=1.0, received_at=t0))
        # Drift but only for 8s — below the 10s sustain window
        for i in range(9):
            hub.ingest(_sample(heading_deg=115.0, heading_acc=1.0, received_at=t0 + i))
        events = ring.since(0)
        assert not any(e["kind"] == "anchor_suspect" for e in events)

    def test_bump_fires_immediately(self):
        ring = EventRing()
        hub = SensorHub(ring, _cfg())
        hub.ingest(_sample(bump=True, received_at=1000.0))
        events = ring.since(0)
        assert any(e["kind"] == "anchor_suspect" and
                   e["detail"]["reason"] == "bump" for e in events)

    def test_bump_rate_limited(self):
        ring = EventRing()
        hub = SensorHub(ring, _cfg())
        # Three bumps within 10s — only first should fire
        for i in range(3):
            hub.ingest(_sample(bump=True, received_at=1000.0 + i))
        bump_events = [e for e in ring.since(0)
                       if e["kind"] == "anchor_suspect" and e["detail"]["reason"] == "bump"]
        assert len(bump_events) == 1

    def test_bump_fires_again_after_rate_limit_window(self):
        ring = EventRing()
        hub = SensorHub(ring, _cfg())
        hub.ingest(_sample(bump=True, received_at=1000.0))
        hub.ingest(_sample(bump=True, received_at=1011.0))  # 11s later — past the 10s window
        bump_events = [e for e in ring.since(0)
                       if e["kind"] == "anchor_suspect" and e["detail"]["reason"] == "bump"]
        assert len(bump_events) == 2

    def test_baseline_reset(self):
        ring = EventRing()
        hub = SensorHub(ring, _cfg())
        hub.ingest(_sample(heading_deg=90.0, heading_acc=1.0, received_at=1000.0))
        hub.reset_baseline()
        # After reset, next valid sample becomes new baseline
        hub.ingest(_sample(heading_deg=200.0, heading_acc=1.0, received_at=1001.0))
        # 200 is the new baseline — no drift events expected for a within-threshold sample
        hub.ingest(_sample(heading_deg=201.0, heading_acc=1.0, received_at=1002.0))
        events = [e for e in ring.since(0)
                  if e["kind"] == "anchor_suspect" and e["detail"].get("reason") == "heading_drift"]
        assert events == []


# ---------------------------------------------------------------------------
# Route tests via TestClient
# ---------------------------------------------------------------------------

from tests.test_control_api import DummyPipeline  # noqa: E402


def _make_client_with_sensors(enabled: bool = False):
    pipeline = DummyPipeline()
    pipeline.cfg.sensors.enabled = enabled
    return TestClient(build_app(pipeline))


class TestSensorsRoute:
    def test_post_returns_200_when_disabled(self):
        client = _make_client_with_sensors(enabled=False)
        r = client.post("/api/v1/sensors/phone", json={"bump": False})
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_post_returns_200_when_enabled(self):
        client = _make_client_with_sensors(enabled=True)
        r = client.post("/api/v1/sensors/phone", json={
            "heading_deg": 90.0, "heading_acc": 2.0,
            "lat": 33.0, "lon": -117.0, "h_acc": 5.0, "bump": False
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_post_full_payload(self):
        client = _make_client_with_sensors(enabled=True)
        r = client.post("/api/v1/sensors/phone", json={
            "heading_deg": 180.0,
            "heading_acc": 3.5,
            "lat": 33.1,
            "lon": -117.2,
            "h_acc": 8.0,
            "bump": True,
        })
        assert r.status_code == 200

    def test_post_empty_payload(self):
        """All fields optional — empty body should 200."""
        client = _make_client_with_sensors(enabled=True)
        r = client.post("/api/v1/sensors/phone", json={})
        assert r.status_code == 200

    def test_disabled_does_not_store_sample(self):
        """When disabled the hub's latest() stays None."""
        pipeline = DummyPipeline()
        pipeline.cfg.sensors.enabled = False
        app = build_app(pipeline)
        client = TestClient(app)
        client.post("/api/v1/sensors/phone", json={"heading_deg": 90.0, "heading_acc": 1.0})
        hub = app.state.control_api.sensor_hub
        assert hub.latest() is None

    def test_enabled_stores_sample(self):
        pipeline = DummyPipeline()
        pipeline.cfg.sensors.enabled = True
        app = build_app(pipeline)
        client = TestClient(app)
        client.post("/api/v1/sensors/phone", json={"heading_deg": 45.0, "heading_acc": 1.5})
        hub = app.state.control_api.sensor_hub
        assert hub.latest() is not None
        assert hub.latest().heading_deg == pytest.approx(45.0)

    def test_baseline_reset_route(self):
        client = _make_client_with_sensors(enabled=True)
        r = client.post("/api/v1/sensors/phone/baseline/reset")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_drift_event_visible_in_events_endpoint(self):
        """anchor_suspect events from the hub appear in GET /api/v1/events."""
        pipeline = DummyPipeline()
        pipeline.cfg.sensors.enabled = True
        pipeline.cfg.sensors.drift_alert_deg = 10.0
        app = build_app(pipeline)
        client = TestClient(app)
        hub = app.state.control_api.sensor_hub

        # Establish baseline then drive sustained drift via direct hub calls
        # (faster than 11 HTTP posts)
        t0 = 1000.0
        hub.ingest(PhoneSample(90.0, 1.0, None, None, None, False, t0))
        for i in range(12):
            hub.ingest(PhoneSample(110.0, 1.0, None, None, None, False, t0 + i))

        r = client.get("/api/v1/events?since=0")
        assert r.status_code == 200
        events = r.json()["events"]
        assert any(e["kind"] == "anchor_suspect" for e in events)
        # Verify the detail dict passes through (not a plain string)
        hit = next(e for e in events if e["kind"] == "anchor_suspect")
        assert isinstance(hit["detail"], dict)
        assert hit["detail"]["reason"] == "heading_drift"
