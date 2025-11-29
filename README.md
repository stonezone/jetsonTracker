# jetsonTracker - AI-Powered Robot Cameraman

A real-time subject tracking system combining GPS telemetry and computer vision to automatically follow and film subjects with a motorized gimbal.

## Overview

This project implements an autonomous robot cameraman that tracks a subject using:
- **GPS tracking** from an Apple Watch worn by the subject
- **Computer vision** with YOLOv8 person detection on camera feed
- **Sensor fusion** combining both GPS and vision data for robust tracking
- **2-axis motorized gimbal** for smooth pan/tilt camera control

### Key Features

- Sub-200ms latency GPS tracking via Cloudflare Tunnel (bypasses Apple Cloud Relay)
- Real-time YOLOv8 object detection on NVIDIA Jetson Orin Nano
- Custom STM32 firmware for precise stepper motor control
- Kalman filter-based sensor fusion for smooth, accurate tracking
- Reed switch limit switches for safe mechanical operation
- Extensible architecture supporting future features (zoom control, multiple cameras)

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      SUBJECT (Wearing Watch)                    │
│                        Apple Watch GPS                          │
└────────────────────────────┬────────────────────────────────────┘
                             │ Bluetooth/LTE
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                   iPhone (Relay Application)                    │
│              WebSocket → wss://ws.stonezone.net                 │
└────────────────────────────┬────────────────────────────────────┘
                             │ Cloudflare Tunnel
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│              JETSON ORIN NANO (192.168.1.155)                   │
│  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────┐ │
│  │  GPS Server      │  │  Vision Tracker  │  │ Fusion Engine │ │
│  │  (port 8765)     │  │  (YOLOv8n)       │  │  (Kalman)     │ │
│  └──────────────────┘  └──────────────────┘  └───────────────┘ │
│           │                      │                     │         │
│           └──────────────────────┴─────────────────────┘         │
│                                  │                               │
│                   ┌──────────────▼───────────────┐               │
│                   │   Gimbal Controller (UART)   │               │
│                   │   115200 8N1 → /dev/ttyACM0  │               │
│                   └──────────────┬───────────────┘               │
└──────────────────────────────────┼───────────────────────────────┘
                                   │ Serial Commands
                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                   STM32 NUCLEO-F401RE                           │
│   Real-time stepper control (1/8 microstepping)                 │
│   Limit switches, homing, position tracking                     │
└────────────────────────────┬────────────────────────────────────┘
                             │ STEP/DIR signals
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│            2x DRV8825 Stepper Drivers + Motors                  │
│         Pan: Tevo NEMA17  |  Tilt: Moons MS17HD5P4100          │
└─────────────────────────────────────────────────────────────────┘
```

## Hardware Requirements

See [BOM.md](BOM.md) for complete bill of materials.

### Core Components

| Component | Model | Purpose |
|-----------|-------|---------|
| Compute | NVIDIA Jetson Orin Nano 8GB | AI inference, sensor fusion, control |
| Microcontroller | STM32 Nucleo-F401RE | Real-time motor control |
| Stepper Drivers | 2x Pololu DRV8825 | Motor drivers (1/8 microstepping) |
| Motors | 2x NEMA17 steppers | Pan/tilt actuation |
| Camera | Android phone via DroidCam | Video feed (IP: 192.168.1.33:4747) |
| GPS Devices | Apple Watch + iPhone | Subject tracking |
| Power | 18V laptop brick | Motor power (temporary) |

### Network Configuration

- **Orin IP:** 192.168.1.155 (SSH alias: `orin`)
- **Camera IP:** 192.168.1.33:4747 (DroidCam)
- **Public Endpoint:** wss://ws.stonezone.net (Cloudflare Tunnel)
- **Local GPS Server:** localhost:8765

## Quick Start

### 1. Hardware Setup

```bash
# Connect Nucleo to Orin via USB
# Appears as /dev/ttyACM0

# Connect DroidCam camera
# Android phone on same network: 192.168.1.33

# Power on motors (18V supply to DRV8825 drivers)
```

### 2. SSH to Orin

```bash
ssh orin
# or: ssh zack@192.168.1.155
# sudo password: motherfucker
```

### 3. Run Tracking System

```bash
cd /data/projects/gimbal

# Start GPS server (receives Watch data via Cloudflare)
python3 gps_server.py &

# Start vision tracker
python3 vision_tracker.py
```

### 4. Deploy iOS/Watch Apps

Build and install the Swift apps from `gps-relay-framework/`:
- iPhone app relays Watch GPS to `wss://ws.stonezone.net`
- Watch app streams GPS from worn device

## Project Structure

