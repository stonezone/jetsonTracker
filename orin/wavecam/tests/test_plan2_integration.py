"""End-to-end integration: pointing_miss → events ring + health beat.

These tests drive PointingVerifier and HealthRegistry directly (no HTTP),
confirming the full signal path from a missed absolute move to observable
telemetry. No real camera needed.
"""
import time
import types
from wavecam.pointing_verifier import PointingVerifier
from wavecam.ptz_state import VERIFY_DELAY_SEC, POINTING_TOLERANCE_ENC
from wavecam.health import HealthRegistry
from wavecam.events import EventRing


def _setup():
    ptz_calls = []
    ptz = types.SimpleNamespace(
        pan_tilt_absolute=lambda pan, tilt, **kw: ptz_calls.append((pan, tilt)),
        _calls=ptz_calls,
    )
    health = HealthRegistry()
    events = EventRing(maxlen=100)
    # PtzState stub: encoder far from any reasonable target
    ptz_state_stub = types.SimpleNamespace(latest=lambda: ((0, 0), 0.01))
    verifier = PointingVerifier(ptz, ptz_state_stub, events)
    return ptz, health, events, verifier


def test_pointing_miss_appears_in_event_ring():
    ptz, health, events, verifier = _setup()
    verifier.record_move(pan_enc=2000, tilt_enc=500,
                         t=time.time() - VERIFY_DELAY_SEC - 0.1)
    verifier.tick()
    ring = events.since(0)
    kinds = [e["kind"] for e in ring]
    assert "pointing_miss" in kinds


def test_health_beat_after_poller_poll():
    """Simulate a pipeline loop: beat the health registry for ptz_poller,
    confirm the registry reports it as fresh."""
    _, health, _, _ = _setup()
    enc = (500, -100)
    health.beat("ptz_poller", {"alive": True, "enc": enc, "age_sec": 0.02})
    snap = health.snapshot(stale_after_sec=5.0)
    assert snap["components"]["ptz_poller"]["ok"] is True
    assert snap["components"]["ptz_poller"]["detail"]["enc"] == enc


def test_stale_poller_beat_flips_health_not_ok():
    """If the poller thread dies silently, /health goes not-ok."""
    _, health, _, _ = _setup()
    health.beat("ptz_poller", {"alive": True, "enc": (0, 0), "age_sec": 0.0})
    # Simulate stale beat by back-dating the timestamp
    health._last["ptz_poller"] = (time.time() - 10.0,
                                   health._last["ptz_poller"][1])
    snap = health.snapshot(stale_after_sec=5.0)
    assert snap["components"]["ptz_poller"]["ok"] is False
    assert snap["ok"] is False


def test_pointing_miss_detail_includes_error_magnitudes():
    ptz, _, events, verifier = _setup()
    verifier.record_move(pan_enc=3000, tilt_enc=0,
                         t=time.time() - VERIFY_DELAY_SEC - 0.1)
    verifier.tick()
    miss = next(e for e in events.since(0) if e["kind"] == "pointing_miss")
    assert "pan_err=" in miss["detail"]
    assert "tilt_err=" in miss["detail"]
