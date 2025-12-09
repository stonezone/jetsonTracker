# GPS Module Setup Guide

Setup instructions for the BN-220 GPS module on Jetson Orin Nano.

---

## Overview

The BN-220 GPS module provides base station positioning for the gimbal. Combined with motor position as heading, this eliminates the need for an iPhone in the GPS pipeline.

## Hardware

### Recommended Module

| Module | Chip | Constellations | Accuracy | Price |
|--------|------|----------------|----------|-------|
| **BN-220** | u-blox M8 | GPS+GLONASS+BeiDou+Galileo | 2.5m | $13-15 |

**Why BN-220 over NEO-6M?**
- Multi-constellation = faster satellite lock
- Better accuracy (2.5m vs 5m)
- GLONASS helps in Hawaii when GPS satellites are low on horizon
- Only $2 more

### Where to Buy
- Amazon: Search "BN-220 GPS"
- AliExpress: ~$10-12 with longer shipping

---

## Wiring Options

### Option 1: USB GPS Dongle (Easiest)
If using a USB GPS dongle instead of BN-220:
1. Plug into Orin USB port
2. Device appears as `/dev/ttyUSB0` or `/dev/ttyACM0`
3. No wiring required

### Option 2: UART Connection (BN-220)

**BN-220 Pinout:**
| Wire Color | Function | Connect To |
|------------|----------|------------|
| Red | VCC (3.3-5V) | Orin 3.3V (Pin 1) or 5V (Pin 2) |
| Black | GND | Orin GND (Pin 6) |
| White/Yellow | TX | Orin RX (Pin 10 / GPIO15) |
| Green | RX | Orin TX (Pin 8 / GPIO14) - optional for config |

**Orin 40-Pin Header Reference:**
```
Pin 1  = 3.3V
Pin 2  = 5V
Pin 6  = GND
Pin 8  = UART1_TX (GPIO14)
Pin 10 = UART1_RX (GPIO15)
```

**Wiring Diagram:**
```
BN-220                    Orin 40-Pin Header
┌──────────┐              ┌──────────────┐
│ VCC (Red)├─────────────►│ Pin 1 (3.3V) │
│ GND (Blk)├─────────────►│ Pin 6 (GND)  │
│ TX (Wht) ├─────────────►│ Pin 10 (RX)  │
│ RX (Grn) │              │ Pin 8 (TX)   │ ← Optional
└──────────┘              └──────────────┘
```

---

## Software Setup

### 1. Install pynmea2

```bash
ssh orin
pip3 install pynmea2 pyserial
```

### 2. Find the GPS Device

```bash
# List serial devices
ls -la /dev/ttyUSB* /dev/ttyACM* /dev/ttyTHS*

# For UART connection on Orin
ls -la /dev/ttyTHS1
```

### 3. Test GPS Connection

```bash
# Quick test - read raw NMEA
cat /dev/ttyUSB0  # or /dev/ttyTHS1 for UART

# You should see lines like:
# $GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,47.0,M,,*47
# $GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A
```

### 4. Parse with Python

```python
#!/usr/bin/env python3
"""Test GPS module connection."""

import pynmea2
import serial
import io

# Adjust device path as needed
GPS_DEVICE = '/dev/ttyUSB0'  # USB dongle
# GPS_DEVICE = '/dev/ttyTHS1'  # UART on Orin
GPS_BAUD = 9600

ser = serial.Serial(GPS_DEVICE, GPS_BAUD, timeout=5.0)
sio = io.TextIOWrapper(io.BufferedRWPair(ser, ser))

print(f"Reading GPS from {GPS_DEVICE}...")

while True:
    try:
        line = sio.readline()
        msg = pynmea2.parse(line)

        # GGA messages contain position
        if isinstance(msg, pynmea2.GGA):
            print(f"Lat: {msg.latitude:.6f}, Lon: {msg.longitude:.6f}, "
                  f"Alt: {msg.altitude}m, Sats: {msg.num_sats}")

    except pynmea2.ParseError:
        continue
    except serial.SerialException as e:
        print(f"Serial error: {e}")
        break
```

---

## Integration with Fusion Engine

### GPS Module Reader (New File)

Create `orin/gps_module.py`:

