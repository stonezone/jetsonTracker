"""TrackingArbiter: coarse-point → vision-refine handoff state machine.

Decides who drives the PTZ each frame — vision (velocity servo) or GPS (absolute
pan/tilt/zoom) — with hysteresis so the two don't fight. Pure logic, no I/O.

Inputs: FusionResult (vision confidence + lock), PointingTarget (GPS bearing +
calibrated encoder targets), freshness/calibration flags.
Output: source (vision_follow | gps_tracker) + optional search_roi for P2.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from .fusion import FusionResult


@dataclass
class ArbiterDecision:
    """Output of the handoff state machine for one frame."""
    owner: str                       # "vision_follow" | "gps_tracker"
    # Non-None search_roi = GPS-cued region for the detector to focus on (P2).
    # (cx, cy, w, h) in normalized frame coords [0,1].
    search_roi: Optional[Tuple[float, float, float, float]] = None


# Handoff config (tuned for surf filming at 50-300m)
DEFAULT_LOCK_FRAMES = 5       # K consecutive locked → hand to vision
DEFAULT_GRACE_SEC = 1.0       # unlock grace before falling back to GPS
DEFAULT_MAX_GPS_AGE_SEC = 10.0  # GPS considered stale beyond this


class TrackingArbiter:
    """Coarse→fine handoff between GPS absolute pointing and vision velocity servo.

    - VISION drives when FusionResult.locked (orange-confirmed person, confidence
      ≥ lock threshold): existing visual servo. owner=vision_follow.
    - GPS drives when *not* vision-locked AND GPS is fresh (age < max_age) AND
      calibrated AND base-locked: absolute coarse-point to bearing.
      owner=gps_tracker.
    - Hysteresis: K consecutive locked frames → vision; grace window before → GPS.
    - NEITHER (no lock, GPS stale/uncalibrated): owner stays idle → PTZ holds.
      GPS is RELEASED on data loss (NOT coasted) — camera stops.
    """

    def __init__(self,
                 lock_frames: int = DEFAULT_LOCK_FRAMES,
                 grace_sec: float = DEFAULT_GRACE_SEC,
                 max_gps_age_sec: float = DEFAULT_MAX_GPS_AGE_SEC,
                 mode: str = "auto",
                 enabled: bool = True):
        self.lock_frames = lock_frames
        self.grace_sec = grace_sec
        self.max_gps_age_sec = max_gps_age_sec
        self.mode = mode
        # Operator "DISABLE PTZ" latch (tracking.enabled). False = autonomous
        # tracking never claims the camera, so a manual aim holds until re-enabled.
        self.enabled = enabled
        self._consecutive_locked = 0
        self._last_locked_time: Optional[float] = None
        self._vision_owns = False  # True once vision takes over from GPS
        self._last_owner: str = "idle"

    def reset_vision_state(self) -> None:
        """Reset vision hysteresis so ownership must be re-earned from scratch.

        Call after manual operator intervention (joystick release / stop) so the
        arbiter doesn't immediately restore a stale vision_follow owner that was
        saved before the operator took over."""
        self._consecutive_locked = 0
        self._last_locked_time = None
        self._vision_owns = False
        if self._last_owner not in ("vision_follow", "gps_tracker", "idle"):
            self._last_owner = "idle"

    def decide(self,
               vision: FusionResult,
               gps_fresh: bool,
               gps_calibrated: bool,
               base_locked: bool,
               now_sec: float,
               calibration_valid: bool = False,
               capture_ok: bool = True) -> ArbiterDecision:
        """Return who drives this frame.

        Args:
            vision: FusionResult from this frame.
            gps_fresh: True if GPS target_age < max_gps_age_sec.
            gps_calibrated: True if CameraPose is calibrated (pan_enc_per_deg ≠ 0).
            base_locked: True if base GPS has a current fix (camera position known).
            now_sec: monotonic time for grace-window tracking.
            calibration_valid: True ONLY when the CURRENT CALIBRATE session is both
                valid and confirmed. Fail-closed default (False) — persisted pose
                flags survive restart/cancel/KILL and are NOT sufficient for GPS
                authority (audit 2026-06-13).
            capture_ok: False if the camera frames have gone stale (a wedged/dead
                grabber). Vision authority requires live frames; GPS pointing does
                not depend on the camera, so gps_only still works (ZOMBIE-1).
        """
        # --- Zombie-rig guard (ZOMBIE-1): a wedged grabber keeps the last frame
        # non-None, so the vision loop keeps running fusion on a frozen frame while
        # /status reports TRACKING. Vision must NOT drive the PTZ on stale frames.
        # gps_only is exempt (it doesn't use vision). Reset hysteresis so a lock is
        # re-earned once frames resume. ---
        if not capture_ok and self._tracking_mode() != "gps_only":
            self._vision_owns = False
            self._consecutive_locked = 0
            self._last_locked_time = None
            gps_viable = (gps_fresh and gps_calibrated and base_locked and calibration_valid)
            owner = "gps_tracker" if (self.enabled and gps_viable) else "idle"
            self._last_owner = owner
            roi = (0.5, 0.5, 0.5, 0.5) if owner == "gps_tracker" else None
            return ArbiterDecision(owner=owner, search_roi=roi)

        # --- DISABLE-PTZ latch: autonomy off → idle every frame, regardless of
        # mode/lock/GPS. Manual control is uncontested so a hand-aim holds until
        # the operator re-enables. Reset hysteresis so a lock must be re-earned. ---
        if not self.enabled:
            self._vision_owns = False
            self._consecutive_locked = 0
            self._last_locked_time = None
            self._last_owner = "idle"
            return ArbiterDecision(owner="idle")

        # --- GPS viability (C1: base locked; C2: CURRENT calibration session valid
        # AND confirmed — persisted pose flags alone are NOT sufficient) ---
        gps_viable = (gps_fresh and gps_calibrated and base_locked
                      and calibration_valid)
        mode = self._tracking_mode()

        if mode == "gps_only":
            self._vision_owns = False
            self._consecutive_locked = 0
            self._last_locked_time = None
            owner = "gps_tracker" if gps_viable else "idle"
            self._last_owner = owner
            roi = (0.5, 0.5, 0.5, 0.5) if owner == "gps_tracker" else None
            return ArbiterDecision(owner=owner, search_roi=roi)

        if mode == "vision_only":
            gps_viable = False
            if self._last_owner == "gps_tracker":
                self._last_owner = "idle"
                self._vision_owns = False
                self._consecutive_locked = 0

        # --- GPS→STOP on data loss (MUST run before state mutation) ---
        # If we were GPS-tracking and GPS became unviable, release to idle
        # (camera holds position, doesn't coast on stale bearing). But NOT if vision is
        # locked this frame: this short-circuit runs before the lock counting below, so a
        # single stale-GPS frame would otherwise block the GPS→vision handoff exactly when
        # vision just acquired — a visible tracking stutter (ARB-1).
        if (mode == "auto" and not gps_viable and self._last_owner == "gps_tracker"
                and not vision.locked):
            self._last_owner = "idle"
            self._vision_owns = False
            self._consecutive_locked = 0
            return ArbiterDecision(owner="idle")

        # --- vision lock counting (hysteresis) ---
        if vision.locked:
            self._consecutive_locked += 1
            self._last_locked_time = now_sec
        else:
            self._consecutive_locked = 0

        # --- decide ownership ---
        owner = self._decide_owner(vision.locked, gps_viable, now_sec)
        self._last_owner = owner
        # When GPS owns, emit a search_roi centered at frame center (the camera
        # is already pointed at the GPS target). Consumers may use this to crop
        # the detector input (P2, gps_roi_enabled flag gates the crop).
        roi = (0.5, 0.5, 0.5, 0.5) if owner == "gps_tracker" else None
        return ArbiterDecision(owner=owner, search_roi=roi)

    def _tracking_mode(self) -> str:
        mode = str(getattr(self, "mode", "auto") or "auto").strip().lower()
        return mode if mode in ("auto", "gps_only", "vision_only") else "auto"

    def _decide_owner(self, vision_locked: bool, gps_viable: bool,
                      now_sec: float) -> str:
        if vision_locked and self._consecutive_locked >= self.lock_frames:
            # K consecutive locked frames → vision takes over
            self._vision_owns = True
            return "vision_follow"

        if self._vision_owns:
            # Vision had it — check if we should release to GPS
            grace_elapsed = (
                self._last_locked_time is not None and
                (now_sec - self._last_locked_time) > self.grace_sec
            )
            if not vision_locked and grace_elapsed:
                # Vision lost lock past grace window → release
                self._vision_owns = False
                if gps_viable:
                    return "gps_tracker"
                # No GPS available either — hold position
                return "idle"
            # Still in grace window or vision re-locked
            return "vision_follow"

        # Vision hasn't claimed ownership yet or lost it
        if gps_viable:
            return "gps_tracker"

        # Neither vision locked nor GPS viable — hold position
        return "idle"
