"""
Visual servo: turn target image-position error into a PTZ velocity command, plus
person-box-gated zoom. No I/O.

compute(): P controller with a center deadzone (speed scales min..max across
deadzone..1) + optional feed-forward lead (ff_gain) that anticipates motion, with
a jump-guard that ignores detection switches.
compute_zoom(): drives a YOLO person box toward target_frac of the frame height;
holds zoom (stop) on color-only frames.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Tuple

from .ptz_visca import PAN_LEFT, PAN_RIGHT, PAN_STOP, TILT_UP, TILT_DOWN, TILT_STOP


@dataclass
class PtzCommand:
    pan_speed: int
    tilt_speed: int
    pan_dir: int
    tilt_dir: int

    def key(self) -> Tuple[int, int, int, int]:
        """Quantized identity for de-duping repeat sends."""
        return (self.pan_speed, self.tilt_speed, self.pan_dir, self.tilt_dir)

    @property
    def is_stop(self) -> bool:
        return self.pan_dir == PAN_STOP and self.tilt_dir == TILT_STOP


STOP_CMD = PtzCommand(1, 1, PAN_STOP, TILT_STOP)


class VisualServo:
    def __init__(self, cfg):
        self.cfg = cfg
        self._last = None      # last (ex, ey) image error, for feed-forward lead

    def _map_speed(self, err_abs: float, max_speed: int) -> int:
        dz = self.cfg.deadzone
        span = max(1e-6, 1.0 - dz)
        frac = max(0.0, min(1.0, (err_abs - dz) / span))
        spd = self.cfg.min_speed + (max_speed - self.cfg.min_speed) * frac
        return int(round(max(self.cfg.min_speed, min(max_speed, spd))))

    def _lead(self, ex: float, ey: float) -> Tuple[float, float]:
        """Feed-forward: bias the error by its inter-frame change so the camera
        anticipates motion. Off when ff_gain is unset/0. A big jump is a detection
        switch (not real motion), so it skips the lead. Near-center jitter is
        also ignored so feed-forward cannot pull the camera out of deadzone."""
        g = getattr(self.cfg, "ff_gain", 0.0) or 0.0
        last = self._last
        self._last = (ex, ey)
        if g <= 0 or last is None:
            return ex, ey
        dex, dey = ex - last[0], ey - last[1]
        if abs(dex) > 0.45 or abs(dey) > 0.45:
            return ex, ey
        lead_zone = self.cfg.deadzone * max(1.0, getattr(self.cfg, "ff_deadzone_mult", 1.5))
        lead_x = 0.0 if abs(ex) <= lead_zone or abs(last[0]) <= lead_zone else g * dex
        lead_y = 0.0 if abs(ey) <= lead_zone or abs(last[1]) <= lead_zone else g * dey
        return ex + lead_x, ey + lead_y

    def compute(self, target_xy: Optional[Tuple[float, float]],
                frame_wh: Tuple[int, int]) -> PtzCommand:
        """Return the velocity command to center target_xy. None target -> STOP."""
        if target_xy is None:
            self._last = None
            return STOP_CMD

        w, h = frame_wh
        ex = (target_xy[0] - w / 2.0) / (w / 2.0)   # -1 (left) .. +1 (right)
        ey = (target_xy[1] - h / 2.0) / (h / 2.0)   # -1 (top)  .. +1 (bottom)
        ex, ey = self._lead(ex, ey)                 # feed-forward anticipation

        if self.cfg.invert_pan:
            ex = -ex
        if self.cfg.invert_tilt:
            ey = -ey

        pan_dir, tilt_dir = PAN_STOP, TILT_STOP
        pan_speed = tilt_speed = self.cfg.min_speed

        if abs(ex) > self.cfg.deadzone:
            pan_dir = PAN_RIGHT if ex > 0 else PAN_LEFT
            pan_speed = self._map_speed(abs(ex), self.cfg.max_pan_speed)

        if abs(ey) > self.cfg.deadzone:
            # image y grows downward: target below center -> tilt down
            tilt_dir = TILT_DOWN if ey > 0 else TILT_UP
            tilt_speed = self._map_speed(abs(ey), self.cfg.max_tilt_speed)

        if pan_dir == PAN_STOP and tilt_dir == TILT_STOP:
            return STOP_CMD
        return PtzCommand(pan_speed, tilt_speed, pan_dir, tilt_dir)

    def compute_zoom(self, person_bbox: Optional[Tuple[int, int, int, int]],
                     frame_h: int) -> Tuple[str, int]:
        """Zoom only off a YOLO person box; HOLD (stop) on color-only frames.
        Drives the person bbox height toward target_frac of the frame height."""
        if not person_bbox or frame_h <= 0:
            return "stop", 0
        target = getattr(self.cfg, "zoom_target_frac", getattr(self.cfg, "target_frac", 0.5))
        dz = getattr(self.cfg, "zoom_deadband", 0.06)
        zmax = int(getattr(self.cfg, "zoom_max_speed", getattr(self.cfg, "zoom_max", 5)))
        frac = person_bbox[3] / float(frame_h)
        err = target - frac
        if abs(err) <= dz:
            return "stop", 0
        speed = max(1, min(zmax, int(round(abs(err) / max(1e-6, target) * zmax))))
        return ("tele" if err > 0 else "wide"), speed
