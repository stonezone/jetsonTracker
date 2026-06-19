"""Unit tests for TrackingArbiter — no hardware required."""
from wavecam.tracking_arbiter import TrackingArbiter
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
    d = a.decide(_vision(False), gps_fresh=False, gps_calibrated=False, base_locked=False, now_sec=0.0)
    assert d.owner == "idle"


def test_gps_when_fresh_and_calibrated_no_vision():
    a = TrackingArbiter()
    d = a.decide(_vision(False), gps_fresh=True, gps_calibrated=True, base_locked=True, now_sec=0.0, calibration_valid=True)
    assert d.owner == "gps_tracker"


def test_vision_when_no_gps():
    # Vision locked but GPS not viable — vision should own
    a = TrackingArbiter(lock_frames=1)
    d = a.decide(_vision(True, 0.9), gps_fresh=False, gps_calibrated=True, base_locked=False, now_sec=0.0)
    assert d.owner == "vision_follow"


def test_gps_wins_when_uncalibrated():
    a = TrackingArbiter()
    d = a.decide(_vision(False), gps_fresh=True, gps_calibrated=False, base_locked=False, now_sec=0.0)
    assert d.owner == "idle"  # GPS not viable without calibration


# --- SAFETY: calibration-session validity gate (audit 2026-06-13) ---

def test_gps_denied_when_calibration_invalid():
    """Fresh + calibrated + base_locked must NOT grant GPS authority when the current
    CALIBRATE session is not valid+confirmed — neither in auto nor gps_only mode. A
    persisted stale pose must fail closed to idle."""
    a = TrackingArbiter()
    d = a.decide(_vision(False), gps_fresh=True, gps_calibrated=True,
                 base_locked=True, now_sec=0.0, calibration_valid=False)
    assert d.owner == "idle"

    g = TrackingArbiter(mode="gps_only")
    d2 = g.decide(_vision(False), gps_fresh=True, gps_calibrated=True,
                  base_locked=True, now_sec=0.0, calibration_valid=False)
    assert d2.owner == "idle"
    assert d2.search_roi is None


def test_calibration_valid_defaults_fail_closed():
    """Omitting calibration_valid yields NO GPS authority (fail-closed default)."""
    a = TrackingArbiter()
    d = a.decide(_vision(False), gps_fresh=True, gps_calibrated=True,
                 base_locked=True, now_sec=0.0)
    assert d.owner == "idle"


# --- hysteresis ---

def test_vision_takes_over_after_k_consecutive_locks():
    a = TrackingArbiter(lock_frames=3)
    # Two locks — not enough, stay on GPS
    d1 = a.decide(_vision(True), gps_fresh=True, gps_calibrated=True, base_locked=True, now_sec=0.0, calibration_valid=True)
    assert d1.owner == "gps_tracker"
    d2 = a.decide(_vision(True), gps_fresh=True, gps_calibrated=True, base_locked=True, now_sec=0.1, calibration_valid=True)
    assert d2.owner == "gps_tracker"
    # Third lock — hand to vision
    d3 = a.decide(_vision(True), gps_fresh=True, gps_calibrated=True, base_locked=True, now_sec=0.2, calibration_valid=True)
    assert d3.owner == "vision_follow"


def test_vision_holds_through_brief_unlock():
    a = TrackingArbiter(lock_frames=2, grace_sec=1.0)
    # Lock in vision
    a.decide(_vision(True), gps_fresh=True, gps_calibrated=True, base_locked=True, now_sec=0.0, calibration_valid=True)
    a.decide(_vision(True), gps_fresh=True, gps_calibrated=True, base_locked=True, now_sec=0.1, calibration_valid=True)
    # Brief unlock within grace
    d = a.decide(_vision(False), gps_fresh=True, gps_calibrated=True, base_locked=True, now_sec=0.2, calibration_valid=True)
    assert d.owner == "vision_follow"  # still held


def test_vision_releases_after_grace():
    a = TrackingArbiter(lock_frames=2, grace_sec=0.5)
    a.decide(_vision(True), gps_fresh=True, gps_calibrated=True, base_locked=True, now_sec=0.0, calibration_valid=True)
    a.decide(_vision(True), gps_fresh=True, gps_calibrated=True, base_locked=True, now_sec=0.1, calibration_valid=True)
    a.decide(_vision(False), gps_fresh=True, gps_calibrated=True, base_locked=True, now_sec=0.2, calibration_valid=True)
    # After grace expires + GPS viable → GPS takes over
    d = a.decide(_vision(False), gps_fresh=True, gps_calibrated=True, base_locked=True, now_sec=1.0, calibration_valid=True)
    assert d.owner == "gps_tracker"


# --- GPS loss → STOP ---

def test_gps_loss_stops_when_gps_was_owning():
    a = TrackingArbiter()
    # GPS owns
    d1 = a.decide(_vision(False), gps_fresh=True, gps_calibrated=True, base_locked=True, now_sec=0.0, calibration_valid=True)
    assert d1.owner == "gps_tracker"
    # GPS becomes stale — should release to idle (STOP)
    d2 = a.decide(_vision(False), gps_fresh=False, gps_calibrated=True, base_locked=False, now_sec=0.5)
    assert d2.owner == "idle"


