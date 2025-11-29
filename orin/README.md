# Orin Software

Code running on the Jetson Orin Nano for the robot cameraman.

## Components

| Module | Description |
|--------|-------------|
| `gps_server.py` | WebSocket server receiving GPS from iPhone (port 8765) |
| `vision/vision_tracker.py` | YOLOv8 person detection and tracking |
| `gimbal_control/gimbal_controller.py` | Serial UART interface to Nucleo |
| `gps_fusion/` | GPS-Vision fusion with Kalman filtering |
| `scripts/phone_webcam.sh` | Android camera via scrcpy |

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Start GPS server (for Cloudflare tunnel)
python3 gps_server.py &

# Start vision tracking
python3 vision/vision_tracker.py --camera 10 --gimbal /dev/ttyACM0
```

## GPS Fusion

The fusion engine combines:
- **Visual tracking:** YOLOv8 person detection → frame offset
- **GPS tracking:** Watch location → bearing/distance → pan/tilt angles

See `gps_fusion/README.md` for details.

## Camera Setup

Uses Android phone as USB webcam via scrcpy:

```bash
./scripts/phone_webcam.sh
# Creates /dev/video10
```

## Serial Commands

See `ARCHITECTURE.md` in project root for full command reference.
