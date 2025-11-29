"""Sensor fusion engine combining GPS and visual tracking."""

import time
import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Tuple, List
from collections import deque

from .geo_calc import (
    GeoPoint, RelativePosition, 
    calculate_relative_position, gps_to_gimbal_angles,
    predict_position, estimate_target_size_pixels, normalize_angle
)


class TrackingMode(Enum):
    """Current tracking mode."""
    VISUAL = auto()        # Target visible, using vision
    GPS_ASSISTED = auto()  # Vision primary, GPS hints
    GPS_PRIMARY = auto()   # Target lost, using GPS
    SEARCHING = auto()     # Lost both, searching
    IDLE = auto()          # No tracking


@dataclass
class VisualTarget:
    """Visual detection result."""
    cx: float  # Center X in frame (0-1 normalized)
    cy: float  # Center Y in frame (0-1 normalized)
    width: float  # Bounding box width (0-1)
    height: float  # Bounding box height (0-1)
    confidence: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class FusionOutput:
    """Output from fusion engine."""
    mode: TrackingMode
    pan_offset: float  # Normalized pan offset (-1 to 1)
    tilt_offset: float  # Normalized tilt offset (-1 to 1)
    confidence: float  # Combined confidence (0-1)
    gps_distance: Optional[float] = None  # Distance to target (m)
    gps_bearing: Optional[float] = None  # Bearing to target (deg)
    predicted_pan: Optional[float] = None  # Predicted pan angle
    predicted_tilt: Optional[float] = None  # Predicted tilt angle
    target_expected_size: Optional[float] = None  # Expected target height (px)


class SimpleKalman:
    """Simple 1D Kalman filter for smoothing."""
    
    def __init__(self, process_noise: float = 0.1, measurement_noise: float = 0.5):
        self.q = process_noise
        self.r = measurement_noise
        self.x = 0.0  # State estimate
        self.p = 1.0  # Estimate uncertainty
        self.initialized = False
    
    def update(self, measurement: float) -> float:
        if not self.initialized:
            self.x = measurement
            self.initialized = True
            return self.x
        
        # Prediction
        self.p += self.q
        
        # Update
        k = self.p / (self.p + self.r)
        self.x += k * (measurement - self.x)
        self.p *= (1 - k)
        
        return self.x
    
    def reset(self):
        self.x = 0.0
        self.p = 1.0
        self.initialized = False