def test_gps_stale_does_not_take_over_from_vision():
    a = TrackingArbiter(lock_frames=1)
    a.decide(_vision(True), gps_fresh=True, gps_calibrated=True, base_locked=True, now_sec=0.0, calibration_valid=True)
    # Vision locked, GPS becomes stale — vision keeps ownership
    d = a.decide(_vision(True), gps_fresh=False, gps_calibrated=True, base_locked=False, now_sec=0.5)
    assert d.owner == "vision_follow"


# --- edge cases ---

def test_vision_reacquired_within_grace_resets_timer():
    a = TrackingArbiter(lock_frames=2, grace_sec=1.0)
    a.decide(_vision(True), gps_fresh=True, gps_calibrated=True, base_locked=True, now_sec=0.0, calibration_valid=True)
    a.decide(_vision(True), gps_fresh=True, gps_calibrated=True, base_locked=True, now_sec=0.1, calibration_valid=True)
    # Unlock at 0.2, re-lock at 0.4 (within 1.0 grace)
    a.decide(_vision(False), gps_fresh=True, gps_calibrated=True, base_locked=True, now_sec=0.2, calibration_valid=True)
    d = a.decide(_vision(True), gps_fresh=True, gps_calibrated=True, base_locked=True, now_sec=0.4, calibration_valid=True)
    # Still vision_follow — re-locked within grace
    assert d.owner == "vision_follow"


def test_idle_sticks_when_neither_improves():
    a = TrackingArbiter()
    a.decide(_vision(False), gps_fresh=False, gps_calibrated=False, base_locked=False, now_sec=0.0)
    a.decide(_vision(False), gps_fresh=False, gps_calibrated=False, base_locked=False, now_sec=1.0)
    d = a.decide(_vision(False), gps_fresh=False, gps_calibrated=False, base_locked=False, now_sec=2.0)
    assert d.owner == "idle"


def test_arbiter_decision_fields():
    a = TrackingArbiter()
    d = a.decide(_vision(False), gps_fresh=True, gps_calibrated=True, base_locked=True, now_sec=0.0, calibration_valid=True)
    assert d.owner == "gps_tracker"
    # P2 (Package 3) now populates search_roi when GPS is tracking (gps_roi_enabled flag gates the crop)
    assert d.search_roi is not None
    cx, cy, w, h = d.search_roi
    assert 0.0 <= cx <= 1.0 and 0.0 <= cy <= 1.0


def test_gps_only_mode_forces_gps_even_when_vision_locked():
    a = TrackingArbiter(lock_frames=1, mode="gps_only")

    d = a.decide(_vision(True, 0.9), gps_fresh=True, gps_calibrated=True,
                 base_locked=True, now_sec=0.0, calibration_valid=True)

    assert d.owner == "gps_tracker"
    assert d.search_roi is not None


def test_gps_only_mode_releases_to_idle_when_gps_not_viable():
    a = TrackingArbiter(lock_frames=1, mode="gps_only")

    d = a.decide(_vision(True, 0.9), gps_fresh=False, gps_calibrated=True,
                 base_locked=True, now_sec=0.0, calibration_valid=True)

    assert d.owner == "idle"
    assert d.search_roi is None


def test_vision_only_mode_never_uses_gps():
    a = TrackingArbiter(mode="vision_only")

    d = a.decide(_vision(False), gps_fresh=True, gps_calibrated=True,
                 base_locked=True, now_sec=0.0, calibration_valid=True)

    assert d.owner == "idle"
    assert d.search_roi is None


def test_vision_only_mode_allows_vision_after_lock_hysteresis():
    a = TrackingArbiter(lock_frames=1, mode="vision_only")

    d = a.decide(_vision(True, 0.9), gps_fresh=True, gps_calibrated=True,
                 base_locked=True, now_sec=0.0, calibration_valid=True)

    assert d.owner == "vision_follow"
    assert d.search_roi is None


def test_just_locked_vision_survives_a_stale_gps_frame():
    # ARB-1: the GPS->idle short-circuit ran before vision-lock counting, so a single
    # stale-GPS frame blocked the GPS->vision handoff exactly when vision just locked.
    a = TrackingArbiter(lock_frames=1)
    # Frame 1: GPS viable, no vision -> gps_tracker (sets _last_owner=gps_tracker)
    d = a.decide(_vision(False), gps_fresh=True, gps_calibrated=True,
                 base_locked=True, now_sec=0.0, calibration_valid=True)
    assert d.owner == "gps_tracker"
    # Frame 2: GPS goes stale AND vision locks the same frame -> hand off, don't idle.
    d = a.decide(_vision(True, 0.9), gps_fresh=False, gps_calibrated=True,
                 base_locked=True, now_sec=0.1, calibration_valid=True)
    assert d.owner == "vision_follow"


print("ARBITER TESTS PASSED")
