"""Unit tests for TrackingArbiter — no hardware required."""
from wavecam.tracking_arbiter import TrackingArbiter, ArbiterDecision
from wavecam.fusion import FusionResult


# --- helpers ---

def _vision(locked: bool, conf: float = 0.5) -> FusionResult:
    return FusionResult(
        target_xy=(0.5, 0.5),
        bbox=None, person_bbox=None,
        conf=conf, locked=locked,
        state="TRACKING" if locked else "SEARCHING",
        has_color=True, has_person=True, matched=locked,
    )


# --- basic handoff ---

def test_idle_when_no_vision_and_no_gps():
    a = TrackingArbiter()
    d = a.decide(_vision(False), gps_fresh=False, gps_calibrated=False, now_sec=0.0)
    assert d.owner == "idle"


def test_gps_when_fresh_and_calibrated_no_vision():
    a = TrackingArbiter()
    d = a.decide(_vision(False), gps_fresh=True, gps_calibrated=True, now_sec=0.0)
    assert d.owner == "gps_tracker"


def test_vision_when_no_gps():
    # Vision locked but GPS not viable — vision should own
    a = TrackingArbiter(lock_frames=1)
    d = a.decide(_vision(True, 0.9), gps_fresh=False, gps_calibrated=True, now_sec=0.0)
    assert d.owner == "vision_follow"


def test_gps_wins_when_uncalibrated():
    a = TrackingArbiter()
    d = a.decide(_vision(False), gps_fresh=True, gps_calibrated=False, now_sec=0.0)
    assert d.owner == "idle"  # GPS not viable without calibration


# --- hysteresis ---

def test_vision_takes_over_after_k_consecutive_locks():
    a = TrackingArbiter(lock_frames=3)
    # Two locks — not enough, stay on GPS
    d1 = a.decide(_vision(True), gps_fresh=True, gps_calibrated=True, now_sec=0.0)
    assert d1.owner == "gps_tracker"
    d2 = a.decide(_vision(True), gps_fresh=True, gps_calibrated=True, now_sec=0.1)
    assert d2.owner == "gps_tracker"
    # Third lock — hand to vision
    d3 = a.decide(_vision(True), gps_fresh=True, gps_calibrated=True, now_sec=0.2)
    assert d3.owner == "vision_follow"


def test_vision_holds_through_brief_unlock():
    a = TrackingArbiter(lock_frames=2, grace_sec=1.0)
    # Lock in vision
    a.decide(_vision(True), gps_fresh=True, gps_calibrated=True, now_sec=0.0)
    a.decide(_vision(True), gps_fresh=True, gps_calibrated=True, now_sec=0.1)
    # Brief unlock within grace
    d = a.decide(_vision(False), gps_fresh=True, gps_calibrated=True, now_sec=0.2)
    assert d.owner == "vision_follow"  # still held


def test_vision_releases_after_grace():
    a = TrackingArbiter(lock_frames=2, grace_sec=0.5)
    a.decide(_vision(True), gps_fresh=True, gps_calibrated=True, now_sec=0.0)
    a.decide(_vision(True), gps_fresh=True, gps_calibrated=True, now_sec=0.1)
    a.decide(_vision(False), gps_fresh=True, gps_calibrated=True, now_sec=0.2)
    # After grace expires + GPS viable → GPS takes over
    d = a.decide(_vision(False), gps_fresh=True, gps_calibrated=True, now_sec=1.0)
    assert d.owner == "gps_tracker"


# --- GPS loss → STOP ---

def test_gps_loss_stops_when_gps_was_owning():
    a = TrackingArbiter()
    # GPS owns
    d1 = a.decide(_vision(False), gps_fresh=True, gps_calibrated=True, now_sec=0.0)
    assert d1.owner == "gps_tracker"
    # GPS becomes stale — should release to idle (STOP)
    d2 = a.decide(_vision(False), gps_fresh=False, gps_calibrated=True, now_sec=0.5)
    assert d2.owner == "idle"


def test_gps_stale_does_not_take_over_from_vision():
    a = TrackingArbiter(lock_frames=1)
    a.decide(_vision(True), gps_fresh=True, gps_calibrated=True, now_sec=0.0)
    # Vision locked, GPS becomes stale — vision keeps ownership
    d = a.decide(_vision(True), gps_fresh=False, gps_calibrated=True, now_sec=0.5)
    assert d.owner == "vision_follow"


# --- edge cases ---

def test_vision_reacquired_within_grace_resets_timer():
    a = TrackingArbiter(lock_frames=2, grace_sec=1.0)
    a.decide(_vision(True), gps_fresh=True, gps_calibrated=True, now_sec=0.0)
    a.decide(_vision(True), gps_fresh=True, gps_calibrated=True, now_sec=0.1)
    # Unlock at 0.2, re-lock at 0.4 (within 1.0 grace)
    a.decide(_vision(False), gps_fresh=True, gps_calibrated=True, now_sec=0.2)
    d = a.decide(_vision(True), gps_fresh=True, gps_calibrated=True, now_sec=0.4)
    # Still vision_follow — re-locked within grace
    assert d.owner == "vision_follow"


def test_idle_sticks_when_neither_improves():
    a = TrackingArbiter()
    a.decide(_vision(False), gps_fresh=False, gps_calibrated=False, now_sec=0.0)
    a.decide(_vision(False), gps_fresh=False, gps_calibrated=False, now_sec=1.0)
    d = a.decide(_vision(False), gps_fresh=False, gps_calibrated=False, now_sec=2.0)
    assert d.owner == "idle"


def test_arbiter_decision_fields():
    a = TrackingArbiter()
    d = a.decide(_vision(False), gps_fresh=True, gps_calibrated=True, now_sec=0.0)
    assert d.owner == "gps_tracker"
    assert d.search_roi is None  # reserved for P2


print("ARBITER TESTS PASSED")
