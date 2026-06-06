# GPS-Vision Fusion Module

## Overview

Reusable GPS pointing and fusion math for the future Wio/LoRa phase. The active
WaveCam runtime is vision-first today; this module is retained because its
bearing, distance, prediction, and pointing pieces are the basis for coarse GPS
cueing when the subject is too far for reliable vision lock.

The archived Watch/iPhone/Cloudflare relay was a GPS transport. It is not the
target transport anymore. Future work should feed this module normalized fixes
from Wio/Meshtastic or another LoRa source.

## Architecture

```text
Wio/LoRa target fix
        |
        v
Normalized GPS fix source -> geo_calc.py -> fusion_engine.py
        |                         |
        |                         v
Camera-base position       pointing_controller.py
        |                         |
        v                         v
Vision tracker ---------> GPS-assisted PTZ cueing
```

## Data Flow

1. **GPS stream** (future Wio/LoRa)
   - Target fix: subject position, speed, course
   - Camera-base fix: Orin/camera position when available, otherwise configured site position

2. **Vision stream**
   - Detection bounding boxes
   - Target center offset from frame center

3. **Fusion output**
   - Primary: visual tracking when target is visible
   - Secondary: GPS-derived pointing when target is lost or distant
   - Prediction: extrapolated subject position using velocity/course

## Key Calculations

### Bearing And Distance

```text
Given: camera_base(lat1, lon1), subject(lat2, lon2)
Output: bearing (degrees), distance (meters)
```

### GPS To PTZ Angles

```text
pan_angle = bearing - ptz_home_heading  (normalize to +/-180 degrees)
tilt_angle = atan2(altitude_diff, distance)  (with horizon offset)
```

### Expected Target Size

```text
person_height ~= 1.7m
pixel_height = (focal_length * person_height) / distance
```

## Tracking Modes

| Mode | Trigger | Behavior |
|---|---|---|
| Visual | Target in frame | Pure vision tracking |
| GPS-assisted | Target at edge/small | GPS provides search hint |
| GPS-primary | Target lost | Point at GPS bearing, search pattern |
| Predictive | Target moving fast | Lead target using velocity |

## Files

- `gps_client.py` - legacy WebSocket GPS client; useful as a reference adapter only
- `geo_calc.py` - bearing, distance, angle calculations
- `fusion_engine.py` - sensor fusion with prediction
- `pointing_controller.py` - provider-agnostic GPS-to-PTZ pointing logic
- `camera_pose.py` - camera/base pose model
- `tracker_integration.py` - earlier integration harness, not the current WaveCam runtime
