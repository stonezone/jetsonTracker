import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import time
import types
from fastapi.testclient import TestClient
from test_control_api import DummyPipeline
from wavecam.health import HealthRegistry
from wavecam.web import build_app


def test_beat_and_staleness():
    h = HealthRegistry()
    h.beat("capture", detail={"fps": 30.1})
    snap = h.snapshot(stale_after_sec=5.0)
    assert snap["components"]["capture"]["ok"] is True
    assert snap["components"]["capture"]["detail"]["fps"] == 30.1
    assert snap["ok"] is True


def test_stale_component_flips_overall_not_ok():
    h = HealthRegistry()
    h.beat("capture")
    h._last["capture"] = (time.time() - 99, {})    # simulate silence
    snap = h.snapshot(stale_after_sec=5.0)
    assert snap["components"]["capture"]["ok"] is False and snap["ok"] is False


def test_health_endpoint_returns_capture_and_disk(tmp_path):
    pl = DummyPipeline()
    # Point rec_dir at a real directory so the disk check succeeds
    pl.recorder.config = types.SimpleNamespace(rec_dir=tmp_path)
    pl.health.beat("capture", {"fps": 29.9, "connected": True})
    client = TestClient(build_app(pl))
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert "capture" in body["components"]
    assert "disk" in body["components"]
    assert body["components"]["capture"]["ok"] is True


def test_watchdog_health_paths_exist(tmp_path):
    pl = DummyPipeline()
    pl.recorder.config = types.SimpleNamespace(rec_dir=tmp_path)
    pl.gps = types.SimpleNamespace(
        reader_alive=lambda: False,
        last_poll_age_sec=lambda: 12.3,
    )
    client = TestClient(build_app(pl))

    body = client.get("/api/v1/health").json()

    assert body["components"]["gps_reader"]["ok"] is False
    assert body["components"]["gps_reader"]["age_sec"] == 12.3
    assert isinstance(body["components"]["disk"]["detail"]["free_gb"], float)


def test_base_silent_flags_wedged_base(tmp_path):
    # GPS-2: reader thread alive but no fresh lines for > base_silent_sec means the
    # base Wio is wedged (serial open, silent). reader_alive() can't see this, so a
    # dedicated component must flag it ok:false instead of looking healthy.
    pl = DummyPipeline()
    pl.recorder.config = types.SimpleNamespace(rec_dir=tmp_path)
    pl.gps = types.SimpleNamespace(
        reader_alive=lambda: True,
        last_poll_age_sec=lambda: 99.0,   # silent for 99s (> 30s default)
    )
    body = TestClient(build_app(pl)).get("/api/v1/health").json()
    assert body["components"]["base_silent"]["ok"] is False
    assert body["components"]["base_silent"]["detail"]["reader_alive"] is True
    assert body["ok"] is False


def test_base_silent_ok_when_fresh(tmp_path):
    pl = DummyPipeline()
    pl.recorder.config = types.SimpleNamespace(rec_dir=tmp_path)
    pl.gps = types.SimpleNamespace(
        reader_alive=lambda: True,
        last_poll_age_sec=lambda: 1.5,    # fresh
    )
    body = TestClient(build_app(pl)).get("/api/v1/health").json()
    assert body["components"]["base_silent"]["ok"] is True


def test_base_silent_ok_when_reader_down(tmp_path):
    # A dead reader is the gps_reader component's job to flag; base_silent should not
    # double-fault (you can't be "silent" if the thread isn't even running).
    pl = DummyPipeline()
    pl.recorder.config = types.SimpleNamespace(rec_dir=tmp_path)
    pl.gps = types.SimpleNamespace(
        reader_alive=lambda: False,
        last_poll_age_sec=lambda: None,
    )
    body = TestClient(build_app(pl)).get("/api/v1/health").json()
    assert body["components"]["base_silent"]["ok"] is True
