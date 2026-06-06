# Pin Mapping Reference

Complete pin assignments for all hardware connections.

---

## STM32 Nucleo-F401RE Pin Assignments

### Motor Control (DRV8825 Drivers)

| Function | Arduino Pin | STM32 Pin | GPIO | Connected To |
|----------|-------------|-----------|------|--------------|
| **PAN DIR** | D2 | PA10 | GPIOA | DRV8825 #1 DIR |
| **PAN STEP** | D3 | PB3 | GPIOB | DRV8825 #1 STEP |
| **TILT DIR** | D4 | PB5 | GPIOB | DRV8825 #2 DIR |
| **TILT STEP** | D5 | PB4 | GPIOB | DRV8825 #2 STEP |

### Microstepping Configuration (Shared for Both Drivers)

| Pin Function | Arduino Pin | STM32 Pin | GPIO | Default State |
|--------------|-------------|-----------|------|---------------|
| **M0** | D10 | PB6 | GPIOB | HIGH (1) |
| **M1** | D9 | PC7 | GPIOC | HIGH (1) |
| **M2** | D8 | PA9 | GPIOA | LOW (0) |

**Microstepping Mode:** M2=0, M1=1, M0=1 = **1/8 microstepping**

### Limit Switches (Active-Low with Internal Pull-ups)

| Limit | Arduino Pin | STM32 Pin | GPIO | Description |
|-------|-------------|-----------|------|-------------|
| **PAN_HOME** | D6 | PB10 | GPIOB | Pan negative limit / home |
| **TILT_HOME** | D7 | PA8 | GPIOA | Tilt negative limit / home |
| **PAN_POS** | D11 | PA7 | GPIOA | Pan positive limit |
| **TILT_POS** | D12 | PA6 | GPIOA | Tilt positive limit |

**Wiring:** Reed switch wire 1 → Pin, wire 2 → GND
**Logic:** Open (not triggered) = HIGH, Closed (triggered) = LOW

### UART Communication

| Function | Arduino Pin | STM32 Pin | USART | Connected To |
|----------|-------------|-----------|-------|--------------|
| **TX** | D1 | PA2 | USART2_TX | Jetson RX (Pin 10) or USB virtual COM |
| **RX** | D0 | PA3 | USART2_RX | Jetson TX (Pin 8) or USB virtual COM |

**Settings:** 115200 baud, 8N1

---

## Jetson Orin Nano 40-Pin Header

### UART Connection to Nucleo

| Pin | Function | Voltage | Connected To |
|-----|----------|---------|--------------|
| **6** | GND | 0V | Nucleo GND |
| **8** | UART1_TX (GPIO14) | 3.3V | Nucleo RX (PA3) |
| **10** | UART1_RX (GPIO15) | 3.3V | Nucleo TX (PA2) |

**Device:** `/dev/ttyTHS1` (direct UART) or `/dev/ttyACM0` (via USB)

### Power Pins (Reference Only)

| Pin | Function | Max Current | Notes |
|-----|----------|-------------|-------|
| **1, 17** | 3.3V | 1A total | Do NOT use for motor power |
| **2, 4** | 5V | 3A total | Do NOT use for motor power |
| **6, 9, 14, 20, 25, 30, 34, 39** | GND | - | Common ground reference |

**WARNING:** Do not draw motor current from Jetson header pins!

---

## DRV8825 Stepper Driver Connections

### Driver #1 (Pan Axis)

| Terminal | Function | Connected To | Wire Color (Typical) |
|----------|----------|--------------|----------------------|
| **VMOT** | Motor power (+) | 18V power supply positive | Red |
| **GND** | Power ground | 18V power supply negative | Black |
| **1A, 1B** | Coil A | Pan motor coil A | Red/Blue |
| **2A, 2B** | Coil B | Pan motor coil B | Green/Black |
| **STEP** | Step signal | Nucleo PB3 (D3) | Yellow |
| **DIR** | Direction | Nucleo PA10 (D2) | Orange |
| **EN** | Enable | GND (always enabled) or GPIO | - |
| **M0, M1, M2** | Microstepping | Nucleo D10, D9, D8 | - |

### Driver #2 (Tilt Axis)

