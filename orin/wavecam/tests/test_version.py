# tests/test_version.py
import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))
from test_control_api import DummyPipeline  # noqa: E402
from wavecam.web import build_app


def test_version_reports_unknown_without_stamp(tmp_path, monkeypatch):
    monkeypatch.setenv("WAVECAM_VERSION_PATH", str(tmp_path / "version.json"))
    client = TestClient(build_app(DummyPipeline()))
    r = client.get("/api/v1/version")
    assert r.status_code == 200
    body = r.json()
    assert body["git_sha"] is None and body["deployed_at"] is None


def test_version_reports_stamp(tmp_path, monkeypatch):
    p = tmp_path / "version.json"
    p.write_text('{"git_sha": "abc1234", "branch": "main", "deployed_at": "2026-06-10T00:00:00Z"}')
    monkeypatch.setenv("WAVECAM_VERSION_PATH", str(p))
    client = TestClient(build_app(DummyPipeline()))
    body = client.get("/api/v1/version").json()
    assert body["git_sha"] == "abc1234"
    assert body["branch"] == "main"
