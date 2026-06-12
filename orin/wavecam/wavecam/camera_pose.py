"""Camera pose + calibration: map true bearings/elevations to VISCA pan/tilt encoder
units, and fix the camera's own geographic position.

Ported from the field-validated legacy ``gps_fusion/camera_pose.py``. VISCA reports raw
encoder counts (not degrees), so the mapping is an empirical linear fit: an **anchor**
``(encoder, world-angle)`` plus a **scale** (encoder-per-degree). The scale comes from
the camera's measured ``PRISUAL_PAN_ENC_PER_DEG`` for a SINGLE aim-at-remote capture,
or is derived empirically from TWO aims. Pure given its inputs; persistence is
plain JSON.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import List, Optional, Tuple

from .gps_geo import normalize_180

# Measured 2026-06-11 by driving the pan to both mechanical hard stops:
# ±2448 encoder counts over the ±170° envelope (the standard VISCA range).
# Replaces the unmeasured 4.47 folklore value, which made every
# bearing→encoder conversion ~3.2× short — GPS slews stopped a third of
# the way to the target.
PRISUAL_PAN_ENC_PER_DEG = 4896.0 / 340.0   # 14.4


@dataclass
class CameraPose:
    # Camera (beach tripod) geographic position.
    lat: float = 0.0
    lon: float = 0.0
    alt_m: float = 0.0
    # Pan:  encoder = pan_anchor_enc + normalize_180(bearing - pan_anchor_bearing) * pan_enc_per_deg
    pan_anchor_enc: float = 0.0
    pan_anchor_bearing: float = 0.0
    pan_enc_per_deg: float = 0.0   # 0 => uncalibrated
    # Tilt: encoder = tilt_anchor_enc + (elev - tilt_anchor_elev) * tilt_enc_per_deg
    tilt_anchor_enc: float = 0.0
    tilt_anchor_elev: float = 0.0
    tilt_enc_per_deg: float = 0.0  # 0 => uncalibrated (hold a fixed tilt)

    @property
    def calibrated(self) -> bool:
        """Pan-calibrated = the bearing→pan mapping is usable."""
        return self.pan_enc_per_deg != 0.0

    @property
    def has_base(self) -> bool:
        """True once a base GPS position has been latched (non-zero lat or lon)."""
        return self.lat != 0.0 or self.lon != 0.0

    # --- calibration ---
    def calibrate_pan_aim(self, enc: float, bearing_deg: float, enc_per_deg: float) -> None:
        """Single aim-at-remote: anchor ``(enc, bearing)`` with a known encoder-per-degree
        scale (the Prisual's ~4.47). Operator centres the camera on the remote, captures;
        the bearing comes from the base→remote GPS geometry, the enc from VISCA inquiry."""
        if enc_per_deg == 0.0:
            raise ValueError("enc_per_deg must be non-zero for a single-point aim")
        self.pan_anchor_enc = enc
        self.pan_anchor_bearing = bearing_deg
        self.pan_enc_per_deg = enc_per_deg

    def calibrate_pan_two_point(self, enc1: float, bearing1: float,
                                enc2: float, bearing2: float) -> None:
        """Two aims at known bearings derive the scale empirically (most accurate)."""
        dbear = normalize_180(bearing2 - bearing1)
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
    def pan_encoder_to_bearing(self, enc: float) -> Optional[float]:
        """Inverse of bearing_to_pan_encoder: encoder counts -> true bearing [0,360).
        None while uncalibrated. The estimator's vision observation depends on
        this; it was referenced (estimator.py) before it existed — test fakes
        supplied it, the real class did not, and the first locked frame with
        live encoders killed the vision loop (2026-06-11)."""
        if self.pan_enc_per_deg == 0.0:
            return None
        return (self.pan_anchor_bearing
                + (enc - self.pan_anchor_enc) / self.pan_enc_per_deg) % 360.0

    def bearing_to_pan_encoder(self, bearing_deg: float) -> float:
        if self.pan_enc_per_deg == 0.0:
            raise RuntimeError("pan not calibrated")
        return self.pan_anchor_enc + normalize_180(bearing_deg - self.pan_anchor_bearing) * self.pan_enc_per_deg

    def elevation_to_tilt_encoder(self, elev_deg: float) -> float:
        if self.tilt_enc_per_deg == 0.0:
            return self.tilt_anchor_enc  # uncalibrated => hold a fixed tilt
        return self.tilt_anchor_enc + (elev_deg - self.tilt_anchor_elev) * self.tilt_enc_per_deg

    # --- persistence ---
    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls, path: str) -> "CameraPose":
        with open(path, encoding="utf-8") as f:
            return cls(**json.load(f))


def lock_base_position(
    fixes: List[Tuple[float, float, float, Optional[float]]],
    max_h_acc_m: float = 5.0,
) -> Optional[Tuple[float, float, float]]:
    """Average ``(lat, lon, alt, h_acc)`` base fixes, rejecting poor-accuracy ones.

    The tripod is stationary, so a one-time average is far steadier than per-frame base
    GPS (whose ±2.5–5 m jitter would wobble every computed bearing). Falls back to all
    fixes if none meet the accuracy bar; returns None only when there are no fixes.
    """
    good = [(la, lo, al) for (la, lo, al, acc) in fixes if acc is not None and acc <= max_h_acc_m]
    if not good:
        good = [(la, lo, al) for (la, lo, al, _) in fixes]
    if not good:
        return None
    n = len(good)
    return (sum(g[0] for g in good) / n, sum(g[1] for g in good) / n, sum(g[2] for g in good) / n)
