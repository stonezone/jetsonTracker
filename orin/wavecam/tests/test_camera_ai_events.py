"""Onboard camera-AI disable: real CGI, basic auth, verify-after-set.

The original endpoint was folklore that 500'd silently for months — these
tests pin the probed-live contract (post_aimode&off sets, get_aimode reads
back) and that every outcome lands in events without ever blocking boot.
"""
from __future__ import annotations

from wavecam.camera_http import disable_onboard_ai
from wavecam.config import CameraAiCfg
from wavecam.events import EventRing


def _cfg(**over):
    base = dict(
        disable_on_start=True,
        http_base="http://cam",
        off_path="/cgi-bin/param.cgi?post_aimode&off",
        verify_path="/cgi-bin/param.cgi?get_aimode",
    )
    base.update(over)
    return CameraAiCfg(**base)


def _events_kinds(ring):
    return [(e["kind"], e["detail"]) for e in ring.since(0)]


def test_disabled_and_verified():
    calls = []

    def fake_get(url, user, password, timeout=2.0):
        calls.append((url, user, password))
        return "get_aimode=Off" if "get_aimode" in url else '{"status":"200"}'

    ring = EventRing()
    assert disable_onboard_ai(_cfg(), events=ring, http_get=fake_get) is True
    assert calls[0][0] == "http://cam/cgi-bin/param.cgi?post_aimode&off"
    assert calls[0][1:] == ("admin", "admin")   # factory-default basic auth
    assert calls[1][0] == "http://cam/cgi-bin/param.cgi?get_aimode"
    assert _events_kinds(ring) == [("camera_ai", "disabled (verified Off)")]


def test_set_accepted_but_readback_on_is_failure():
    """Trusting the status code is how the 500 went unnoticed — a readback
    that isn't Off must be reported as a failure."""
    def fake_get(url, user, password, timeout=2.0):
        return "get_aimode=On" if "get_aimode" in url else '{"status":"200"}'

    ring = EventRing()
    assert disable_onboard_ai(_cfg(), events=ring, http_get=fake_get) is False
    kinds = _events_kinds(ring)
    assert kinds[0][0] == "camera_ai"
    assert "FAILED" in kinds[0][1]


def test_http_error_records_event_and_never_raises():
    def fake_get(url, user, password, timeout=2.0):
        raise OSError("HTTP Error 500: Internal Server Error")

    ring = EventRing()
    assert disable_onboard_ai(_cfg(), events=ring, http_get=fake_get) is False
    assert "FAILED" in _events_kinds(ring)[0][1]


def test_disable_on_start_false_is_noop():
    def fake_get(url, user, password, timeout=2.0):
        raise AssertionError("must not be called")

    ring = EventRing()
    assert disable_onboard_ai(_cfg(disable_on_start=False),
                              events=ring, http_get=fake_get) is False
    assert _events_kinds(ring) == []


def test_unconfigured_cgi_is_noop():
    ring = EventRing()
    cfg = _cfg(http_base="", off_path="")
    assert disable_onboard_ai(cfg, events=ring) is False
    assert _events_kinds(ring) == []


def test_custom_credentials_used():
    seen = []

    def fake_get(url, user, password, timeout=2.0):
        seen.append((user, password))
        return "get_aimode=Off"

    cfg = _cfg(http_user="zack", http_pass="hunter2")
    assert disable_onboard_ai(cfg, events=None, http_get=fake_get) is True
    assert all(c == ("zack", "hunter2") for c in seen)
