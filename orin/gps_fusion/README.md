# GPS-Vision Fusion Module

## Overview
Integrates GPS data from Apple Watch (subject) and iPhone (gimbal base) with
YOLOv8 visual tracking to enable robust tracking at distance and speed.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          FUSION ENGINE                                   │
│                                                                          │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐               │
│  │ GPSClient   │────▶│ GeoCalc     │────▶│ FusionCore  │               │
│  │ (WebSocket) │     │ (bearing,   │     │ (Kalman,    │               │
│  │             │     │  distance)  │     │  prediction)│               │
│  └─────────────┘     └─────────────┘     └──────┬──────┘               │
│        ↑                                        │                       │
│   Watch GPS                                     ↓                       │
│   Phone GPS                              ┌─────────────┐               │
│                                          │ GimbalCalc  │               │
│  ┌─────────────┐                         │ (pan/tilt   │               │
│  │ VisionTrack │────────────────────────▶│  angles)    │               │
│  │ (YOLOv8)    │                         └──────┬──────┘               │
│  └─────────────┘                                │                       │
│        ↑                                        ↓                       │
│   Camera Frame                          ┌─────────────┐               │
│                                         │ Gimbal      │               │
│                                         │ Controller  │               │
│                                         └─────────────┘               │
└─────────────────────────────────────────────────────────────────────────┘
```

## Data Flow

1. **GPS Stream** (2Hz from iPhone app)
   - Watch fix: Subject position, speed, heading
   - Phone fix: Gimbal base position, heading (compass orientation)

2. **Vision Stream** (8Hz from YOLOv8)
   - Detection bounding boxes
   - Target center offset from frame center

3. **Fusion Output**
   - Primary: Visual tracking (when target visible)
   - Secondary: GPS-derived pointing (when target lost or at distance)
   - Prediction: Extrapolate position using velocity

## Key Calculations

### Bearing & Distance (Haversine)
```
Given: gimbal(lat1, lon1), subject(lat2, lon2)
Output: bearing (degrees), distance (meters)
```

### GPS → Gimbal Angles
```
pan_angle = bearing - gimbal_heading  (normalize to ±180°)
tilt_angle = atan2(altitude_diff, distance)  (with horizon offset)
```

### Expected Target Size
```
person_height ≈ 1.7m
pixel_height = (focal_length * person_height) / distance
```

## Tracking Modes

| Mode | Trigger | Behavior |
|------|---------|----------|
| **Visual** | Target in frame | Pure vision tracking |
| **GPS-Assisted** | Target at edge/small | GPS provides search hint |
| **GPS-Primary** | Target lost | Point at GPS bearing, search pattern |
| **Predictive** | Target moving fast | Lead target using velocity |

## Files

- `gps_client.py` - WebSocket client for GPS fixes
- `geo_calc.py` - Bearing, distance, angle calculations
- `fusion_engine.py` - Sensor fusion with Kalman filter
- `tracker_integration.py` - Connects fusion to vision_tracker.py
