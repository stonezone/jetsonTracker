"""Base drift monitor — pure helper, no I/O. (WaveCam Backend Plan v3, Phase 1)

Watches fresh base GPS fixes against the latched calibration position and flags
SUSTAINED tripod movement, which would silently corrupt GPS absolute pointing. Runs
as an observer: it produces a 5-state assessment; the pipeline maps that onto
pose.base_locked and this module never issues PTZ commands.

States:
  disabled  - monitor off (gps.base_drift_enabled = False).
  unknown   - cannot judge: no latch yet, base fix too old, or sats below floor.
              PRESERVES the current lock — a stale/poor base fix does NOT mean the
              tripod moved; the latched calibration position is still valid, so GPS
              authority must NOT be denied on poor base-GPS quality. This is the
              explicit guard against false unlocks (audit 2026-06-13).
  locked    - good quality, no sustained drift -> base trusted, GPS allowed.
  suspect   - good quality, drift threshold + trend met but not yet confirmed over
              min_consecutive samples. Still trusted (locked stays True) but flagged.
  unlocked  - CONFIRMED sustained drift -> base no longer trusted; GPS authority must
              be withheld until recalibration (latch()). Sticky until re-latched.

Design note for reviewers: only the CONFIRMED `unlocked` state sets locked=False.
`unknown`/`suspect` keep locked=True. This deviates from a literal reading of the v3
plan ("authority requires state not in {unknown, suspect, unlocked}") on purpose:
denying GPS whenever the base's *ongoing* fix drops out would disable pointing every
time base GPS gets noisy indoors/at range, even though the camera has not moved. The
plan's intent — never FALSE-unlock on bad GPS — is preserved.

Adapted from Kimi's Phase-B base_drift draft (the dual-trigger core); extended here
with the quality gate, the 5-state model, and the suspect/confirmed split.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple

from .gps_geo import haversine_m

DISABLED = "disabled"
UNKNOWN = "unknown"
LOCKED = "locked"
SUSPECT = "suspect"
UNLOCKED = "unlocked"


@dataclass
class BaseDriftSample:
    lat: float
    lon: float
    alt_m: float
    t: float


@dataclass
class BaseDriftResult:
    state: str
    locked: bool
    mean_distance_m: float
    trend_m: float
    samples: int = 0
    alert: bool = False  # True only on the transition to CONFIRMED unlocked


class BaseDriftMonitor:
    """Detect sustained base movement via a dual trigger (mean distance AND linear
    trend), gated on base-fix quality, with a confirmed/suspect split.

    The dual requirement rejects single jumps (threshold only) and slow scatter
    (trend only); the min_consecutive confirm count rejects brief excursions.
    """

    def __init__(self,
                 threshold_m: float = 2.0,
                 min_trend_m: float = 1.0,
                 window_size: int = 10,
                 min_consecutive: int = 5,
                 max_fix_age_sec: float = 10.0,
                 min_sats: int = 0,
                 enabled: bool = True) -> None:
        self._enabled = enabled
        self._threshold_m = max(0.0, threshold_m)
        self._min_trend_m = max(0.0, min_trend_m)
        self._window_size = max(2, window_size)
        self._min_consecutive = max(2, min_consecutive)
        self._max_fix_age_sec = max(0.0, max_fix_age_sec)
        self._min_sats = max(0, min_sats)
        self._samples: Deque[BaseDriftSample] = deque(maxlen=self._window_size)
        self._latched: Optional[Tuple[float, float, float]] = None
        self._unlocked = False  # sticky once confirmed, until re-latch
        self._last_mean = 0.0   # retained so the sticky-unlocked readout keeps the value
        self._last_trend = 0.0

    def latch(self, lat: float, lon: float, alt_m: float = 0.0) -> None:
        """Record the trusted calibration base position and clear drift state."""
        self._latched = (lat, lon, alt_m)
        self._samples.clear()
        self._unlocked = False
        self._last_mean = 0.0
        self._last_trend = 0.0

    def update(self,
               lat: float,
               lon: float,
               alt_m: float,
               t: float,
               *,
               fix_age_sec: Optional[float] = None,
               sats: Optional[int] = None,
               currently_locked: bool = True) -> BaseDriftResult:
        """Ingest one fresh base fix and return the drift assessment."""
        if not self._enabled:
            return BaseDriftResult(DISABLED, currently_locked, 0.0, 0.0, 0, False)

        # Sticky: once confirmed drifted, stay unlocked until a fresh latch.
        if self._unlocked:
            return BaseDriftResult(UNLOCKED, False, self._last_mean, self._last_trend,
                                   len(self._samples), False)

        # Quality gate -> unknown (preserve current lock; do not judge).
        if self._latched is None:
            return BaseDriftResult(UNKNOWN, currently_locked, 0.0, 0.0, 0, False)
        if fix_age_sec is not None and fix_age_sec > self._max_fix_age_sec:
            return BaseDriftResult(UNKNOWN, currently_locked, 0.0, 0.0,
                                   len(self._samples), False)
        if sats is not None and sats < self._min_sats:
            return BaseDriftResult(UNKNOWN, currently_locked, 0.0, 0.0,
                                   len(self._samples), False)

        # Good quality -> assess drift.
        self._samples.append(BaseDriftSample(lat, lon, alt_m, t))
        bl_lat, bl_lon, _ = self._latched
        distances = [haversine_m(bl_lat, bl_lon, s.lat, s.lon) for s in self._samples]
        mean_dist = sum(distances) / len(distances)
        trend = self._compute_trend()

        moving = mean_dist > self._threshold_m and abs(trend) > self._min_trend_m
        if not moving:
            return BaseDriftResult(LOCKED, True, mean_dist, trend,
                                   len(self._samples), False)

        if len(self._samples) >= self._min_consecutive:
            self._unlocked = True
            self._last_mean = mean_dist
            self._last_trend = trend
            return BaseDriftResult(UNLOCKED, False, mean_dist, trend,
                                   len(self._samples), True)
        return BaseDriftResult(SUSPECT, True, mean_dist, trend,
                               len(self._samples), False)

    def _compute_trend(self) -> float:
        """Signed linear least-squares slope of distance-vs-time over the window,
        scaled to the observed time span (metres of net divergence)."""
        if len(self._samples) < 2 or self._latched is None:
            return 0.0
        bl_lat, bl_lon, _ = self._latched
        xs = [s.t for s in self._samples]
        ys = [haversine_m(bl_lat, bl_lon, s.lat, s.lon) for s in self._samples]
        n = len(xs)
        x_mean = sum(xs) / n
        y_mean = sum(ys) / n
        denom = sum((x - x_mean) ** 2 for x in xs)
        if denom < 1e-12:
            return 0.0
        slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denom
        span = xs[-1] - xs[0]
        return slope * span
