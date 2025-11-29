"""GPS-Vision Fusion Module for Auto-Tracking Gimbal."""
from .geo_calc import GeoPoint, RelativePosition, calculate_relative_position, gps_to_gimbal_angles
from .gps_client import GPSClient, GPSState
from .fusion_engine import FusionEngine, FusionOutput, TrackingMode, VisualTarget
from .tracker_integration import IntegratedTracker, IntegratedTrackerConfig

__all__ = [
    'GeoPoint', 'RelativePosition', 'calculate_relative_position', 'gps_to_gimbal_angles',
    'GPSClient', 'GPSState',
    'FusionEngine', 'FusionOutput', 'TrackingMode', 'VisualTarget',
    'IntegratedTracker', 'IntegratedTrackerConfig'
]
