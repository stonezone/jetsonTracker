"""Camera pose + calibration: map world bearings/elevations to VISCA encoder units.

The pointing loop computes a desired PAN bearing (deg, true) and TILT elevation
(deg) from GPS; this module converts those to raw VISCA encoder units via a
per-camera linear calibration. VISCA reports raw encoder counts (not degrees),
so calibration is empirical: two reference aims of known world geometry give the
encoder<->degree scale AND the anchor, with no camera spec sheet required.

It also fixes the camera's own geographic position (averaged from the beach
iPhone 'base' GPS, which sits next to the camera).
"""

from __future__ import annotations

import json
import math  # noqa: F401  (kept for downstream helpers/tests)
from dataclasses import asdict, dataclass
from typing import List, Optional, Tuple


def ang_diff(a: float, b: float) -> float:
    """Signed smallest difference (a - b) in degrees, within (-180, 180]."""
    d = (a - b + 180.0) % 360.0 - 180.0
    return d + 360.0 if d <= -180.0 else d


@dataclass
class CameraPose:
    # Geographic position of the camera (beach tripod).
    lat: float = 0.0
    lon: float = 0.0
    alt_m: float = 0.0
    # Pan: encoder = pan_anchor_enc + ang_diff(bearing, pan_anchor_bearing) * pan_enc_per_deg
    pan_anchor_enc: float = 0.0
    pan_anchor_bearing: float = 0.0
    pan_enc_per_deg: float = 0.0   # 0 => uncalibrated
    # Tilt: encoder = tilt_anchor_enc + (elev - tilt_anchor_elev) * tilt_enc_per_deg
    tilt_anchor_enc: float = 0.0
    tilt_anchor_elev: float = 0.0
    tilt_enc_per_deg: float = 0.0  # 0 => uncalibrated (hold tilt)

    # --- calibration (two empirical aims) ---
    def calibrate_pan_two_point(self, enc1: float, bearing1: float,
                                enc2: float, bearing2: float) -> None:
        dbear = ang_diff(bearing2, bearing1)
        if abs(dbear) < 1e-6:
            raise ValueError("pan calibration points have ~equal bearing")
        self.pan_enc_per_deg = (enc2 - enc1) / dbear
        self.pan_anchor_enc = enc1
        self.pan_anchor_bearing = bearing1

    def calibrate_tilt_two_point(self, enc1: float, elev1: float,
                                 enc2: float, elev2: float) -> None:
        if abs(elev2 - elev1) < 1e-6:
            raise ValueError("tilt calibration points have ~equal elevation")
        self.tilt_enc_per_deg = (enc2 - enc1) / (elev2 - elev1)
        self.tilt_anchor_enc = enc1
        self.tilt_anchor_elev = elev1

    # --- conversions ---
    def bearing_to_pan_encoder(self, bearing_deg: float) -> float:
        if self.pan_enc_per_deg == 0.0:
            raise RuntimeError("pan not calibrated")
        return self.pan_anchor_enc + ang_diff(bearing_deg, self.pan_anchor_bearing) * self.pan_enc_per_deg

    def elevation_to_tilt_encoder(self, elev_deg: float) -> float:
        if self.tilt_enc_per_deg == 0.0:
            return self.tilt_anchor_enc  # uncalibrated => hold a fixed tilt
        return self.tilt_anchor_enc + (elev_deg - self.tilt_anchor_elev) * self.tilt_enc_per_deg

    @property
    def calibrated(self) -> bool:
        return self.pan_enc_per_deg != 0.0

    # --- persistence ---
    def save(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls, path: str) -> "CameraPose":
        with open(path) as f:
            return cls(**json.load(f))


def lock_base_position(fixes: List[Tuple[float, float, float, Optional[float]]],
                       max_h_acc_m: float = 5.0) -> Optional[Tuple[float, float, float]]:
    """Average (lat, lon, alt, h_acc) base fixes, rejecting poor-accuracy ones.

    The beach iPhone is stationary, so a one-time average is far steadier than
    per-frame base GPS (which would wobble the aim by its ±2.5-5 m jitter).
    Returns (lat, lon, alt) or None if no fixes at all.
    """
    good = [(la, lo, al) for (la, lo, al, acc) in fixes
            if acc is not None and acc <= max_h_acc_m]
    if not good:
        good = [(la, lo, al) for (la, lo, al, _) in fixes]  # fall back to all
    if not good:
        return None
    n = len(good)
    return (sum(g[0] for g in good) / n,
            sum(g[1] for g in good) / n,
            sum(g[2] for g in good) / n)