| Terminal | Function | Connected To | Wire Color (Typical) |
|----------|----------|--------------|----------------------|
| **VMOT** | Motor power (+) | 18V power supply positive | Red |
| **GND** | Power ground | 18V power supply negative | Black |
| **1A, 1B** | Coil A | Tilt motor coil A | Red/Blue |
| **2A, 2B** | Coil B | Tilt motor coil B | Green/Black |
| **STEP** | Step signal | Nucleo PB4 (D5) | Blue |
| **DIR** | Direction | Nucleo PB5 (D4) | White |
| **EN** | Enable | GND (always enabled) or GPIO | - |
| **M0, M1, M2** | Microstepping | Nucleo D10, D9, D8 (shared) | - |

### Current Limiting (Vref Settings)

| Driver | Motor | Vref | Target Current |
|--------|-------|------|----------------|
| Pan | Tevo NEMA17 | 0.60V | 1.2A/phase |
| Tilt | Moons MS17HD5P4100 | 0.50V | 1.0A/phase |

**Formula:** I_max ≈ Vref × 2

**Adjustment:** Use small ceramic screwdriver on potentiometer while measuring with multimeter between Vref pin and GND.

---

## Power Distribution

### Current Setup

```
18V Laptop Brick
    │
    ├──→ DRV8825 #1 VMOT
    │
    └──→ DRV8825 #2 VMOT

12V Wall Adapter
    │
    └──→ Jetson Orin Nano (barrel jack)

Jetson USB
    │
    └──→ Nucleo USB (provides 5V logic power)
```

### Planned Mobile Setup

```
6S LiPo (22.2V)
    │
    ├──→ [Fuse] → DRV8825 VMOT (both drivers)
    │
    └──→ LM2596 Buck → 12V → Jetson Orin Nano
         └──→ 5V out → Nucleo (optional)
```

---

## Reed Switch Wiring Detail

Reed switches are **normally open** (NO) switches that close when a magnet is nearby.

```
Reed Switch          Nucleo Pin
───────────         ────────────
   │ │
   │ └──────────────→ GND
   │
   └────────────────→ Dx (configured with internal pull-up)
```

**Firmware Configuration:**
- GPIO configured as INPUT with PULL_UP enabled
- Pin reads HIGH when switch is open (no magnet)
- Pin reads LOW when switch is closed (magnet detected)

**Physical Placement:**
- Mount reed switches on gimbal frame at end-of-travel positions
- Attach magnets to moving parts (motor shafts or gears)
- Adjust gap to ~5mm for reliable triggering

---

## USB Connections

### Development/Programming

| Device | Cable Type | Purpose | Device Name |
|--------|------------|---------|-------------|
| Nucleo → Mac | Mini USB | Flashing firmware | /dev/cu.usbmodem* |
| Nucleo → Orin | USB-A to Mini | Serial + power | /dev/ttyACM0 |
| Android Phone → Orin | USB-A to USB-C | Camera via scrcpy | /dev/video10 |

---

## Network Connections

### Local Network

| Device | IP Address | Protocol | Purpose |
|--------|------------|----------|---------|
| Jetson Orin | 192.168.1.155 | SSH (port 22) | Remote access |
| Android Camera | 192.168.1.33 | HTTP (port 4747) | DroidCam video |
| iPhone | DHCP | WebSocket | GPS relay |

### Internet

| Service | Endpoint | Direction | Purpose |
|---------|----------|-----------|---------|
| Cloudflare Tunnel | wss://ws.stonezone.net | IN | GPS data from Watch/iPhone |
| Cloudflare Tunnel | localhost:8765 | OUT | GPS server on Orin |

---

## GPIO Summary by Device

### Nucleo Outputs (to Drivers)

- PA10, PB3 (Pan DIR, STEP)
- PB5, PB4 (Tilt DIR, STEP)
- PB6, PC7, PA9 (M0, M1, M2 shared)

### Nucleo Inputs (from Limit Switches)

- PB10, PA8 (Pan Home, Tilt Home)
- PA7, PA6 (Pan Pos, Tilt Pos)

### Nucleo Communication

- PA2, PA3 (USART2 TX/RX)

### Jetson Used Pins

- Pin 6, 8, 10 (GND, TX, RX for UART)

---

**Last Updated:** November 28, 2025
**Firmware Version:** v1.0 (stepper_control)
**Hardware Revision:** Development prototype
