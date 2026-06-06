# Bill of Materials - jetsonTracker

Complete hardware requirements for the AI-powered robot cameraman tracking system.

---

## Core Components (Required)

### Compute & Control

| Component | Model/Spec | Qty | Purpose | Status |
|-----------|------------|-----|---------|--------|
| **Jetson Orin Nano** | NVIDIA Orin Nano Dev Kit 8GB | 1 | AI inference, sensor fusion | ✅ Owned |
| **STM32 Nucleo** | Nucleo-F401RE (Cortex-M4, 84MHz) | 1 | Real-time motor control | ✅ Owned |
| **NVMe SSD** | 500GB M.2 NVMe | 1 | Fast storage for Orin | ✅ Installed |

**Specifications:**
- **Jetson Orin Nano:** 6-core ARM A78AE, 1024 CUDA cores, 8GB LPDDR5
- **Nucleo:** 512KB Flash, 96KB RAM, STM32CubeIDE compatible
- **Storage:** Mounted at `/data` with 435GB free

### Motion System

| Component | Model/Spec | Qty | Purpose | Status |
|-----------|------------|-----|---------|--------|
| **Stepper Motors** | NEMA17 Bipolar | 2 | Pan and tilt actuation | ✅ Owned |
| - Pan Motor | Tevo 17HD4401 (1.8°/step) | 1 | Horizontal rotation | ✅ Installed |
| - Tilt Motor | Moons MS17HD5P4100 | 1 | Vertical tilt | ✅ Installed |
| **Stepper Drivers** | Pololu DRV8825 | 2 | Motor control (1/8 microstepping) | ✅ Owned |
| **Limit Switches** | Reed switches with magnets | 4 | Pan/tilt homing & safety | ⚠️ Wired, not installed |
| **Gimbal Frame** | 3D printed with herringbone gears | 1 | Mechanical structure | ✅ Assembled |

**Motor Specifications:**
- **Pan (Tevo):** 1.2A/phase, Vref 0.60V
- **Tilt (Moons):** 1.0A/phase, Vref 0.50V
- **Microstepping:** 1/8 (3200 steps/revolution)
- **Software Limits:** Pan ±8000 steps (±180°), Tilt ±2000 steps (±90°)

### Camera System

| Component | Model/Spec | Qty | Purpose | Status |
|-----------|------------|-----|---------|--------|
| **Android Phone** | Any recent model | 1 | Camera source via DroidCam | ✅ Using |
| **Future: SLR Camera** | With controllable zoom/focus | 1 | Professional video quality | 📋 Planned |

**Current Setup:**
- **Connection:** USB via ADB/scrcpy → /dev/video10 (v4l2loopback)
- **IP Address:** 192.168.1.33:4747
- **Resolution:** 640x480 @ 30fps

### GPS Telemetry

| Component | Model/Spec | Qty | Purpose | Status |
|-----------|------------|-----|---------|--------|
| **Apple Watch** | Any recent model with GPS | 1 | Subject tracking (worn by target) | ✅ Required |
| **iPhone** | Any recent model | 1 | Relay Watch GPS to Orin | ✅ Required |

