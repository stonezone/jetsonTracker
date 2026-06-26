"""GPS-bearing → fusion cue region. Pure helper, no I/O. (WaveCam Backend Plan v3, Phase 3)

When the camera is GPS-pointed at a target but vision has not yet locked, this module
computes a probabilistic ROI on the frame where the subject is expected to appear.
Fusion uses the cue to boost blob/person confidence inside the region; it NEVER issues
PTZ commands.

Adapted from Kimi's Phase-B draft, with an off-screen gate added: when the target is
beyond the frame edge plus a tolerance, the cue is omitted — at that point GPS pointing
should re-aim the camera, not nudge fusion toward an off-screen spot.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from .gps_geo import normalize_180


@dataclass
class BearingCue:
    cx: float
    cy: float
    radius_px: float


def _fov_at_zoom(fov_curve: List[Tuple[int, float]], zoom_enc: int) -> float:
    """Linear interpolation of horizontal FOV (degrees) from the calibration curve."""
    if not fov_curve:
        return 60.0
    if zoom_enc <= fov_curve[0][0]:
        return fov_curve[0][1]
    for i in range(1, len(fov_curve)):
        z0, f0 = fov_curve[i - 1]
        z1, f1 = fov_curve[i]
        if zoom_enc <= z1:
            t = (zoom_enc - z0) / max(1, z1 - z0)
            return f0 + t * (f1 - f0)
    return fov_curve[-1][1]


def compute_bearing_cue(
    target_bearing_deg: float,
    current_bearing_deg: float,
    fov_curve: List[Tuple[int, float]],
    zoom_enc: int,
    frame_w: int,
    frame_h: int,
    bearing_uncertainty_deg: float = 5.0,
    min_radius_px: float = 20.0,
    max_radius_px: float = 320.0,
    max_offscreen_deg: float = 10.0,
) -> Optional[BearingCue]:
    """Return the frame region where the GPS-predicted subject should appear, or None.

    Args:
        target_bearing_deg: true bearing from base to subject.
        current_bearing_deg: true bearing the camera is currently aimed at.
        fov_curve: list of (zoom_enc, hfov_deg) calibration points.
        zoom_enc: current zoom encoder value.
        frame_w, frame_h: frame dimensions in pixels.
        bearing_uncertainty_deg: expected bearing std (scales the cue radius).
        min_radius_px, max_radius_px: clamp radius for very narrow / wide FOV.
        max_offscreen_deg: tolerance past the frame edge before the cue is omitted.

    Returns None when the frame is empty, the FOV curve is empty, or the target is
    too far outside the frame to be a useful in-frame bias.
    """
    if frame_w <= 0 or frame_h <= 0:
        return None
    if not fov_curve:
        return None
    hfov = _fov_at_zoom(fov_curve, zoom_enc)
    if hfov <= 0:
        return None

    # Bearing error, wrap-safe (359° vs 1° = +2°, not -358°).
    bearing_error = normalize_180(target_bearing_deg - current_bearing_deg)

    # Off-screen gate: omit the cue when the target is beyond the frame edge plus
    # the tolerance — GPS pointing should re-aim rather than bias fusion off-frame.
    if abs(bearing_error) > (hfov / 2.0) + max_offscreen_deg:
        return None

    px_per_deg = frame_w / hfov
    cx = frame_w / 2.0 + bearing_error * px_per_deg
    cy = frame_h / 2.0
    radius_px = max(min_radius_px, min(max_radius_px, bearing_uncertainty_deg * px_per_deg))
    return BearingCue(cx=cx, cy=cy, radius_px=radius_px)


def bearing_residual(
    target_bearing_deg: float,
    current_bearing_deg: float,
    vision_target_x_frac: float,
    fov_curve: List[Tuple[int, float]],
    zoom_enc: int,
    frame_w: int,
) -> Tuple[float, float]:
    """Angular disagreement (deg) + pixel offset between where VISION sees the subject
    (``vision_target_x_frac`` in [0,1], 0.5 = frame center) and where GPS says it is
    (``target_bearing_deg``). 0 = perfect agreement. Observe-only measurement of the
    GPS-vs-vision pointing gap (the cal-vs-FOV gap) — pure, never feeds pointing/fusion."""
    hfov = _fov_at_zoom(fov_curve, zoom_enc)
    vision_bearing = current_bearing_deg + (vision_target_x_frac - 0.5) * hfov
    deg = normalize_180(vision_bearing - target_bearing_deg)
    px = deg * (frame_w / hfov) if hfov > 0 else 0.0
    return deg, px
