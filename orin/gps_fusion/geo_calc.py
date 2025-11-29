"""Geographic calculations for GPS-based gimbal pointing."""

import math
from dataclasses import dataclass
from typing import Optional, Tuple

# Earth radius in meters
EARTH_RADIUS_M = 6_371_000


@dataclass
class GeoPoint:
    """Geographic point with optional altitude and heading."""
    lat: float  # degrees
    lon: float  # degrees
    alt: Optional[float] = None  # meters above sea level
    heading: Optional[float] = None  # compass heading in degrees (0=N, 90=E)
    speed: Optional[float] = None  # m/s
    course: Optional[float] = None  # direction of travel in degrees
    timestamp: Optional[float] = None  # unix timestamp
    accuracy: Optional[float] = None  # horizontal accuracy in meters


@dataclass
class RelativePosition:
    """Position of target relative to gimbal."""
    bearing: float  # degrees from north (0-360)
    distance: float  # meters
    altitude_diff: float  # meters (positive = target above gimbal)
    relative_bearing: float  # degrees relative to gimbal heading (-180 to 180)


def haversine_distance(p1: GeoPoint, p2: GeoPoint) -> float:
    """Calculate great-circle distance between two points in meters."""
    lat1, lon1 = math.radians(p1.lat), math.radians(p1.lon)
    lat2, lon2 = math.radians(p2.lat), math.radians(p2.lon)
    
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return EARTH_RADIUS_M * c


def calculate_bearing(p1: GeoPoint, p2: GeoPoint) -> float:
    """Calculate initial bearing from p1 to p2 in degrees (0-360)."""
    lat1, lon1 = math.radians(p1.lat), math.radians(p1.lon)
    lat2, lon2 = math.radians(p2.lat), math.radians(p2.lon)
    
    dlon = lon2 - lon1
    
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    
    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360) % 360


def normalize_angle(angle: float) -> float:
    """Normalize angle to -180 to 180 degrees."""
    while angle > 180:
        angle -= 360
    while angle < -180:
        angle += 360
    return angle


def calculate_relative_position(gimbal: GeoPoint, target: GeoPoint) -> RelativePosition:
    """Calculate target position relative to gimbal."""
    bearing = calculate_bearing(gimbal, target)
    distance = haversine_distance(gimbal, target)
    
    # Altitude difference (positive = target above)
    alt_diff = 0.0
    if gimbal.alt is not None and target.alt is not None:
        alt_diff = target.alt - gimbal.alt
    
    # Relative bearing (accounting for gimbal heading)
    rel_bearing = bearing
    if gimbal.heading is not None:
        rel_bearing = normalize_angle(bearing - gimbal.heading)
    
    return RelativePosition(
        bearing=bearing,
        distance=distance,
        altitude_diff=alt_diff,
        relative_bearing=rel_bearing
    )


def gps_to_gimbal_angles(rel_pos: RelativePosition, 
                          gimbal_height: float = 1.0,
                          target_height: float = 1.7) -> Tuple[float, float]:
    """Convert relative position to pan/tilt angles.
    
    Args:
        rel_pos: Relative position from calculate_relative_position()
        gimbal_height: Height of gimbal above ground (meters)
        target_height: Estimated height of target's center (meters)
    
    Returns:
        (pan_degrees, tilt_degrees) - Pan is relative to gimbal heading,
        Tilt is from horizontal (positive = up)
    """
    pan = rel_pos.relative_bearing
    
    # Calculate tilt angle
    # Account for gimbal height and target height
    effective_alt_diff = rel_pos.altitude_diff + (target_height - gimbal_height)
    
    if rel_pos.distance > 0:
        tilt = math.degrees(math.atan2(effective_alt_diff, rel_pos.distance))
    else:
        tilt = 0.0
    
    return pan, tilt


def predict_position(point: GeoPoint, dt_seconds: float) -> GeoPoint:
    """Predict future position based on current speed and course.
    
    Args:
        point: Current position with speed and course
        dt_seconds: Time to predict ahead
    
    Returns:
        Predicted GeoPoint
    """
    if point.speed is None or point.course is None or point.speed < 0.1:
        return point
    
    # Distance traveled
    distance = point.speed * dt_seconds
    
    # Calculate new position
    lat1 = math.radians(point.lat)
    lon1 = math.radians(point.lon)
    bearing = math.radians(point.course)
    
    lat2 = math.asin(
        math.sin(lat1) * math.cos(distance / EARTH_RADIUS_M) +
        math.cos(lat1) * math.sin(distance / EARTH_RADIUS_M) * math.cos(bearing)
    )
    
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(distance / EARTH_RADIUS_M) * math.cos(lat1),
        math.cos(distance / EARTH_RADIUS_M) - math.sin(lat1) * math.sin(lat2)
    )
    
    return GeoPoint(
        lat=math.degrees(lat2),
        lon=math.degrees(lon2),
        alt=point.alt,
        heading=point.heading,
        speed=point.speed,
        course=point.course,
        timestamp=point.timestamp + dt_seconds if point.timestamp else None,
        accuracy=point.accuracy
    )


def estimate_target_size_pixels(distance_m: float,
                                 target_height_m: float = 1.7,
                                 focal_length_px: float = 500,
                                 sensor_height_px: float = 480) -> float:
    """Estimate expected target height in pixels based on distance.
    
    Args:
        distance_m: Distance to target in meters
        target_height_m: Real-world height of target
        focal_length_px: Camera focal length in pixels (approx)
        sensor_height_px: Frame height in pixels
    
    Returns:
        Expected height in pixels
    """
    if distance_m < 1:
        return sensor_height_px
    
    return (focal_length_px * target_height_m) / distance_m


if __name__ == '__main__':
    # Test calculations
    gimbal = GeoPoint(lat=21.3069, lon=-157.8583, alt=10, heading=45)  # Honolulu
    target = GeoPoint(lat=21.3079, lon=-157.8573, alt=12, speed=2.0, course=90)
    
    rel = calculate_relative_position(gimbal, target)
    print(f'Distance: {rel.distance:.1f}m')
    print(f'Bearing: {rel.bearing:.1f}°')
    print(f'Relative bearing: {rel.relative_bearing:.1f}°')
    
    pan, tilt = gps_to_gimbal_angles(rel)
    print(f'Pan: {pan:.1f}°, Tilt: {tilt:.1f}°')
    
    # Predict 2 seconds ahead
    predicted = predict_position(target, 2.0)
    print(f'Predicted position: ({predicted.lat:.6f}, {predicted.lon:.6f})')
    
    # Expected size at 50m
    size = estimate_target_size_pixels(50)
    print(f'Expected target height at 50m: {size:.0f}px')
