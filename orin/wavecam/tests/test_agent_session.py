"""Phase 1a: ArmState safety machine + AgentSession claude -p driver."""
from __future__ import annotations

from wavecam.agent_session import ArmState


def test_default_disarmed():
    s = ArmState(ttl_sec=600.0, now=lambda: 0.0)
    assert s.armed is False and s.killed is False and s.can_act() is False


def test_arm_then_ttl_expiry():
    t = {"v": 0.0}
    s = ArmState(ttl_sec=600.0, now=lambda: t["v"])
    s.arm()
    assert s.can_act() is True
    t["v"] = 599.0
    assert s.armed is True
    t["v"] = 601.0
    assert s.armed is False and s.can_act() is False  # auto-expired


def test_kill_disarms_and_blocks_rearm():
    s = ArmState(ttl_sec=600.0, now=lambda: 0.0)
    s.arm()
    s.kill()
    assert s.killed is True and s.can_act() is False
    s.arm()                       # re-arm attempt while killed
    assert s.can_act() is False   # refused until clear_kill()
    s.clear_kill()
    s.arm()
    assert s.can_act() is True


def test_snapshot_shape():
    s = ArmState(ttl_sec=300.0, now=lambda: 0.0)
    assert s.snapshot() == {"armed": False, "killed": False, "ttl_sec": 300.0}
