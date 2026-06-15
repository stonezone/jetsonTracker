"""Phase-0 observability (Backend Plan v3): the /status `authority` section exposes
why ownership resolved — live owner/killed plus the GPS-authority gate inputs — for
field diagnosis during the water test. Additive, no behavior change.
"""
from __future__ import annotations
import types

from wavecam.control_snapshots import build_authority
from wavecam.ptz_owner import PtzOwner


def _pipe(last_authority=None, killed=False, owner="idle"):
    o = PtzOwner()
    if owner != "idle":
        o.request(owner)
    if killed:
        o.kill()
    return types.SimpleNamespace(owner=o, _last_authority=last_authority)


def test_authority_reports_live_owner():
    auth = build_authority(_pipe(owner="vision_follow"))
    assert auth["owner"] == "vision_follow"
    assert auth["killed"] is False


def test_authority_reflects_kill_latch():
    auth = build_authority(_pipe(killed=True))
    assert auth["killed"] is True
    assert auth["owner"] == "idle"


def test_authority_exposes_gps_gate_inputs():
    auth = build_authority(_pipe(owner="gps_tracker", last_authority={
        "owner": "gps_tracker", "mode": "auto", "gps_fresh": True,
        "gps_calibrated": True, "base_locked": True, "calibration_valid": False,
        "gps_age_sec": 1.2,
    }))
    assert auth["gps_fresh"] is True
    # the gate that would deny GPS authority despite a fresh fix
    assert auth["calibration_valid"] is False
    assert auth["gps_age_sec"] == 1.2
    assert auth["mode"] == "auto"


def test_authority_safe_before_first_decision():
    auth = build_authority(_pipe(last_authority=None))
    assert auth["owner"] == "idle"
    assert auth["gps_fresh"] is None


if __name__ == "__main__":
    test_authority_reports_live_owner()
    test_authority_reflects_kill_latch()
    test_authority_exposes_gps_gate_inputs()
    test_authority_safe_before_first_decision()
    print("STATUS AUTHORITY OBSERVABILITY TESTS PASSED")