```
jetsonTracker/
├── README.md                 # This file
├── BOM.md                    # Bill of materials
├── LICENSE                   # Project license
├── .gitignore
│
├── orin/                     # Jetson Orin Nano code
│   ├── README.md
│   ├── requirements.txt
│   ├── gps_server.py         # WebSocket server for GPS (port 8765)
│   ├── gps_fusion/           # GPS processing modules
│   │   ├── fusion_engine.py  # Kalman filter fusion
│   │   ├── geo_calc.py       # Haversine, bearing calculations
│   │   ├── gps_client.py     # (deprecated - use gps_server.py)
│   │   └── tracker_integration.py
│   ├── vision/
│   │   └── vision_tracker.py # YOLOv8 detection pipeline
│   ├── gimbal_control/
│   │   └── gimbal_controller.py # Serial UART controller
│   ├── scripts/
│   │   ├── phone_webcam.sh   # DroidCam setup helper
│   │   ├── test_detection.py
│   │   └── check_camera.py
│   └── models/
│       └── yolov8n.engine    # TensorRT model
│
├── nucleo/                   # STM32 firmware
│   ├── README.md
│   ├── firmware/
│   │   └── stepper_control/  # CubeIDE project
│   │       └── Sources/main.c
│   ├── docs/
│   │   ├── hardware-contract.md
│   │   └── pin-mapping.md
│   └── images/
│
├── gps-relay-framework/      # iOS/Watch Swift apps
│   ├── Sources/
│   ├── iosTrackerApp/
│   └── watchTrackerApp Watch App/
│
├── gimbal/                   # Hardware files
│   └── README.md             # Placeholder for STL files
│
├── docs/                     # Documentation
│   ├── ARCHITECTURE.md       # Full system architecture
│   ├── PROJECT_STATUS.md     # Current status
│   ├── CLOUDFLARE_SETUP.md   # Tunnel setup guide
│   ├── architecture/
│   ├── wiring/
│   ├── setup/
│   └── api/
│
├── config/                   # Configuration files
│   ├── cloudflare/
│   │   └── config.yml        # Tunnel config
│   ├── network/
│   └── systemd/
│
├── hardware/                 # Hardware documentation
│   ├── BOM.md → ../BOM.md
│   ├── power/
│   └── mechanical/
│
└── archive/                  # Old documentation (gitignored)
    └── ...
```

## Current Status

### Completed

- [x] Nucleo firmware with UART protocol and limit switches
- [x] YOLOv8 detection on Orin (CPU mode, ~8 FPS)
- [x] Cloudflare tunnel setup (wss://ws.stonezone.net)
- [x] DroidCam camera integration (192.168.1.33:4747)
- [x] Serial communication Orin ↔ Nucleo verified
- [x] GPS fusion module architecture
- [x] Project reorganization complete

### In Progress

- [ ] Deploy gps_server.py to Orin
- [ ] Build iOS/Watch apps with Cloudflare endpoint
- [ ] End-to-end GPS → Fusion → Gimbal testing

### Known Issues

1. ~~**RESOLVED:** gps_server.py heartbeat handler added~~ (Nov 29, 2025)
   - Swift app sends `{"type": "ping"}`, server now responds with `{"type": "pong"}`

2. **Performance:** TensorRT/PyTorch CUDA issue
   - Workaround: Using CPU mode with yolov8n.pt
   - Fix: Reinstall Jetson-specific PyTorch wheel

3. **Hardware:** Reed switch limit switches wired but not physically installed

## Communication Protocols

### UART (Orin ↔ Nucleo)

**Settings:** 115200 baud, 8N1, /dev/ttyACM0

```
PING              → PONG
PAN_REL:+100      → OK PAN:100
TILT_REL:-50      → OK TILT:-50
HOME_ALL          → ALL HOMED
GET_POS           → POS PAN:500 TILT:200
GET_STATUS        → STATUS PAN_SW:0 TILT_SW:1 PAN_OK:1 TILT_OK:1
```

Position limits: PAN ±8000 steps, TILT ±2000 steps

### WebSocket (Watch → iPhone → Cloudflare → Orin)

**Endpoint:** wss://ws.stonezone.net → localhost:8765

**RelayUpdate Payload:**
```json
{
  "remote": {
    "ts_unix_ms": 1732868400000,
    "source": "watchOS",
    "lat": 37.7749,
    "lon": -122.4194,
    "alt_m": 10.5,
    "h_accuracy_m": 5.0,
    "speed_mps": 1.2,
    "course_deg": 45.0,
    "heading_deg": 90.0,
    "battery_pct": 0.85,
    "seq": 123
  },
  "base": {
    "ts_unix_ms": 1732868400000,
    "source": "iOS",
    "lat": 37.7748,
    "lon": -122.4195,
    "..."
  }
}
```

## Development

### Building Nucleo Firmware

```bash
cd nucleo/firmware/stepper_control/Debug
make clean && make -j4

# Flash via STM32CubeIDE or ST-Link CLI
```

### Running Tests

```bash
# On Orin
cd /data/projects/gimbal

# Test camera detection
python3 scripts/test_detection.py

# Test gimbal controller
python3 -c "from gimbal_control.gimbal_controller import GimbalController; \
            g = GimbalController(); g.ping()"

# Test GPS fusion (mock data)
python3 scripts/test_fusion.py
```

### Deploying to Orin

```bash
# Copy files from local orin/ to Jetson
rsync -avz orin/ orin:/data/projects/gimbal/

# Install dependencies
ssh orin "cd /data/projects/gimbal && pip3 install -r requirements.txt"
```

## Documentation

- [ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md) - Complete system architecture
- [PROJECT_STATUS.md](docs/PROJECT_STATUS.md) - Current status and known issues
- [END_TO_END_TESTING.md](docs/END_TO_END_TESTING.md) - Integration test procedure
- [FAILURE_MODES.md](docs/FAILURE_MODES.md) - Failure handling and recovery
- [CLOUDFLARE_SETUP.md](docs/setup/CLOUDFLARE_SETUP.md) - Tunnel configuration
- [BOM.md](BOM.md) - Hardware bill of materials
- [nucleo/README.md](nucleo/README.md) - Firmware documentation
- [orin/README.md](orin/README.md) - Software stack details

## Contributing

This is a personal project, but documentation improvements and bug reports are welcome.

## License

[To be determined]

## Acknowledgments

- NVIDIA for Jetson platform and samples
- Ultralytics for YOLOv8
- Cloudflare for tunnel infrastructure
- STMicroelectronics for CubeIDE and HAL libraries

---

**Last Updated:** November 28, 2025
**Project Status:** Phase B - Integration & Testing
**Contact:** [Your contact info]
