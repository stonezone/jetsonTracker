# Robot Cameraman - System Architecture

**Project:** Real-Time Subject Tracking Gimbal with GPS-Vision Fusion
**Last Updated:** November 29, 2025

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Hardware Components](#hardware-components)
3. [Software Architecture](#software-architecture)
4. [Communication Protocols](#communication-protocols)
5. [GPS-Vision Fusion](#gps-vision-fusion)
6. [Cloudflare Tunnel](#cloudflare-tunnel)
7. [Nucleo Stepper Control](#nucleo-stepper-control)
8. [Power System](#power-system)
9. [Directory Structure](#directory-structure)
10. [Deployment Guide](#deployment-guide)

---

## System Overview

A robot cameraman that tracks a subject using a 2-axis pan/tilt gimbal. The system combines:

- **Visual tracking** via YOLOv8 person detection on camera feed
- **GPS tracking** via Apple Watch location data
- **Fusion engine** that blends both sources for robust tracking

### Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              TRACKING SUBJECT                                │
│                           (wearing Apple Watch)                              │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ GPS Location
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                               APPLE WATCH                                    │
│                          GPS + Motion Sensors                                │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ Bluetooth/LTE
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            iPHONE (Relay App)                                │
│         - Receives Watch GPS data                                            │
│         - Sends to Orin via Cloudflare Tunnel (wss://ws.stonezone.net)      │
│         - Also provides phone location/heading for base station reference   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ WebSocket over Cloudflare Tunnel
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         JETSON ORIN NANO (Compute)                           │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                          GPS Server (port 8765)                        │  │
│  │  - Receives Watch/Phone GPS via Cloudflare Tunnel                     │  │
│  │  - Calculates relative position (bearing, distance, pan/tilt angles)  │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                      │                                       │
│                                      ▼                                       │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                           Fusion Engine                                │  │
│  │  - Blends GPS + Vision tracking                                       │  │
│  │  - Kalman filter for smooth predictions                               │  │
│  │  - Outputs target pan/tilt angles                                     │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│         ▲                            │                                       │
│         │                            ▼                                       │
│  ┌──────┴────────────┐   ┌───────────────────────────────────────────────┐  │
│  │  Vision Tracker   │   │              Gimbal Controller                 │  │
│  │  - YOLOv8n        │   │  - Serial UART to Nucleo (115200 baud)        │  │
│  │  - Person detect  │   │  - Sends PAN_REL/TILT_REL commands            │  │
│  │  - Frame offsets  │   │  - Tracks position in degrees                 │  │
│  └───────────────────┘   └───────────────────────────────────────────────┘  │
│         ▲                            │                                       │
│         │                            │ USB Serial (/dev/ttyACM0)            │
└─────────│────────────────────────────│───────────────────────────────────────┘
          │                            │
          │ USB Camera                 ▼
┌─────────┴─────────┐    ┌─────────────────────────────────────────────────────┐
│  ANDROID PHONE    │    │                 STM32 NUCLEO-F401RE                  │
│  (Camera Source)  │    │   ┌─────────────────────────────────────────────┐   │
│  - scrcpy stream  │    │   │              Bare-Metal Firmware             │   │
│  - /dev/video10   │    │   │  - UART command parser (115200 8N1)          │   │
└───────────────────┘    │   │  - DRV8825 stepper drivers                   │   │
                         │   │  - 4 limit switch inputs                     │   │
                         │   │  - Position tracking in steps                │   │
                         │   └─────────────────────────────────────────────┘   │
                         │                       │                              │
                         └───────────────────────│──────────────────────────────┘
                                                 │ STEP/DIR signals
                                                 ▼
                         ┌─────────────────────────────────────────────────────┐
                         │              2x DRV8825 MOTOR DRIVERS                │
                         │         PAN (Tevo NEMA17) + TILT (Moons)            │
                         └─────────────────────────────────────────────────────┘
                                                 │
                                                 ▼
                         ┌─────────────────────────────────────────────────────┐
                         │                  PAN/TILT GIMBAL                     │
                         │              (3D printed, herringbone gears)         │
                         └─────────────────────────────────────────────────────┘
```

---

## Hardware Components

### Compute Unit: Jetson Orin Nano

| Spec | Value |
|------|-------|
| Model | NVIDIA Jetson Orin Nano Developer Kit |
| CPU | 6-core Arm Cortex-A78AE |
| GPU | 1024 CUDA cores, Tensor Cores |
| RAM | 8GB LPDDR5 |
| Storage | NVMe SSD (mounted at /data) |
| IP Address | 192.168.1.155 |
| SSH | `ssh orin` (keys configured) |
| Username | zack |

### Microcontroller: STM32 Nucleo-F401RE

| Spec | Value |
|------|-------|
| MCU | STM32F401RET6 (Arm Cortex-M4, 84MHz) |
| Flash | 512KB |
| RAM | 96KB |
| Programming | Via USB (ST-Link V2-1) |
| IDE | STM32CubeIDE |
| Connection to Orin | USB Serial (/dev/ttyACM0) |

### Stepper Motor Drivers: 2x Pololu DRV8825

| Driver | Motor | Vref | Current Limit |
|--------|-------|------|---------------|
| PAN | Tevo NEMA17 (17HD4401) | 0.60V | ~1.2A/phase |
| TILT | Moons MS17HD5P4100 | 0.50V | ~1.0A/phase |

### Camera: Android Phone

| Spec | Value |
|------|-------|
| Connection | USB via ADB/scrcpy |
| Virtual Device | /dev/video10 (v4l2loopback) |
| Resolution | 640x480 @ 30fps |
| App | Native camera app |

### Apple Devices

| Device | Role |
|--------|------|
| Apple Watch | GPS tracking of subject (worn by target) |
| iPhone | Relay GPS to Orin, mounted on gimbal for heading reference |

---

## Software Architecture

### Orin Software Stack

```
/data/projects/gimbal/
├── vision_tracker.py      # YOLOv8 detection + tracking loop
├── gimbal_controller.py   # Serial UART controller for Nucleo
├── gps_server.py          # WebSocket server for GPS data (port 8765)
├── phone_webcam.sh        # scrcpy to v4l2loopback script
├── gps_fusion/
│   ├── fusion_engine.py   # GPS-Vision fusion with Kalman filter
│   ├── geo_calc.py        # Haversine distance, bearing calculations
│   ├── gps_client.py      # WebSocket client (deprecated, server mode now)
│   └── tracker_integration.py  # Integration layer
└── models/
    └── yolov8n.engine     # TensorRT-optimized YOLOv8 nano
```

### Key Dependencies

```
# requirements.txt
ultralytics>=8.0.0      # YOLOv8
opencv-python>=4.7.0    # Computer vision
numpy>=1.24.0           # Numerical computing
filterpy>=1.4.5         # Kalman filtering
pyserial>=3.5           # Serial communication
websockets>=12.0        # WebSocket server
pyyaml>=6.0             # Configuration
```

### Object Detection

- **Model:** YOLOv8n (nano variant)
- **Format:** TensorRT engine (.engine) for GPU acceleration
- **Target:** COCO class 0 (person)
- **Confidence:** 0.5 threshold
- **FPS:** ~30fps on Orin GPU

---

## Communication Protocols

### Orin ↔ Nucleo (Serial UART)

| Parameter | Value |
|-----------|-------|
| Port | /dev/ttyACM0 |
| Baud Rate | 115200 |
| Data Bits | 8 |
| Parity | None |
| Stop Bits | 1 |
| Line Ending | `\n` (LF) |

#### Command Set

| Command | Description | Response |
|---------|-------------|----------|
| `PING` | Connection test | `PONG` |
| `PAN_REL:<steps>` | Relative pan move | `OK PAN:<actual>` |
| `TILT_REL:<steps>` | Relative tilt move | `OK TILT:<actual>` |
| `PAN_ABS:<steps>` | Absolute pan position | `OK PAN:<position>` |
| `TILT_ABS:<steps>` | Absolute tilt position | `OK TILT:<position>` |
| `HOME_PAN` | Home pan axis | `PAN HOMED` |
| `HOME_TILT` | Home tilt axis | `TILT HOMED` |
| `HOME_ALL` | Home both axes | `ALL HOMED` |
| `CENTER` | Move to (0, 0) | `CENTERED` |
| `GET_POS` | Get current position | `POS PAN:<p> TILT:<t>` |
| `GET_STATUS` | Get limit switch status | `STATUS PN:<0/1> PP:<0/1> TN:<0/1> TP:<0/1> PH:<0/1> TH:<0/1>` |

#### Status Response Fields

- `PN`: Pan Negative limit (1 = triggered)
- `PP`: Pan Positive limit
- `TN`: Tilt Negative limit
- `TP`: Tilt Positive limit
- `PH`: Pan Homed flag
- `TH`: Tilt Homed flag

### iPhone ↔ Orin (WebSocket)

| Parameter | Value |
|-----------|-------|
| Endpoint | wss://ws.stonezone.net |
| Local Port | 8765 |
| Protocol | WebSocket JSON |

#### RelayUpdate Payload (from iPhone)

```json
{
  "remote": {
    "ts_unix_ms": 1732868400000,
    "source": "watchOS",
    "lat": 37.7749,
    "lon": -122.4194,
    "alt_m": 10.5,
    "h_accuracy_m": 5.0,
    "v_accuracy_m": 8.0,
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
  },
  "latency": {
    "gpsToRelayMs": 50.0,
    "totalMs": 150.0
  }
}
```

---

## GPS-Vision Fusion

### Tracking Modes

| Mode | Description | When Used |
|------|-------------|-----------|
| VISUAL | Vision-only tracking | Subject visible in frame |
| GPS_ASSISTED | Vision primary, GPS backup | Subject partially visible |
| GPS_PRIMARY | GPS-only tracking | Subject lost from frame |

### Fusion Algorithm

1. **GPS Input:** Watch location → calculate bearing/distance from gimbal
2. **Convert to Angles:** Bearing → pan angle, elevation → tilt angle
3. **Vision Input:** YOLOv8 detection → frame offset → pan/tilt correction
4. **Kalman Filter:** Smooth predictions, reduce jitter
5. **Output:** Combined pan/tilt commands to gimbal

### Coordinate System

- **Pan:** 0° = camera forward, +ve = clockwise, range ±180°
- **Tilt:** 0° = horizontal, +ve = up, range ±90°
- **GPS Bearing:** 0° = North, clockwise

---

## Cloudflare Tunnel

### Purpose

The Jetson Orin is behind NAT (home network / mobile hotspot). The Apple Watch on LTE cannot connect directly. Cloudflare Tunnel creates a public endpoint.

### Architecture

```
Apple Watch → iPhone → wss://ws.stonezone.net → Cloudflare Edge → Jetson:8765
```

### Benefits

- **Bypasses Apple's APNS:** Direct connection, no Apple Cloud Relay delay
- **Latency:** <200ms (vs 2-10 seconds through APNS)
- **No Port Forwarding:** Outbound tunnel from Jetson
- **HTTPS/WSS:** Secure by default

### Configuration

| Setting | Value |
|---------|-------|
| Tunnel Name | robot-core |
| Tunnel ID | 3ea6c1a2-5b5a-4d91-b0df-5e458b0fbbf5 |
| Domain | ws.stonezone.net |
| Service | http://localhost:8765 |
| Systemd Service | cloudflared.service |

### Config File (`/etc/cloudflared/config.yml`)

```yaml
tunnel: 3ea6c1a2-5b5a-4d91-b0df-5e458b0fbbf5
credentials-file: /etc/cloudflared/3ea6c1a2-5b5a-4d91-b0df-5e458b0fbbf5.json

ingress:
  - hostname: ws.stonezone.net
    service: http://localhost:8765
  - service: http_status:404
```

---

## Nucleo Stepper Control

### Pin Mapping

#### Motor Control

| Function | Arduino Pin | STM32 Pin | GPIO |
|----------|-------------|-----------|------|
| PAN DIR | D2 | PA10 | GPIOA |
| PAN STEP | D3 | PB3 | GPIOB |
| TILT DIR | D4 | PB5 | GPIOB |
| TILT STEP | D5 | PB4 | GPIOB |

#### Microstepping

| Pin | Arduino | STM32 | Default |
|-----|---------|-------|---------|
| M2 | D8 | PA9 | LOW (0) |
| M1 | D9 | PC7 | HIGH (1) |
| M0 | D10 | PB6 | HIGH (1) |

**Default Mode:** 1/8 microstepping (M2=0, M1=1, M0=1)

#### Limit Switches (Active-Low with Pull-ups)

| Limit | Arduino Pin | STM32 Pin | Description |
|-------|-------------|-----------|-------------|
| PAN_NEG | D6 | PB10 | Pan negative limit (-180°) |
| PAN_POS | D11 | PA7 | Pan positive limit (+180°) |
| TILT_NEG | D7 | PA8 | Tilt negative limit (-90°) |
| TILT_POS | D12 | PA6 | Tilt positive limit (+90°) |

#### Serial (USART2)

| Function | Arduino Pin | STM32 Pin |
|----------|-------------|-----------|
| TX | D1 | PA2 |
| RX | D0 | PA3 |

### Limit Switch Wiring

Reed switches with magnets (2-wire, no power required):

```
Reed Switch          Nucleo
───────────         ────────
   │ │
   │ └──────────────→ GND
   │
   └────────────────→ D6/D7/D11/D12 (has internal pull-up)
```

When magnet is near: switch closes → pin reads LOW → detected as triggered.

### Software Limits

| Axis | Min Steps | Max Steps | Degrees |
|------|-----------|-----------|---------|
| PAN | -8000 | +8000 | ±180° |
| TILT | -2000 | +2000 | ±90° |

---

## Power System

### Current Setup

| Component | Power Source | Voltage |
|-----------|--------------|---------|
| Jetson Orin Nano | DC barrel jack | 12V (from wall adapter) |
| Nucleo F401RE | USB from Orin | 5V |
| Stepper Motors | Laptop brick | 18V |
| DRV8825 Logic | Nucleo 3.3V rail | 3.3V |

### Planned Mobile Setup

| Component | Power Source |
|-----------|--------------|
| All | 6S 12Ah LiPo (22.2V nominal) |
| Steppers | Direct from LiPo (via fuse) |
| Orin | 12V step-down regulator |
| Nucleo | USB from Orin or 5V regulator |

### Power Calculations (TBD)

```
Orin Nano:    ~15W typical
Steppers:     ~2A × 2 × 18V = ~72W peak (much less average)
Total:        ~20-30W average, ~90W peak
```

---

## Directory Structure

```
jetsonTracker/
├── ARCHITECTURE.md              # This document
├── README.md                    # Project overview
│
├── orin/                        # Jetson Orin Nano code
│   ├── README.md
│   ├── requirements.txt
│   ├── gps_fusion/              # GPS processing modules
│   │   ├── fusion_engine.py
│   │   ├── geo_calc.py
│   │   └── tracker_integration.py
│   ├── vision/                  # Object detection
│   │   └── vision_tracker.py
│   ├── gimbal_control/          # Gimbal serial interface
│   │   └── gimbal_controller.py
│   ├── gps_server.py            # WebSocket server for GPS
│   └── scripts/
│       └── phone_webcam.sh
│
├── nucleo/                      # STM32 Nucleo firmware
│   ├── README.md
│   ├── firmware/                # Actual firmware project
│   │   └── stepper_control/
│   │       └── Sources/main.c
│   └── docs/
│       ├── hardware-contract.md
│       └── pin-mapping.md
│
├── gps-relay-framework/         # iOS/Watch app (Swift)
│   ├── Sources/
│   ├── iosTrackerApp/
│   └── watchTrackerApp Watch App/
│
├── docs/                        # Documentation
│   ├── wiring/                  # Wiring diagrams
│   ├── schematics/              # Electrical schematics
│   └── setup/                   # Setup guides
│
├── config/                      # Configuration files
│   ├── cloudflare/              # Tunnel config
│   ├── network/                 # Network settings
│   └── systemd/                 # Service files
│
└── hardware/                    # Hardware documentation
    ├── BOM.md                   # Bill of materials
    ├── power/                   # Power system docs
    └── mechanical/              # 3D prints, mounts
```

---

## Deployment Guide

### Fresh Orin Setup

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Install cloudflared:**
   ```bash
   wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
   sudo dpkg -i cloudflared-linux-arm64.deb
   ```

3. **Configure tunnel:** (requires browser auth)
   ```bash
   cloudflared tunnel login
   cloudflared tunnel create robot-core
   cloudflared tunnel route dns robot-core ws.stonezone.net
   ```

4. **Start services:**
   ```bash
   sudo systemctl start cloudflared
   python3 gps_server.py &
   python3 vision_tracker.py
   ```

### Nucleo Flashing

1. Open `nucleo/firmware/stepper_control/` in STM32CubeIDE
2. Build project
3. Connect Nucleo via USB
4. Flash via Run → Debug

---

## Status & TODOs

### Working

- [x] YOLOv8n person detection on Orin
- [x] Gimbal serial protocol (UART commands)
- [x] Nucleo firmware with limit switches
- [x] Cloudflare tunnel installed and running
- [x] GPS fusion module (geo calculations)

### In Progress

- [ ] GPS WebSocket server deployment
- [ ] Phone camera streaming (scrcpy)
- [ ] End-to-end integration testing

### Future

- [ ] Reed switch hardware installation
- [ ] Mobile power system (LiPo)
- [ ] Weather-sealed enclosure
- [ ] Phone heading → gimbal orientation offset
