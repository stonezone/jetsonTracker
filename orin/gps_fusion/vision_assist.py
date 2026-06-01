"""P4 vision-assist size gate (PURE LOGIC, offline-testable).

The architecture: GPS points the camera at the subject (50-300 m offshore while
foil surfing); YOLO *refines* the aim once the subject is large enough in frame.
This module is the decision layer between the two. Each frame it answers:

  1. Which YOLO detection (if several) is the subject?  -> GPS-gated ROI:
     pick the detection nearest the GPS-predicted image position, rejecting
     boxes that are too far off or the wrong size for the GPS distance.
  2. Should we trust vision this frame?  -> size gate with hysteresis:
       GPS_ASSISTED  - subject big enough; emit a vision refinement offset.
       GPS_PRIMARY   - subject too small / no trustworthy box; aim by GPS only.

Design constraints:
  * No camera, no network, no GPS client, no YOLO here. Every input is a plain
    number or a Detection, so the whole module runs under an offline unit test
    with synthetic detections (orin/scripts/test_vision_assist.py).
  * Stateful only for the hysteresis latch (self.mode). Geometry + selection are
    free functions, independently testable.

run_tracker wires real YOLO boxes + the GPS-predicted image position into
VisionAssist.evaluate(); that live wiring is intentionally NOT done here (needs
Zack + a person in frame to validate). This module is the testable core only.

Offset convention (GPS_ASSISTED): vision_offset = (dx, dy), each in [-1, 1],
measured from image center. dx>0 => subject right of center (pan toward +).
dy>0 => subject below center (tilt toward image-down). The pointing controller
scales this into a velocity nudge; sign mapping lives there, not here.
"""

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

GPS_PRIMARY = "GPS_PRIMARY"
GPS_ASSISTED = "GPS_ASSISTED"


@dataclass
class Detection:
    """A YOLO person box in pixel coordinates of the frame YOLO ran on."""
    cx: float        # bbox center x (px)
    cy: float        # bbox center y (px)
    w: float         # bbox width (px)
    h: float         # bbox height (px)
    conf: float = 1.0


@dataclass
class FusionResult:
    mode: str                                       # GPS_PRIMARY | GPS_ASSISTED
    vision_offset: Optional[Tuple[float, float]]    # normalized [-1,1], or None
    chosen: Optional[Detection]                     # selected box, or None
    expected_h_px: float                            # GPS-predicted person height
    reason: str


def estimate_target_size_pixels(distance_m: float, vfov_deg: float,
                                frame_h_px: float, person_h_m: float = 1.7) -> float:
    """Expected on-screen height (px) of a person of `person_h_m` at
    `distance_m`, given the camera's current vertical FOV and frame height.

    Uses the true subtended angle (atan2), so it stays correct for near
    subjects, not just the small-angle regime. Returns 0.0 on bad input.
    """
    if distance_m <= 0 or vfov_deg <= 0 or frame_h_px <= 0:
        return 0.0
    subtended = 2.0 * math.atan2(person_h_m / 2.0, distance_m)   # radians
    fov = math.radians(vfov_deg)
    return frame_h_px * (subtended / fov)


def normalized_offset(det: Detection, frame_w: float, frame_h: float) -> Tuple[float, float]:
    """Detection center as an offset from image center, each axis in [-1, 1]."""
    half_w = frame_w / 2.0
    half_h = frame_h / 2.0
    return ((det.cx - half_w) / half_w, (det.cy - half_h) / half_h)


def select_by_gps_gate(dets: List[Detection], pred_x: float, pred_y: float,
                       gate_px: float, expected_h_px: float = 0.0,
                       size_tol: Tuple[float, float] = (0.35, 3.0)) -> Optional[Detection]:
    """Pick the detection nearest the GPS-predicted image position (pred_x,
    pred_y), within gate_px. If expected_h_px>0, first discard boxes whose
    height is implausible vs the GPS-derived expectation (rejects a near boat or
    a far gull masquerading as the subject). Returns None if nothing survives.
    """
    best, best_d2 = None, None
    gate2 = gate_px * gate_px
    lo, hi = size_tol
    for d in dets:
        if expected_h_px > 0 and d.h > 0:
            r = d.h / expected_h_px
            if r < lo or r > hi:
                continue
        ddx = d.cx - pred_x
        ddy = d.cy - pred_y
        d2 = ddx * ddx + ddy * ddy
        if d2 > gate2:
            continue
        if best_d2 is None or d2 < best_d2:
            best, best_d2 = d, d2
    return best


def recommend_gate_px(frame_w: float) -> float:
    """Convenience: a sane GPS gate radius ~= 15% of frame width."""
    return 0.15 * frame_w


def prisual_vfov_deg(zoom_ratio: float) -> float:
    """Approximate vertical FOV (deg) of the Prisual PTZ vs optical zoom ratio
    (1.0 .. 20.0). Log-interpolated between wide (~60 deg at 1x) and tele
    (~3.2 deg at 20x). UNVALIDATED stand-in until measured against the real
    lens during calibration; estimate_target_size_pixels takes vfov directly so
    a measured curve can replace this without touching the gate logic.
    """
    z = max(1.0, min(20.0, zoom_ratio))
    wide, tele = 60.0, 3.2
    f = math.log(z) / math.log(20.0)            # 0 at 1x, 1 at 20x
    return wide * (1.0 - f) + tele * f


class VisionAssist:
    """Stateful size gate. Holds the GPS_PRIMARY<->GPS_ASSISTED latch so the
    enter/exit thresholds form a hysteresis band (no flicker at the boundary).
    """

    def __init__(self, enter_px: float = 60.0, exit_px: float = 40.0,
                 gate_px: float = 120.0, person_h_m: float = 1.7):
        if enter_px <= exit_px:
            raise ValueError("enter_px must exceed exit_px (hysteresis band)")
        self.enter_px = enter_px
        self.exit_px = exit_px
        self.gate_px = gate_px
        self.person_h_m = person_h_m
        self.mode = GPS_PRIMARY

    def reset(self) -> None:
        self.mode = GPS_PRIMARY

    def evaluate(self, dets: List[Detection], pred_x: float, pred_y: float,
                 distance_m: float, vfov_deg: float,
                 frame_w: float, frame_h: float) -> FusionResult:
        """One frame of fusion. `pred_x/pred_y` is where GPS says the subject is
        in image pixels (image center if the camera is already aimed by GPS with
        no lag). `distance_m` + `vfov_deg` set the expected box size for gating.
        """
        expected = estimate_target_size_pixels(distance_m, vfov_deg, frame_h,
                                                self.person_h_m)
        chosen = select_by_gps_gate(dets, pred_x, pred_y, self.gate_px, expected)

        if chosen is None:
            self.mode = GPS_PRIMARY
            return FusionResult(GPS_PRIMARY, None, None, expected,
                                "no detection in GPS gate")

        # Hysteresis latch on the chosen box's observed pixel height.
        h = chosen.h
        if self.mode == GPS_PRIMARY:
            if h >= self.enter_px:
                self.mode = GPS_ASSISTED
        else:  # GPS_ASSISTED
            if h < self.exit_px:
                self.mode = GPS_PRIMARY

        if self.mode == GPS_ASSISTED:
            return FusionResult(GPS_ASSISTED,
                                normalized_offset(chosen, frame_w, frame_h),
                                chosen, expected, "vision refine")
        return FusionResult(GPS_PRIMARY, None, chosen, expected,
                            "subject too small for vision, GPS only")
