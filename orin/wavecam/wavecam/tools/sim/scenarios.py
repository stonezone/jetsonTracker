"""Synthetic scenario generators for the estimator sim harness.

Each generator returns (fixes, detections) where:
  fixes: list of NormalizedFix-like objects with (.lat, .lon, .speed, .course, .age_sec, .t)
  detections: list of VisionDetection-like objects with (.t, .pan_enc, .pixel_cx, .frame_w, .zoom_enc)
              — empty in most scenarios (vision is the harder path to synthesise).

Ground truth: (lat, lon) at each timestamp — fixes carry the truth since they're synthetic.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# Simulation base position (lat, lon) — matches the test pose in estimator tests
_BASE_LAT = 21.601
_BASE_LON = -158.001
EARTH_R = 6_371_000.0


@dataclass
class SimFix:
    lat: float
    lon: float
    speed: float
    course_deg: float
    age_sec: float
    t: float


@dataclass
class SimDetection:
    t: float
    pan_enc: int
    pixel_cx: float
    frame_w: float = 640.0
    zoom_enc: int = 0


def _project(lat: float, lon: float, bearing_deg: float, dist_m: float) -> Tuple[float, float]:
    """Project a point forward by dist_m along bearing_deg."""
    brg = math.radians(bearing_deg)
    d = dist_m / EARTH_R
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    lat2 = math.asin(math.sin(lat1)*math.cos(d) + math.cos(lat1)*math.sin(d)*math.cos(brg))
    lon2 = lon1 + math.atan2(math.sin(brg)*math.sin(d)*math.cos(lat1),
                             math.cos(d) - math.sin(lat1)*math.sin(lat2))
    return math.degrees(lat2), math.degrees(lon2)


def straight_run(
    speed_mps: float = 8.0,
    course_deg: float = 270.0,   # due west = typical surf direction
    start_dist_m: float = 100.0, # subject starts 100m from base
    start_bearing_deg: float = 270.0,
    duration_sec: float = 30.0,
    dt_gps: float = 2.0,
    gps_age_sec: float = 2.0,
) -> Tuple[List[SimFix], List[SimDetection]]:
    """Constant-speed straight run. No GPS dropout, no vision."""
    start_lat, start_lon = _project(_BASE_LAT, _BASE_LON, start_bearing_deg, start_dist_m)
    fixes = []
    t = 0.0
    lat, lon = start_lat, start_lon
    while t <= duration_sec:
        fixes.append(SimFix(lat=lat, lon=lon, speed=speed_mps, course_deg=course_deg,
                            age_sec=gps_age_sec, t=t))
        dist = speed_mps * dt_gps
        lat, lon = _project(lat, lon, course_deg, dist)
        t += dt_gps
    return fixes, []


def bottom_turn(
    speed_mps: float = 6.0,
    accel_mps2: float = 3.0,
    turn_duration_sec: float = 3.0,
    start_course_deg: float = 270.0,
    end_course_deg: float = 310.0,
    start_dist_m: float = 120.0,
    dt_gps: float = 2.0,
) -> Tuple[List[SimFix], List[SimDetection]]:
    """Lateral acceleration event (bottom turn). Course changes linearly over turn_duration."""
    duration_sec = turn_duration_sec + 10.0
    start_lat, start_lon = _project(_BASE_LAT, _BASE_LON, start_course_deg, start_dist_m)
    fixes = []
    t = 0.0
    lat, lon = start_lat, start_lon
    while t <= duration_sec:
        frac = min(1.0, t / max(0.01, turn_duration_sec))
        course = start_course_deg + frac * (end_course_deg - start_course_deg)
        fixes.append(SimFix(lat=lat, lon=lon, speed=speed_mps, course_deg=course,
                            age_sec=2.0, t=t))
        dist = speed_mps * dt_gps
        lat, lon = _project(lat, lon, course, dist)
        t += dt_gps
    return fixes, []


def gps_dropout(
    speed_mps: float = 7.0,
    course_deg: float = 270.0,
    start_dist_m: float = 150.0,
    dropout_start_sec: float = 5.0,
    dropout_dur_sec: float = 10.0,
    duration_sec: float = 30.0,
    dt_gps: float = 2.0,
) -> Tuple[List[SimFix], List[SimDetection]]:
    """GPS blackout for dropout_dur_sec seconds mid-run."""
    start_lat, start_lon = _project(_BASE_LAT, _BASE_LON, course_deg, start_dist_m)
    fixes = []
    t = 0.0
    lat, lon = start_lat, start_lon
    while t <= duration_sec:
        in_dropout = dropout_start_sec <= t <= dropout_start_sec + dropout_dur_sec
        if not in_dropout:
            fixes.append(SimFix(lat=lat, lon=lon, speed=speed_mps, course_deg=course_deg,
                                age_sec=2.0, t=t))
        dist = speed_mps * dt_gps
        lat, lon = _project(lat, lon, course_deg, dist)
        t += dt_gps
    return fixes, []


def vision_dropout(
    speed_mps: float = 6.0,
    course_deg: float = 270.0,
    start_dist_m: float = 100.0,
    duration_sec: float = 20.0,
    dt_gps: float = 2.0,
) -> Tuple[List[SimFix], List[SimDetection]]:
    """GPS only — no vision detections (tests GPS-only path)."""
    return straight_run(speed_mps=speed_mps, course_deg=course_deg,
                        start_dist_m=start_dist_m, duration_sec=duration_sec,
                        dt_gps=dt_gps)


def combined_dropout(
    speed_mps: float = 7.0,
    course_deg: float = 270.0,
    start_dist_m: float = 130.0,
    dropout_start_sec: float = 5.0,
    dropout_dur_sec: float = 8.0,
    duration_sec: float = 25.0,
    dt_gps: float = 2.0,
) -> Tuple[List[SimFix], List[SimDetection]]:
    """Both GPS and vision gone for dropout_dur_sec. Tests dead-reckoning."""
    return gps_dropout(speed_mps=speed_mps, course_deg=course_deg,
                       start_dist_m=start_dist_m, dropout_start_sec=dropout_start_sec,
                       dropout_dur_sec=dropout_dur_sec, duration_sec=duration_sec,
                       dt_gps=dt_gps)


@dataclass
class SimRangeDetection:
    """A synthetic vision range observation (person bbox height in pixels)."""
    t: float
    bbox_h_px: float
    frame_h: float = 720.0
    zoom_enc: int = 0


def _range_from_geometry(dist_m: float, subject_height_m: float,
                         fov_h_deg: float, frame_h: float) -> float:
    """Compute expected bbox_h_px for a known geometry (inverse range model).

    Useful for synthesising ground-truth bbox heights in sim scenarios.
    vfov = 2 * atan(tan(hfov/2) * 9/16); bbox_frac = 2*atan(h_m/2/d) / vfov
    """
    hfov_rad = math.radians(fov_h_deg)
    vfov_rad = 2.0 * math.atan(math.tan(hfov_rad / 2.0) * 9.0 / 16.0)
    angle_sub = 2.0 * math.atan(subject_height_m / 2.0 / max(1.0, dist_m))
    frac = angle_sub / vfov_rad
    return frac * frame_h


def range_obs_scenario(
    speed_mps: float = 8.0,
    course_deg: float = 270.0,
    start_dist_m: float = 80.0,
    start_bearing_deg: float = 270.0,
    duration_sec: float = 30.0,
    dt_gps: float = 2.0,
    dt_vis: float = 1.0,
    subject_height_m: float = 1.0,
    fov_h_deg: float = 60.0,
    frame_h: float = 720.0,
    zoom_enc: int = 0,
) -> Tuple[List[SimFix], List[SimRangeDetection]]:
    """Straight run with synthetic range observations from person bbox height.

    The bbox heights are computed from ground-truth geometry (no noise added
    here — noise comes from R in the Kalman update). Used to compare range-on
    vs range-off covariance tightening in the sim harness.
    """
    start_lat, start_lon = _project(_BASE_LAT, _BASE_LON, start_bearing_deg, start_dist_m)
    fixes = []
    range_detections = []
    t = 0.0
    lat, lon = start_lat, start_lon
    t_vis = 0.0

    while t <= duration_sec:
        dist = ((lat - _BASE_LAT) ** 2 * (111320.0 ** 2) +
                (lon - _BASE_LON) ** 2 * (111320.0 * math.cos(math.radians(_BASE_LAT))) ** 2) ** 0.5
        fixes.append(SimFix(lat=lat, lon=lon, speed=speed_mps, course_deg=course_deg,
                            age_sec=2.0, t=t))
        if t >= t_vis:
            bbox_h = _range_from_geometry(dist, subject_height_m, fov_h_deg, frame_h)
            range_detections.append(SimRangeDetection(
                t=t, bbox_h_px=bbox_h, frame_h=frame_h, zoom_enc=zoom_enc,
            ))
            t_vis += dt_vis

        move_dist = speed_mps * dt_gps
        lat, lon = _project(lat, lon, course_deg, move_dist)
        t += dt_gps

    return fixes, range_detections
