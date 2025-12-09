# Limit Switch Wiring Guide

Step-by-step instructions for wiring the 4 reed switch limit switches to the STM32 Nucleo.

---

## Overview

The jetsonTracker gimbal uses 4 reed switches for position limiting:
- **PAN_NEG (D6)** - Pan axis negative limit (home position)
- **PAN_POS (D11)** - Pan axis positive limit
- **TILT_NEG (D7)** - Tilt axis negative limit (home position)
- **TILT_POS (D12)** - Tilt axis positive limit

All switches are at **90 degrees** from center (±90° range for both axes).

---

## Parts Needed

- 4x Reed switches (normally open)
- 4x Magnets (for triggering reed switches)
- Hookup wire (22-24 AWG recommended)
- Wire strippers, soldering iron (optional but recommended)

---

## Pin Mapping

| Limit Switch | Arduino Pin | STM32 Pin | Nucleo Header Location |
|--------------|-------------|-----------|------------------------|
| **PAN_NEG** (Home) | D6 | PB10 | CN5 (Morpho) or CN7 |
| **TILT_NEG** (Home) | D7 | PA8 | CN5 (Morpho) or CN9 |
| **PAN_POS** | D11 | PA7 | CN5 (Morpho) or CN7 |
| **TILT_POS** | D12 | PA6 | CN5 (Morpho) or CN7 |

---

## Wiring Instructions

### Step 1: Identify Reed Switch Wires

Reed switches have 2 wires (no polarity - either wire can go to either pin).

```
Reed Switch
    │ │
    │ └──── Wire 2 (to GND)
    │
    └────── Wire 1 (to signal pin)
```

### Step 2: Wire Each Limit Switch

**For each of the 4 reed switches:**

1. Connect **Wire 1** to the Nucleo Arduino header pin:
   - PAN_NEG → D6
   - TILT_NEG → D7
   - PAN_POS → D11
   - TILT_POS → D12

2. Connect **Wire 2** to **GND** (any ground pin on Nucleo)

**Tip:** You can daisy-chain all GND wires to a single GND connection.

### Step 3: GND Connection Options

Multiple GND pins are available on Nucleo headers:
- CN6 (Arduino header): Pin 7 is GND
- CN5 (Arduino header): Pin 7 is GND
- CN8 (Morpho): Multiple GND pins
- CN7 (Morpho): Multiple GND pins

### Step 4: Physical Mounting

Mount reed switches on the gimbal frame at the 90° and -90° positions:

| Switch | Position | Axis | Location Description |
|--------|----------|------|---------------------|
| PAN_NEG | -90° | Pan (horizontal) | Far left when facing gimbal |
| PAN_POS | +90° | Pan (horizontal) | Far right when facing gimbal |
| TILT_NEG | -90° | Tilt (vertical) | Looking down |
| TILT_POS | +90° | Tilt (vertical) | Looking up |

Mount magnets on the moving gimbal arm, positioned to pass within ~5mm of reed switches at limits.

---

## Wiring Diagram

```
                    ┌─────────────────────┐
                    │   STM32 Nucleo      │
                    │                     │
    PAN_NEG ───────►│ D6  (PB10)         │
    Reed Switch     │                     │
                    │                     │
    TILT_NEG ──────►│ D7  (PA8)          │
    Reed Switch     │                     │
                    │                     │
    PAN_POS ───────►│ D11 (PA7)          │
    Reed Switch     │                     │
                    │                     │
    TILT_POS ──────►│ D12 (PA6)          │
    Reed Switch     │                     │
                    │                     │
    All GND wires ─►│ GND                 │
    (daisy-chain)   │                     │
                    └─────────────────────┘
```

---

## How It Works

1. **Internal Pull-up Resistors**: The STM32 firmware configures pins D6, D7, D11, D12 with internal pull-up resistors.

2. **Normal State (No Magnet)**: Reed switch is OPEN → Pin reads HIGH (3.3V)

3. **Triggered State (Magnet Near)**: Reed switch CLOSES → Pin reads LOW (0V via GND connection)

4. **Firmware Behavior**:
   - Motor stops immediately when limit switch triggers
   - Homing routines use NEG limits (D6/D7) to find home position
   - Position tracking resets to known value after homing

---

## Testing After Wiring

### Test 1: Check Switch Detection

```bash
# From Orin - connect to STM32
python3 -c "
import serial
ser = serial.Serial('/dev/ttyACM0', 115200, timeout=1)
ser.write(b'GET_STATUS\n')
import time
time.sleep(0.1)
print(ser.read(200).decode())
"
```

**Expected output:**
```
STATUS PN:0 PP:0 TN:0 TP:0 PH:0 TH:0
```

Where:
- PN/PP/TN/TP: 0 = switch open (not triggered), 1 = switch triggered
- PH/TH: 0 = not homed, 1 = homed

### Test 2: Trigger Each Switch Manually

Hold a magnet near each reed switch and re-run GET_STATUS:

```
# With magnet on PAN_NEG switch:
STATUS PN:1 PP:0 TN:0 TP:0 PH:0 TH:0
        ↑
        Should change to 1
```

### Test 3: Home Both Axes

```bash
python3 -c "
import serial
ser = serial.Serial('/dev/ttyACM0', 115200, timeout=10)
ser.write(b'HOME_ALL\n')
import time
while True:
    line = ser.readline().decode().strip()
    if line:
        print(line)
    if 'ALL HOMED' in line:
        break
"
```

**Expected sequence:**
```
HOMING PAN...
PAN HOMED
HOMING TILT...
TILT HOMED
ALL HOMED
```

---

## Troubleshooting

### Switch Not Detected

1. **Check wiring continuity** with multimeter
2. **Verify correct pin** - use Nucleo pinout diagram
3. **Test reed switch** - should show continuity when magnet is near
4. **Check GND connection** - must have common ground

### Homing Fails with "LIMIT NOT FOUND"

1. **Magnet too far** - reduce gap to ~3-5mm
2. **Wrong magnet polarity** - try flipping magnet
3. **Reed switch faulty** - test with multimeter

### Motor Stops Unexpectedly

1. **Limit switch triggering early** - adjust magnet/switch position
2. **Wiring short** - check for bare wires touching

---

## Safety Notes

- **Power off** when wiring
- **Double-check** pin connections before powering on
- **Start homing slowly** first time to verify switches work
- **Never run gimbal** without limit switches properly configured

---

**Created:** 2025-12-04
**Status:** Ready for physical wiring
**Hardware:** 4x reed switches installed (not wired)
