"""Pure GPS→camera pointing target.

Given the camera base position, the subject target, and a calibrated ``CameraPose``,
compute the desired VISCA pan/tilt/zoom **encoder targets** to frame the subject.

NO camera I/O, NO motion: this is only the *target* computation (ported from the pure
``desired_*`` parts of the field-validated legacy ``gps_fusion/pointing_controller``).
The control loop that acts on this target — velocity vs absolute snap, feed-forward,
deadbands, and the actual VISCA command — lives in the controller/arbiter (P1).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .camera_pose import CameraPose
from .gps_geo import GeoPoint, bearing_deg, elevation_deg, haversine_m, predict_lead


@dataclass
class ZoomCurve:
    """Maps distance → a zoom-encoder target. Nearer = wider, farther = tele, so a
    fast subject keeps margin in frame. Linear between near/far, clamped."""
    near_m: float = 40.0
    far_m: float = 250.0
    max_enc: float = 16384.0
    max_frac: float = 0.85   # never go fully tele — keep search margin


@dataclass
class PointingTarget:
    bearing_deg: float
    distance_m: float
    pan_enc: float
    tilt_enc: float
    zoom_enc: Optional[float] = None   # None when zoom is not driven
    clamped: bool = False              # True when the up-tilt clamp engaged (mis-surveyed base?)


def distance_to_zoom_encoder(distance_m: float, curve: ZoomCurve) -> float:
    """Distance → zoom encoder. near_m → wide (0), far_m → ``max_frac*max_enc``."""
    span = max(curve.far_m - curve.near_m, 1.0)
    frac = min(1.0, max(0.0, (distance_m - curve.near_m) / span))
    return frac * curve.max_frac * curve.max_enc


def compute_target(base: GeoPoint, target: GeoPoint, pose: CameraPose,
                   lead_s: float = 0.65, zoom: Optional[ZoomCurve] = None,
                   max_up_elev_deg: float = 5.0) -> PointingTarget:
    """Desired pan/tilt/zoom encoders to frame the subject.

    Leads the target by ``lead_s`` (GPS poll lag + prediction) along its course, then
    maps the resulting bearing/elevation/distance through the calibrated pose. Requires
    a pan-calibrated pose (raises via ``bearing_to_pan_encoder`` otherwise). ``zoom=None``
    leaves ``zoom_enc`` unset (zoom not driven this call).

    ``max_up_elev_deg`` clamps the commanded elevation: the surf subject is at sea level,
    so any large up-tilt is almost always a bad base altitude or GPS glitch (the "points
    at the sky/ski" failure). The returned ``PointingTarget.clamped`` is True when the
    clamp engaged so callers can log a persistently-clamped aim (= mis-surveyed base).
    """
    lead = predict_lead(target, lead_s)
    bearing = bearing_deg(base.lat, base.lon, lead.lat, lead.lon)
    dist = haversine_m(base.lat, base.lon, lead.lat, lead.lon)
    pan_enc = pose.bearing_to_pan_encoder(bearing)
    elev = elevation_deg(base, lead, dist)
    clamped = elev > max_up_elev_deg
    if clamped:
        elev = max_up_elev_deg
    tilt_enc = pose.elevation_to_tilt_encoder(elev)
    zoom_enc = distance_to_zoom_encoder(dist, zoom) if zoom is not None else None
    return PointingTarget(bearing_deg=bearing, distance_m=dist, pan_enc=pan_enc,
                          tilt_enc=tilt_enc, zoom_enc=zoom_enc, clamped=clamped)