```python
"""GPS module reader for BN-220 or USB GPS dongle.

Provides base station position for the gimbal (replaces iPhone GPS).
"""

import pynmea2
import serial
import io
import threading
import time
from typing import Optional, Callable
from gps_fusion.geo_calc import GeoPoint

class GPSModule:
    """Read position from local GPS module."""

    def __init__(self, device: str = '/dev/ttyUSB0', baud: int = 9600):
        self.device = device
        self.baud = baud
        self.position: Optional[GeoPoint] = None
        self.last_update: float = 0
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Start reading GPS in background thread."""
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop GPS reading."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    def _read_loop(self):
        """Background thread to read GPS."""
        try:
            ser = serial.Serial(self.device, self.baud, timeout=5.0)
            sio = io.TextIOWrapper(io.BufferedRWPair(ser, ser))

            while self._running:
                try:
                    line = sio.readline()
                    msg = pynmea2.parse(line)

                    if isinstance(msg, pynmea2.GGA) and msg.latitude:
                        self.position = GeoPoint(
                            lat=msg.latitude,
                            lon=msg.longitude,
                            alt=msg.altitude if msg.altitude else 0,
                            timestamp=time.time()
                        )
                        self.last_update = time.time()

                except pynmea2.ParseError:
                    continue

        except serial.SerialException as e:
            print(f"GPS module error: {e}")

    def get_position(self) -> Optional[GeoPoint]:
        """Get latest position (None if no fix or stale > 5s)."""
        if self.position and (time.time() - self.last_update) < 5.0:
            return self.position
        return None


# Usage example
if __name__ == '__main__':
    gps = GPSModule('/dev/ttyUSB0')
    gps.start()

    try:
        while True:
            pos = gps.get_position()
            if pos:
                print(f"Position: {pos.lat:.6f}, {pos.lon:.6f}")
            else:
                print("Waiting for GPS fix...")
            time.sleep(1)
    except KeyboardInterrupt:
        gps.stop()
```

### Update Fusion Engine

In `fusion_engine.py`, replace iPhone GPS with GPS module:

```python
from gps_module import GPSModule
from gps_fusion.geo_calc import get_heading_from_motor_position, calculate_relative_position

# Initialize GPS module
gps_module = GPSModule('/dev/ttyUSB0')
gps_module.start()

# In tracking loop:
gimbal_pos = gps_module.get_position()
if gimbal_pos:
    # Get motor heading (after HOME_ALL)
    motor_heading = get_heading_from_motor_position(current_pan_steps)

    # Calculate relative position using motor heading
    rel_pos = calculate_relative_position(
        gimbal=gimbal_pos,
        target=watch_gps_position,
        motor_heading=motor_heading
    )
```

---

## Mounting

### Best Practices

1. **Clear sky view** - Mount on top of gimbal with unobstructed view
2. **Away from interference** - Keep 10+ cm from stepper motors and power wires
3. **Secure mounting** - Vibration can affect GPS lock
4. **Waterproofing** - Use conformal coating or enclosure for outdoor use

### Suggested Mounting Location

```
        [Camera]
           │
    ┌──────┴──────┐
    │   Gimbal    │
    │             │
    │  [GPS] ◄────┼── Mount here (clear sky view)
    │             │
    └─────────────┘
           │
      [Tripod]
```

---

## Troubleshooting

### No Data from GPS

1. Check wiring (VCC, GND, TX→RX)
2. Verify device path: `ls /dev/tty*`
3. Check permissions: `sudo chmod 666 /dev/ttyUSB0`
4. Test with `cat /dev/ttyUSB0`

### No Satellite Fix

1. Move to open sky (indoors = no fix)
2. Wait 30-60 seconds for cold start
3. Multi-constellation helps in obstructed areas

### Wrong Baud Rate

BN-220 defaults to 9600 baud. If you see garbage:
```bash
# Try different baud rates
stty -F /dev/ttyUSB0 9600
stty -F /dev/ttyUSB0 38400
stty -F /dev/ttyUSB0 115200
```

### Permission Denied

```bash
# Add user to dialout group
sudo usermod -a -G dialout $USER
# Logout and login again
```

---

## Quick Reference

| Setting | Value |
|---------|-------|
| Device (USB) | `/dev/ttyUSB0` or `/dev/ttyACM0` |
| Device (UART) | `/dev/ttyTHS1` |
| Baud Rate | 9600 (default) |
| Library | `pynmea2` |
| Update Rate | 1-10 Hz (typically 1 Hz default) |

---

**Created**: 2025-12-07
**Status**: Ready for hardware installation
**Hardware**: Not yet installed - order BN-220 (~$15)
