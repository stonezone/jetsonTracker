"""GPS -> camera pointing controller.

Turns base (camera) + target (subject) GeoPoints into a VISCA move that keeps the
subject framed. Pan is primary (a distant surfer moves mostly in azimuth); tilt and
zoom are optional. Speed/course LEAD the target through GPS lag; proportional
velocity + feed-forward give smooth tracking; a large error snaps via absolute.
Distance drives optical zoom (farther => tele), with margin so a fast subject does
not blow out of frame.

Validated sign conventions (live): +pan velocity increases the pan encoder;
+zoom velocity (tele) increases the zoom encoder.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Optional

from .camera_pose import CameraPose, ang_diff
from .geo_calc import GeoPoint, calculate_bearing, haversine_distance, predict_position


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


@dataclass
class PointingStatus:
    action: str          # "velocity" | "absolute"
    bearing: float
    distance: float
    pan_target_enc: float
    pan_cur_enc: float
    pan_err: float
    zoom_target_enc: float = 0.0
    zoom_cur_enc: float = 0.0


class PointingController:
    def __init__(self, pose: CameraPose, camera,
                 prediction_horizon: float = 0.5, latency_comp_s: float = 0.15,
                 pan_deadband_enc: float = 25.0, pan_kp: float = 0.0012,
                 max_vel: float = 0.7, snap_enc: float = 2500.0,
                 track_tilt: bool = False, tilt_kp: float = 0.0010,
                 tilt_deadband_enc: float = 25.0, tilt_vel_sign: float = 1.0,
                 feedforward: bool = True, enc_per_vel: float = 480.0,
                 zoom_enabled: bool = False, zoom_max_enc: float = 16384.0,
                 zoom_near_m: float = 40.0, zoom_far_m: float = 250.0,
                 zoom_max_frac: float = 0.85, zoom_kp: float = 0.0008,
                 zoom_deadband_enc: float = 200.0, zoom_max_vel: float = 0.6):
        self.pose = pose
        self.cam = camera
        self.lead_s = prediction_horizon + latency_comp_s
        self.pan_deadband = pan_deadband_enc
        self.pan_kp = pan_kp
        self.max_vel = max_vel
        self.snap_enc = snap_enc
        self.track_tilt = track_tilt
        self.tilt_kp = tilt_kp
        self.tilt_deadband = tilt_deadband_enc
        self.tilt_vel_sign = tilt_vel_sign
        # Velocity feed-forward: match the target's angular rate so proportional
        # control only corrects residual error (kills velocity-limited lag).
        self.feedforward = feedforward
        self.enc_per_vel = enc_per_vel  # encoder counts/sec at velocity 1.0 (camera-specific, rough)
        self._last_bearing: Optional[float] = None
        self._last_t: Optional[float] = None
        # Distance -> zoom.
        self.zoom_enabled = zoom_enabled
        self.zoom_max_enc = zoom_max_enc
        self.zoom_near_m = zoom_near_m
        self.zoom_far_m = zoom_far_m
        self.zoom_max_frac = zoom_max_frac
        self.zoom_kp = zoom_kp
        self.zoom_deadband = zoom_deadband_enc
        self.zoom_max_vel = zoom_max_vel

    def desired_encoders(self, base: GeoPoint, target: GeoPoint):
        lead = predict_position(target, self.lead_s)
        bearing = calculate_bearing(base, lead)
        dist = haversine_distance(base, lead)
        pan_enc = self.pose.bearing_to_pan_encoder(bearing)
        alt_diff = (target.alt or 0.0) - (base.alt or 0.0)
        elev = math.degrees(math.atan2(alt_diff, dist)) if dist > 1 else 0.0
        tilt_enc = self.pose.elevation_to_tilt_encoder(elev)
        return pan_enc, tilt_enc, bearing, dist

    def desired_zoom_encoder(self, distance_m: float) -> float:
        span = max(self.zoom_far_m - self.zoom_near_m, 1.0)
        frac = _clamp((distance_m - self.zoom_near_m) / span, 0.0, 1.0)
        return frac * self.zoom_max_frac * self.zoom_max_enc

    def point_at(self, base: GeoPoint, target: GeoPoint) -> Optional[PointingStatus]:
        pan_t, tilt_t, bearing, dist = self.desired_encoders(base, target)
        cur = self.cam.get_position()
        if cur is None:
            return None
        pan_err = pan_t - cur.pan

        if abs(pan_err) > self.snap_enc:
            tilt_cmd = tilt_t if self.track_tilt else cur.tilt
            self.cam.move_absolute(pan_t, tilt_cmd, pan_speed=0.6, tilt_speed=0.5)
            action = "absolute"
        else:
            ff = 0.0
            now = time.time()
            if self.feedforward and self._last_bearing is not None and self.pose.pan_enc_per_deg:
                dt = now - self._last_t
                if dt > 1e-3:
                    brate = ang_diff(bearing, self._last_bearing) / dt           # deg/s
                    ff = (brate * self.pose.pan_enc_per_deg) / self.enc_per_vel  # -> velocity
            self._last_bearing, self._last_t = bearing, now
            prop = 0.0 if abs(pan_err) < self.pan_deadband else self.pan_kp * pan_err
            pv = _clamp(ff + prop, -self.max_vel, self.max_vel)
            tv = 0.0
            if self.track_tilt:
                tilt_err = tilt_t - cur.tilt
                if abs(tilt_err) >= self.tilt_deadband:
                    tv = self.tilt_vel_sign * _clamp(self.tilt_kp * tilt_err, -self.max_vel, self.max_vel)
            self.cam.pan_tilt_velocity(pv, tv)
            action = "velocity"

        zoom_t = cur.zoom
        if self.zoom_enabled:
            zoom_t = self.desired_zoom_encoder(dist)
            zerr = zoom_t - cur.zoom
            zv = 0.0 if abs(zerr) < self.zoom_deadband else _clamp(self.zoom_kp * zerr,
                                                                   -self.zoom_max_vel, self.zoom_max_vel)
            self.cam.zoom_velocity(zv)

        return PointingStatus(action, bearing, dist, pan_t, cur.pan, pan_err, zoom_t, cur.zoom)

    def stop(self) -> None:
        self.cam.stop()
