"""Boot-smoke: the REAL production assembly path, fakes only at hardware edges.

Both 2026-06-11 zombie bugs were WIRING bugs invisible to unit suites:
(1) run() checked the FOV curve before build_app wired pipeline._store;
(2) the estimator called a CameraPose method only test fakes had, killing the
loop on the first locked frame. This test constructs the genuine objects in
run.py's exact order (Pipeline -> build_app) and drives the estimator init +
shadow tick through the same code production runs.
"""
import time
import types

from fastapi.testclient import TestClient

from wavecam.config import load_config
from wavecam.pipeline import Pipeline
from wavecam.ptz_visca import NullPtz
from wavecam.web import build_app


def _write_cfg(tmp_path):
    y = tmp_path / "smoke.yaml"
    y.write_text(
        "camera:\n  source: 0\n"
        "detector:\n  enabled: false\n"
        "color:\n  enabled: false\n"
        "ptz:\n  enabled: false\n"
        "estimator:\n  enabled: true\n  shadow: true\n"
    )
    return str(y)


def _boot(tmp_path, monkeypatch):
    """run.py's assembly order, verbatim: config -> Pipeline -> build_app."""
    monkeypatch.setenv("WAVECAM_POSE_PATH", str(tmp_path / "pose.json"))
    cfg = load_config(_write_cfg(tmp_path))
    cfg.shadow_log_dir = str(tmp_path / "shadow")
    pipe = Pipeline(cfg, NullPtz(), detector_factory=lambda: None)
    app = build_app(pipe)            # wires pipeline._store (the #33 seam)
    return cfg, pipe, TestClient(app)


def test_assembly_then_late_fov_curve_starts_shadow(tmp_path, monkeypatch):
    cfg, pipe, client = _boot(tmp_path, monkeypatch)

    # Pre-curve: the G2 gate must no-op cleanly (and must SEE the wired store).
    pipe._maybe_init_estimator()
    assert pipe.estimator is None
    assert hasattr(pipe, "_store"), "build_app must wire pipeline._store"

    # Curve arrives mid-session through the real endpoint...
    r = client.post("/api/v1/calibration/fov",
                    json={"zoom_enc": 0, "fov_deg": 63.7})
    assert r.status_code == 200

    # ...and the loop's re-check (the #33 fix) must bring shadow up.
    pipe._maybe_init_estimator()
    assert pipe.estimator is not None
    assert pipe._est_active_shadow is True
    assert pipe._shadow_writer is not None


def test_shadow_tick_runs_real_estimator_against_real_pose(tmp_path, monkeypatch):
    """The exact crash condition of bug #2: locked frame + fresh encoders +
    real CameraPose, driven through the production tick."""
    cfg, pipe, client = _boot(tmp_path, monkeypatch)
    client.post("/api/v1/calibration/fov", json={"zoom_enc": 0, "fov_deg": 63.7})
    pipe._maybe_init_estimator()
    assert pipe.estimator is not None

    pipe.pose.lat, pipe.pose.lon = 21.6451, -158.0501
    pipe.pose.calibrate_pan_aim(enc=0.0, bearing_deg=0.0, enc_per_deg=14.4)
    pipe.gps = types.SimpleNamespace(
        get_fix=lambda: types.SimpleNamespace(
            lat=21.6460, lon=-158.0501, age_sec=0.5))
    pipe.ptz_state = types.SimpleNamespace(
        start=lambda: None, stop=lambda: None, is_alive=lambda: False,
        latest=lambda: ((10, 0), 0.05),
        latest_zoom=lambda: (0, 0.1))

    fr = types.SimpleNamespace(locked=True, target_xy=(320.0, 180.0))
    for _ in range(6):                       # past log_every_n at least once
        pipe._estimator_shadow_tick(fr, 640, time.time())

    assert pipe.estimator is not None, "tick must not have self-disabled"
    shadow_files = list((tmp_path / "shadow").glob("*.jsonl"))
    assert shadow_files and shadow_files[0].stat().st_size > 0


def test_kill_through_real_assembly_clears_verifier(tmp_path, monkeypatch):
    cfg, pipe, client = _boot(tmp_path, monkeypatch)
    pipe._pointing_verifier.record_move(pan_enc=500, tilt_enc=0)
    r = client.post("/api/v1/safety/kill", json={})
    assert r.status_code == 200
    assert pipe.owner.killed is True
    assert pipe._pointing_verifier._target is None
