"""GPS-driven zoom curve — pure helper, no I/O. (WaveCam Backend Plan v3, Phase 4)

Maps target distance → zoom encoder. GPS_TRACKER-owner-only (this module returns a
value; the pipeline applies it only when GPS_TRACKER owns). Rate-limited to avoid
abrupt FOV jumps. Defaults disabled so no zoom is driven until explicitly enabled.

CONFIG KEYS (for wiring into GpsCfg):
    drive_zoom_enabled: bool = False
    drive_zoom_near_m: float = 40.0     # distance → widest zoom
    drive_zoom_far_m: float = 250.0     # distance → max zoom
    drive_zoom_max_frac: float = 0.60   # conservative ceiling (first water test)
    drive_zoom_max_enc: float = 16384.0  # camera zoom encoder max
    drive_zoom_min_enc: float = 0.0      # camera zoom encoder min
    drive_zoom_rate_limit: float = 300.0  # max encoder change per call
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ZoomCurveConfig:
    enabled: bool = False
    near_m: float = 40.0
    far_m: float = 250.0
    max_frac: float = 0.60
    max_enc: float = 16384.0
    min_enc: float = 0.0
    rate_limit: float = 300.0


class DriveZoom:
    """Distance→zoom mapping with rate-limited smoothing.

    Pure computation — never issues commands. The pipeline reads ``compute()``
    and applies the returned value only when GPS_TRACKER owns the PTZ."""

    def __init__(self, cfg: ZoomCurveConfig) -> None:
        self._cfg = cfg
        self._current: Optional[float] = None

    def compute(self, distance_m: Optional[float]) -> Optional[float]:
        """Return the desired zoom encoder for *distance_m*, or None.

        Returns None when disabled, distance is None, or distance is ≤ 0.
        Rate-limits against the previous return so zoom ramps smoothly."""
        if not self._cfg.enabled or distance_m is None or distance_m <= 0:
            return None

        span = max(self._cfg.far_m - self._cfg.near_m, 1.0)
        frac = min(1.0, max(0.0, (distance_m - self._cfg.near_m) / span))
        target = frac * self._cfg.max_frac * self._cfg.max_enc

        target = max(self._cfg.min_enc, min(self._cfg.max_enc, target))

        if self._current is not None:
            delta = target - self._current
            if abs(delta) > self._cfg.rate_limit:
                target = self._current + (
                    self._cfg.rate_limit if delta > 0 else -self._cfg.rate_limit
                )

        self._current = target
        return target

    def reset(self) -> None:
        """Clear smoothed state on mode change or tracking stop."""
        self._current = None

    @property
    def current(self) -> Optional[float]:
        return self._current