**Connectivity:**
- Watch → iPhone via Bluetooth or LTE
- iPhone → Orin via WebSocket (wss://ws.stonezone.net)
- Sub-200ms latency (bypassing Apple Cloud Relay)

---

## Power System

### Current (Development) Setup

| Component | Spec | Qty | Purpose | Status |
|-----------|------|-----|---------|--------|
| **Jetson Power** | 12V DC barrel jack | 1 | Orin power | ✅ Wall adapter |
| **Motor Power** | 18V laptop brick | 1 | Stepper driver VMOT | ⚠️ Temporary |
| **Nucleo Power** | USB from Orin (5V) | 1 | Microcontroller | ✅ Working |
| **Buck Converters** | LM2596 (HW-411) | 2 | Voltage regulation | ✅ Owned |

### Planned (Mobile) Setup

| Component | Spec | Qty | Purpose | Notes |
|-----------|------|-----|---------|-------|
| **Battery** | 6S 12Ah LiPo (22.2V nominal) | 1 | Unified power source | 📋 To purchase |
| **12V Regulator** | Step-down to 12V, 5A | 1 | Orin power | Can use LM2596 |
| **Fuse/Protection** | 10A fuse for motor rail | 1 | Safety | Required |
| **Power Distribution** | XT60 connectors, wiring | 1 | Clean power routing | To purchase |

**Power Budget:**
- Orin Nano: ~15W typical, ~25W peak
- Steppers: ~72W peak (2A × 2 × 18V), ~20W average
- **Total:** 20-30W average, 90W peak

---

## Wiring & Connectivity

### Required (Current Gaps)

| Component | Spec | Qty | Purpose | Status |
|-----------|------|-----|---------|--------|
| **Logic Level Shifters** | 4-channel 3.3V↔5V bidirectional | 2 | STM32→Driver reliability | 📋 Recommended |
| **Heavy Gauge Wire** | 18 AWG stranded (red/black) | 3m | Motor power distribution | 📋 To purchase |
| **Dupont Cables** | Female-female jumper wires | 40 | Logic signals | ✅ Owned |
| **USB Cables** | - Mini USB (Nucleo programming) | 1 | STM32 flashing | ✅ Owned |
|  | - USB-A to USB-A (Android ADB) | 1 | Camera connection | ✅ Owned |

### Network Equipment

| Component | Spec | Qty | Purpose | Status |
|-----------|------|-----|---------|--------|
| **WiFi Router** | 2.4/5GHz dual-band | 1 | Local network | ✅ Existing |
| **Cloudflare Account** | Zero Trust tunnel | 1 | Public GPS endpoint | ✅ Configured |

**Network Configuration:**
- Orin: 192.168.1.155 (static recommended)
- Android Camera: 192.168.1.33
- Public endpoint: wss://ws.stonezone.net → Orin:8765

---

## Tools & Development Equipment

### Required for Assembly/Maintenance

| Tool | Purpose | Status |
|------|---------|--------|
| Multimeter | Voltage/continuity testing, Vref adjustment | Required |
| Wire strippers | Preparing power wiring | Required |
| Soldering iron | Connectors, optional modifications | Recommended |
| Small screwdrivers | Nucleo/driver terminal blocks | Required |
| Hex keys | Gimbal assembly, motor mounting | Required |
| 3D Printer | Gimbal parts, mounts | ✅ Used |

### Software Development

| Tool | Purpose | Status |
|------|---------|--------|
| STM32CubeIDE | Nucleo firmware development | ✅ Installed (Mac) |
| Xcode | iOS/Watch app development | ✅ Available |
| SSH Client | Orin remote access | ✅ Configured |
| Git | Version control | ✅ Active |

---

## Optional Upgrades

### Performance Enhancements

| Component | Benefit | Priority |
|-----------|---------|----------|
| Better camera (SLR/mirrorless) | Higher quality video, optical zoom | Medium |
| PTZ camera with SDK | Automated zoom/focus control | Medium |
| Faster stepper motors | Quicker subject acquisition | Low |
| Harmonic drive gears | Smoother, quieter operation | Low |

### Safety & Reliability

| Component | Benefit | Priority |
|-----------|---------|----------|
| Emergency stop button | Manual safety override | High |
| Voltage monitoring | Battery protection | Medium |
| Weather-sealed enclosure | Outdoor operation | Medium |
| Redundant limit switches | Mechanical safety | Low |

---

## Estimated Costs

### Already Owned
- Jetson Orin Nano: $499
- STM32 Nucleo: $15
- NEMA17 Motors (2x): $40
- DRV8825 Drivers (2x): $10
- **Subtotal:** ~$564

### To Purchase (Critical)
- Heavy gauge wire: $10
- Logic level shifters (2x): $6
- Reed switches (4x): $8
- **Subtotal:** ~$24

### Future (Mobile Power)
- 6S LiPo battery: $100-150
- Connectors & fusing: $20
- **Subtotal:** ~$120-170

**Total Project Cost:** ~$708-758 (excluding optional upgrades)

---

## Vendor Recommendations

### Electronics
- **Pololu:** DRV8825 drivers, logic shifters
- **Adafruit:** Sensors, connectors, wire
- **Amazon:** Generic components, wire, power supplies
- **DigiKey/Mouser:** Precision components, hard-to-find parts

### Batteries & Power
- **HobbyKing:** LiPo batteries for robotics
- **Amazon:** Buck converters, regulators
- **Mean Well:** Industrial power supplies (high quality)

### 3D Printing (if needed)
- **Gimbal STL files:** See `gimbal/` folder (to be populated)
- **Service:** Local maker space or online (Shapeways, Xometry)

---

**Notes:**
1. **Reed switches:** Currently wired to pins (PB10, PA8, PA7, PA6) but magnets not physically positioned on gimbal yet.
2. **Camera upgrade:** System designed with abstraction layer for easy camera swapping.
3. **Power system:** Current 18V laptop brick is adequate for testing but not portable. Battery system is planned for mobile deployment.
4. **Level shifters:** While 3.3V→DRV8825 is working, 5V logic is more reliable for long-term use.

**Last Updated:** November 28, 2025
