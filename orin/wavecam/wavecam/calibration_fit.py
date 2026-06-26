"""Pure least-squares pan-offset fit for the multi-point calibration refine.

Averages the pan OFFSET across aims taken at varied bearings (encoder scale stays
FIXED — the hard-stop-measured 14.4 c/deg is authoritative; fitting it from noisy
GPS would reintroduce the 4.47-class error). Bearings are unwrapped around their
circular mean so aims that straddle north (350°/10°) don't produce a full-range
jump. No I/O — fully unit-testable; the caller applies the result via
CameraPose.calibrate_pan_aim.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class PanOffsetFit:
    anchor_enc: float
    anchor_bearing_deg: float
    rms_residual_deg: float
    worst_residual_deg: float
    residuals_deg: list[float]
    sample_count: int


def _angdiff(a: float, b: float) -> float:
    """Signed shortest angular difference a−b in (−180, 180]."""
    return ((a - b + 180.0) % 360.0) - 180.0


def fit_pan_offset(samples: list[tuple[float, float]], enc_per_deg: float) -> PanOffsetFit:
    """Fit (anchor_enc, anchor_bearing) for ``pose.bearing_to_pan_encoder`` with a FIXED
    scale, from samples of ``(pan_enc, bearing_deg)``.

    Anchors at the circular-mean bearing and averages each sample's encoder projected to
    that bearing; ``residuals_deg`` is each sample's miss (= how far that aim disagrees
    with the fitted offset, in degrees), and ``rms_residual_deg`` summarizes spread.
    """
    if not samples:
        raise ValueError("fit_pan_offset requires at least one sample")
    s = float(enc_per_deg)
    sin_sum = sum(math.sin(math.radians(b)) for _, b in samples)
    cos_sum = sum(math.cos(math.radians(b)) for _, b in samples)
    anchor_bearing = math.degrees(math.atan2(sin_sum, cos_sum)) % 360.0
    projected = [enc - _angdiff(b, anchor_bearing) * s for enc, b in samples]
    anchor_enc = sum(projected) / len(projected)
    residuals = [(p - anchor_enc) / s for p in projected]
    rms = math.sqrt(sum(r * r for r in residuals) / len(residuals))
    worst = max((abs(r) for r in residuals), default=0.0)
    return PanOffsetFit(
        anchor_enc=anchor_enc,
        anchor_bearing_deg=anchor_bearing,
        rms_residual_deg=rms,
        worst_residual_deg=worst,
        residuals_deg=residuals,
        sample_count=len(samples),
    )
