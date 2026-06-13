"""Package 1: camera_ai disable path records events; startup never blocked.

Tests:
  - CGI failure → event recorded with FAILED message
  - CGI success → event recorded with "disabled"
  - disable_on_start=False → no event, returns False
  - startup never raises (failure is non-fatal)
"""
from __future__ import annotations
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
import urllib.error

from wavecam.camera_http import disable_onboard_ai
from wavecam.events import EventRing


def _cfg(disable: bool = True, http_base: str = "http://cam", off_path: str = "/ai/off"):
    return SimpleNamespace(
        disable_on_start=disable,
        http_base=http_base,
        off_path=off_path,
    )


def test_failure_records_event_and_continues():
    """CGI failure → event recorded; function returns False (non-fatal)."""
    events = EventRing()
    cfg = _cfg()
    with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        result = disable_onboard_ai(cfg, events=events)
    assert result is False
    recorded = events.since(0)
    assert len(recorded) == 1
    assert recorded[0]["kind"] == "camera_ai"
    assert "FAILED" in recorded[0]["detail"]


def test_success_records_disabled_event():
    """CGI 200 → event recorded with 'disabled'."""
    events = EventRing()
    cfg = _cfg()
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.status = 200
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = disable_onboard_ai(cfg, events=events)
    assert result is True
    recorded = events.since(0)
    assert len(recorded) == 1
    assert recorded[0]["kind"] == "camera_ai"
    assert recorded[0]["detail"] == "disabled"


def test_disabled_on_start_false_no_event():
    """disable_on_start=False → no event, no HTTP call."""
    events = EventRing()
    cfg = _cfg(disable=False)
    result = disable_onboard_ai(cfg, events=events)
    assert result is False
    assert events.since(0) == []


def test_failure_without_events_arg_does_not_raise():
    """events=None is the default; failure path must not raise."""
    cfg = _cfg()
    with patch("urllib.request.urlopen", side_effect=OSError("unreachable")):
        result = disable_onboard_ai(cfg)   # no events kwarg
    assert result is False


def test_http_error_records_event():
    """urllib HTTPError (non-2xx) also records FAILED event."""
    events = EventRing()
    cfg = _cfg()
    with patch("urllib.request.urlopen",
               side_effect=urllib.error.URLError("timeout")):
        result = disable_onboard_ai(cfg, events=events)
    assert result is False
    recorded = events.since(0)
    assert any("FAILED" in e["detail"] for e in recorded)
