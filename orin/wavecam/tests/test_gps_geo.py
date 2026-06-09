"""Unit tests for gps_geo (pure geographic math). No hardware."""
import math

from wavecam.gps_geo import (
    GeoPoint,
    bearing_deg,
    elevation_deg,
    haversine_m,
    normalize_180,
    predict_lead,
)


def test_haversine_one_degree_latitude():
    assert abs(haversine_m(0.0, 0.0, 1.0, 0.0) - 111_195) < 500


def test_haversine_symmetric_and_zero():
    assert haversine_m(21.6, -158.0, 21.6, -158.0) == 0.0
    d1 = haversine_m(21.60, -158.00, 21.61, -158.02)
    d2 = haversine_m(21.61, -158.02, 21.60, -158.00)
    assert abs(d1 - d2) < 1e-6


def test_bearing_cardinals():
    assert abs(bearing_deg(0.0, 0.0, 1.0, 0.0) - 0.0) < 0.5     # north
    assert abs(bearing_deg(0.0, 0.0, 0.0, 1.0) - 90.0) < 0.5    # east
    assert abs(bearing_deg(0.0, 0.0, -1.0, 0.0) - 180.0) < 0.5  # south
    assert abs(bearing_deg(0.0, 0.0, 0.0, -1.0) - 270.0) < 0.5  # west


def test_matches_legacy_geo_calc_reference():
    # Legacy geo_calc.__main__ reference: Honolulu base -> target.
    base = (21.3069, -157.8583)
    tgt = (21.3079, -157.8573)
    # Legacy haversine_distance / calculate_bearing on these points.
    assert abs(haversine_m(*base, *tgt) - 148.0) < 5.0          # ~148 m
    assert abs(bearing_deg(*base, *tgt) - 43.0) < 2.0           # ~NE


def test_normalize_180():
    assert normalize_180(10.0) == 10.0
    assert normalize_180(190.0) == -170.0
    assert normalize_180(-190.0) == 170.0
    assert normalize_180(360.0) == 0.0


def test_elevation_zero_for_equal_altitude_and_positive_when_higher():
    base = GeoPoint(lat=21.6, lon=-158.0, alt_m=2.0)
    level = GeoPoint(lat=21.6, lon=-158.0, alt_m=2.0)
    higher = GeoPoint(lat=21.6, lon=-158.0, alt_m=12.0)
    assert elevation_deg(base, level, 200.0) == 0.0
    assert elevation_deg(base, higher, 200.0) > 0.0            # target above -> tilt up
    assert elevation_deg(base, higher, 1e-9) == 0.0           # guard near-zero distance


def test_predict_lead_stationary_unchanged():
    p = GeoPoint(lat=21.6, lon=-158.0)                          # no speed/course
    assert predict_lead(p, 2.0) is p
    jitter = GeoPoint(lat=21.6, lon=-158.0, speed_mps=0.05, course_deg=90.0)
    assert predict_lead(jitter, 2.0) is jitter                 # below 0.1 m/s -> hold


def test_predict_lead_projects_along_course():
    p = GeoPoint(lat=21.6, lon=-158.0, alt_m=1.0, speed_mps=10.0, course_deg=0.0)  # north
    out = predict_lead(p, 2.0)                                  # ~20 m north
    moved = haversine_m(p.lat, p.lon, out.lat, out.lon)
    assert abs(moved - 20.0) < 1.0
    assert out.lat > p.lat                                      # north -> latitude up
    assert abs(out.lon - p.lon) < 1e-4                          # ~no east/west
