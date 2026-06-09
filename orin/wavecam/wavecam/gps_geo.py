"""Pure geographic math for GPS-based camera pointing.

Ported + trimmed from the field-validated legacy ``orin/gps_fusion/geo_calc.py``
(the archived stepper-gimbal step math is dropped — WaveCam uses a PTZ camera). No
I/O, no device state: lat/lon in, metres/degrees out. This is the single home for
the haversine/bearing helpers (``gps_meshtastic`` re-uses them).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

EARTH_RADIUS_M = 6_371_000.0


@dataclass
class GeoPoint:
    """A geographic point. Optional fields support lead prediction + tilt geometry."""
    lat: float
    lon: float
    alt_m: float = 0.0
    speed_mps: Optional[float] = None   # ground speed
    course_deg: Optional[float] = None  # direction of travel, 0=N


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial true bearing from point 1 to point 2, degrees in [0, 360)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def normalize_180(angle_deg: float) -> float:
    """Wrap an angle to (-180, 180]."""
    d = (angle_deg + 180.0) % 360.0 - 180.0
    return d + 360.0 if d <= -180.0 else d


def elevation_deg(base: GeoPoint, target: GeoPoint, distance_m: float) -> float:
    """Tilt elevation (deg, +up) from the camera base to the target, accounting for
    the altitude difference over the ground distance. ~0 for two points near sea
    level at 50-300 m (the surf-filming case). Returns 0 if distance is ~0."""
    if distance_m <= 1e-6:
        return 0.0
    return math.degrees(math.atan2(target.alt_m - base.alt_m, distance_m))


def predict_lead(point: GeoPoint, dt_s: float) -> GeoPoint:
    """Project a moving point forward dt seconds along its course at its speed.
    Returns the point unchanged when speed/course are missing or speed is < 0.1 m/s
    (avoids amplifying GPS jitter into a phantom heading)."""
    if point.speed_mps is None or point.course_deg is None or point.speed_mps < 0.1 or dt_s <= 0:
        return point
    dist = point.speed_mps * dt_s
    ang = dist / EARTH_RADIUS_M
    lat1, lon1, brg = math.radians(point.lat), math.radians(point.lon), math.radians(point.course_deg)
    lat2 = math.asin(math.sin(lat1) * math.cos(ang) + math.cos(lat1) * math.sin(ang) * math.cos(brg))
    lon2 = lon1 + math.atan2(math.sin(brg) * math.sin(ang) * math.cos(lat1),
                             math.cos(ang) - math.sin(lat1) * math.sin(lat2))
    return GeoPoint(lat=math.degrees(lat2), lon=math.degrees(lon2), alt_m=point.alt_m,
                    speed_mps=point.speed_mps, course_deg=point.course_deg)
