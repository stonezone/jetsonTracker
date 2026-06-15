# GPS-Vision Fusion Module

## Overview

Reusable GPS pointing and fusion math. The live WaveCam backend (`orin/wavecam/`)
uses this module's bearing, distance, prediction, and pointing pieces for coarse
GPS cueing via the custom direct-LoRa firmware in `firmware/direct-lora/`.

The archived Watch/iPhone/Cloudflare relay was an earlier GPS transport and is
not the target transport anymore.

## Architecture

```text
direct-LoRa tracker Wio -> base Wio -> DirectRadioGps -> NormalizedFix
                                                          |
                                                          v
                                              geo_calc.py -> fusion_engine.py
                                                          |
                                                          v
                                              pointing_controller.py
                                                          |
                                                          v
Vision tracker --------------------------------> GPS-assisted PTZ cueing
```

## Data Flow

1. **GPS stream** (direct-LoRa Wio)
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