class FusionEngine:
    """Combines GPS and visual tracking data."""
    
    def __init__(self,
                 frame_width: int = 640,
                 frame_height: int = 480,
                 camera_hfov: float = 60.0,  # Horizontal FOV in degrees
                 camera_vfov: float = 45.0,  # Vertical FOV in degrees
                 visual_timeout: float = 1.0,  # Seconds before visual considered stale
                 gps_timeout: float = 5.0,  # Seconds before GPS considered stale
                 prediction_horizon: float = 0.5):  # Seconds to predict ahead
        
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.camera_hfov = camera_hfov
        self.camera_vfov = camera_vfov
        self.visual_timeout = visual_timeout
        self.gps_timeout = gps_timeout
        self.prediction_horizon = prediction_horizon
        
        # State
        self.mode = TrackingMode.IDLE
        self.last_visual: Optional[VisualTarget] = None
        self.last_gimbal_gps: Optional[GeoPoint] = None
        self.last_target_gps: Optional[GeoPoint] = None
        
        # Kalman filters for smoothing
        self.pan_filter = SimpleKalman(process_noise=0.05, measurement_noise=0.2)
        self.tilt_filter = SimpleKalman(process_noise=0.05, measurement_noise=0.2)
        
        # History for velocity estimation
        self.visual_history: deque = deque(maxlen=10)
        
        # Timing
        self.last_update = time.time()
        self.target_lost_time: Optional[float] = None
    
    def update_visual(self, target: Optional[VisualTarget]) -> None:
        """Update with new visual detection."""
        if target is not None:
            self.last_visual = target
            self.visual_history.append(target)
            self.target_lost_time = None
        elif self.last_visual is not None:
            # Target just lost
            if self.target_lost_time is None:
                self.target_lost_time = time.time()
    
    def update_gps(self, gimbal: Optional[GeoPoint], target: Optional[GeoPoint]) -> None:
        """Update with new GPS fixes."""
        if gimbal is not None:
            self.last_gimbal_gps = gimbal
        if target is not None:
            self.last_target_gps = target
    
    def _is_visual_fresh(self) -> bool:
        if self.last_visual is None:
            return False
        return (time.time() - self.last_visual.timestamp) < self.visual_timeout
    
    def _is_gps_fresh(self) -> bool:
        if self.last_gimbal_gps is None or self.last_target_gps is None:
            return False
        if self.last_gimbal_gps.timestamp is None or self.last_target_gps.timestamp is None:
            return False
        now = time.time()
        gimbal_age = now - self.last_gimbal_gps.timestamp
        target_age = now - self.last_target_gps.timestamp
        return gimbal_age < self.gps_timeout and target_age < self.gps_timeout
    
    def _visual_to_offset(self, target: VisualTarget) -> Tuple[float, float]:
        """Convert visual target to normalized offset from center."""
        # Target center is 0-1, convert to -1 to 1 (center = 0)
        pan_offset = (target.cx - 0.5) * 2
        tilt_offset = (target.cy - 0.5) * 2
        return pan_offset, tilt_offset
    
    def _gps_to_offset(self) -> Tuple[float, float, RelativePosition]:
        """Convert GPS to normalized offset using gimbal angles."""
        rel_pos = calculate_relative_position(self.last_gimbal_gps, self.last_target_gps)
        pan_deg, tilt_deg = gps_to_gimbal_angles(rel_pos)
        
        # Normalize to -1 to 1 based on camera FOV
        pan_offset = (pan_deg / (self.camera_hfov / 2))
        tilt_offset = (tilt_deg / (self.camera_vfov / 2))
        
        # Clamp to valid range
        pan_offset = max(-1, min(1, pan_offset))
        tilt_offset = max(-1, min(1, tilt_offset))
        
        return pan_offset, tilt_offset, rel_pos
    
    def _estimate_visual_velocity(self) -> Tuple[float, float]:
        """Estimate visual target velocity from history."""
        if len(self.visual_history) < 2:
            return 0.0, 0.0
        
        recent = list(self.visual_history)[-5:]
        if len(recent) < 2:
            return 0.0, 0.0
        
        dt = recent[-1].timestamp - recent[0].timestamp
        if dt < 0.05:
            return 0.0, 0.0
        
        dx = recent[-1].cx - recent[0].cx
        dy = recent[-1].cy - recent[0].cy
        
        return dx / dt, dy / dt
    
    def compute(self) -> FusionOutput:
        """Compute fusion output."""
        now = time.time()
        
        visual_fresh = self._is_visual_fresh()
        gps_fresh = self._is_gps_fresh()
        
        pan_offset = 0.0
        tilt_offset = 0.0
        confidence = 0.0
        gps_distance = None
        gps_bearing = None
        predicted_pan = None
        predicted_tilt = None
        target_size = None
        
        # Determine mode and compute offsets
        if visual_fresh:
            # Visual tracking available
            pan_offset, tilt_offset = self._visual_to_offset(self.last_visual)
            confidence = self.last_visual.confidence
            
            if gps_fresh:
                # GPS can assist
                self.mode = TrackingMode.GPS_ASSISTED
                _, _, rel_pos = self._gps_to_offset()
                gps_distance = rel_pos.distance
                gps_bearing = rel_pos.bearing
                target_size = estimate_target_size_pixels(rel_pos.distance)
                
                # Use GPS to predict where target is going
                if self.last_target_gps.speed and self.last_target_gps.speed > 0.5:
                    predicted_target = predict_position(self.last_target_gps, self.prediction_horizon)
                    pred_rel = calculate_relative_position(self.last_gimbal_gps, predicted_target)
                    pred_pan, pred_tilt = gps_to_gimbal_angles(pred_rel)
                    predicted_pan = pred_pan / (self.camera_hfov / 2)
                    predicted_tilt = pred_tilt / (self.camera_vfov / 2)
            else:
                self.mode = TrackingMode.VISUAL
        
        elif gps_fresh:
            # GPS only - target lost visually
            self.mode = TrackingMode.GPS_PRIMARY
            pan_offset, tilt_offset, rel_pos = self._gps_to_offset()
            
            gps_distance = rel_pos.distance
            gps_bearing = rel_pos.bearing
            target_size = estimate_target_size_pixels(rel_pos.distance)
            
            # Lower confidence for GPS-only
            confidence = 0.5
            
            # Predict ahead
            predicted_target = predict_position(self.last_target_gps, self.prediction_horizon)
            pred_rel = calculate_relative_position(self.last_gimbal_gps, predicted_target)
            pred_pan, pred_tilt = gps_to_gimbal_angles(pred_rel)
            predicted_pan = pred_pan / (self.camera_hfov / 2)
            predicted_tilt = pred_tilt / (self.camera_vfov / 2)
        
        else:
            # Nothing fresh - searching or idle
            self.mode = TrackingMode.SEARCHING if self.target_lost_time else TrackingMode.IDLE
            confidence = 0.0
        
        # Apply Kalman smoothing
        if confidence > 0:
            pan_offset = self.pan_filter.update(pan_offset)
            tilt_offset = self.tilt_filter.update(tilt_offset)
        else:
            self.pan_filter.reset()
            self.tilt_filter.reset()
        
        self.last_update = now
        
        return FusionOutput(
            mode=self.mode,
            pan_offset=pan_offset,
            tilt_offset=tilt_offset,
            confidence=confidence,
            gps_distance=gps_distance,
            gps_bearing=gps_bearing,
            predicted_pan=predicted_pan,
            predicted_tilt=predicted_tilt,
            target_expected_size=target_size
        )


if __name__ == '__main__':
    # Test fusion engine
    engine = FusionEngine()
    
    # Simulate GPS data
    gimbal = GeoPoint(lat=21.3069, lon=-157.8583, alt=10, heading=45, timestamp=time.time())
    target = GeoPoint(lat=21.3079, lon=-157.8573, alt=12, speed=2.0, course=90, timestamp=time.time())
    
    engine.update_gps(gimbal, target)
    
    # Simulate visual detection
    visual = VisualTarget(cx=0.6, cy=0.45, width=0.1, height=0.3, confidence=0.85)
    engine.update_visual(visual)
    
    output = engine.compute()
    print(f'Mode: {output.mode.name}')
    print(f'Pan offset: {output.pan_offset:.3f}')
    print(f'Tilt offset: {output.tilt_offset:.3f}')
    print(f'Confidence: {output.confidence:.2f}')
    print(f'GPS Distance: {output.gps_distance:.1f}m')
    print(f'GPS Bearing: {output.gps_bearing:.1f}°')
    print(f'Expected target size: {output.target_expected_size:.0f}px')
    
    # Simulate target lost
    print('\n--- Target lost ---')
    engine.update_visual(None)
    time.sleep(1.5)  # Wait for visual to go stale
    
    output = engine.compute()
    print(f'Mode: {output.mode.name}')
    print(f'Pan offset: {output.pan_offset:.3f}')
    print(f'Confidence: {output.confidence:.2f}')
