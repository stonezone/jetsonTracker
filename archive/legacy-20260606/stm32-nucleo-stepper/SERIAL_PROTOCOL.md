# Serial Protocol Specification

UART communication protocol between Jetson Orin Nano and STM32 Nucleo-F401RE.

---

## Connection Parameters

| Parameter | Value |
|-----------|-------|
| **Baud Rate** | 115200 |
| **Data Bits** | 8 |
| **Parity** | None (N) |
| **Stop Bits** | 1 |
| **Flow Control** | None |
| **Line Ending** | `\n` (LF, 0x0A) |
| **Device** | `/dev/ttyACM0` (USB) or `/dev/ttyTHS1` (UART pins) |

**Abbreviated:** 115200 8N1

---

## Command Format

All commands are ASCII text terminated with a newline character (`\n`).

```
<COMMAND>[:<PARAMETER>]\n
```

**Examples:**
```
PING\n
PAN_REL:+100\n
TILT_ABS:500\n
```

---

## Command Reference

### Connection & Status

#### PING
Test connection to Nucleo.

**Format:** `PING`

**Response:** `PONG`

**Example:**
```
→ PING
← PONG
```

---

#### GET_POS
Get current position of both axes in steps.

**Format:** `GET_POS`

**Response:** `POS PAN:<pan_steps> TILT:<tilt_steps>`

**Example:**
```
→ GET_POS
← POS PAN:500 TILT:-200
```

---

#### GET_STATUS
Get detailed status including limit switches and homing flags.

**Format:** `GET_STATUS`

**Response:** `STATUS PAN_SW:<0|1> TILT_SW:<0|1> PAN_OK:<0|1> TILT_OK:<0|1>`

**Fields:**
- `PAN_SW`: Pan home switch state (1 = triggered)
- `TILT_SW`: Tilt home switch state (1 = triggered)
- `PAN_OK`: Pan homed flag (1 = homed)
- `TILT_OK`: Tilt homed flag (1 = homed)

**Example:**
```
→ GET_STATUS
← STATUS PAN_SW:0 TILT_SW:1 PAN_OK:1 TILT_OK:1
```

---

### Motion Commands

#### PAN_REL
Move pan axis relative to current position.

**Format:** `PAN_REL:<steps>`

**Parameters:**
- `steps`: Signed integer, positive = clockwise, negative = counter-clockwise

**Response:** `OK PAN:<actual_steps>` or `ERROR:PAN_REL:<steps>`

**Notes:**
- Motion stops at software limits (±8000 steps)
- Actual steps may be less than requested if limit is hit

**Example:**
```
→ PAN_REL:+100
← OK PAN:100

→ PAN_REL:-7950  (when at position 7900)
← OK PAN:-7900   (stopped at -8000 limit)
```

---

#### TILT_REL
Move tilt axis relative to current position.

**Format:** `TILT_REL:<steps>`

**Parameters:**
- `steps`: Signed integer, positive = up, negative = down

**Response:** `OK TILT:<actual_steps>` or `ERROR:TILT_REL:<steps>`

**Limits:** ±2000 steps

**Example:**
```
→ TILT_REL:+50
← OK TILT:50
```

---

#### PAN_ABS
Move pan axis to absolute position.

**Format:** `PAN_ABS:<position>`

**Parameters:**
- `position`: Signed integer (-8000 to +8000)

**Response:** `OK PAN:<position>` or `ERROR:PAN_ABS:<position>`

**Example:**
```
→ PAN_ABS:0
← OK PAN:0
```

---

#### TILT_ABS
Move tilt axis to absolute position.

**Format:** `TILT_ABS:<position>`

**Parameters:**
- `position`: Signed integer (-2000 to +2000)

**Response:** `OK TILT:<position>` or `ERROR:TILT_ABS:<position>`

**Example:**
```
→ TILT_ABS:1000
← OK TILT:1000
```

---

### Homing Commands

#### HOME_PAN
Home the pan axis to the negative limit switch.

**Format:** `HOME_PAN`

**Response:**
- `HOMING PAN...` (immediate)
- `PAN HOMED` (when complete)

**Behavior:**
1. Rotates counter-clockwise until limit switch triggers
2. Sets current position to 0
3. Sets homed flag

**Example:**
```
→ HOME_PAN
← HOMING PAN...
  [motion occurs]
← PAN HOMED
```

---

#### HOME_TILT
Home the tilt axis to the negative limit switch.

**Format:** `HOME_TILT`

**Response:**
- `HOMING TILT...` (immediate)
- `TILT HOMED` (when complete)

**Example:**
```
→ HOME_TILT
← HOMING TILT...
  [motion occurs]
← TILT HOMED
```

---

#### HOME_ALL
Home both axes sequentially (pan first, then tilt).

**Format:** `HOME_ALL`

**Response:**
- `HOMING PAN...` → `PAN HOMED`
- `HOMING TILT...` → `TILT HOMED`
- `ALL HOMED`

**Example:**
```
→ HOME_ALL
← HOMING PAN...
← PAN HOMED
← HOMING TILT...
← TILT HOMED
← ALL HOMED
```

---

#### CENTER
Move both axes to center position (0, 0).

**Format:** `CENTER`

**Response:** `CENTERED`

**Prerequisite:** Both axes must be homed first

**Example:**
```
→ CENTER
← CENTERED
```

---

## Error Handling

### Error Response Format

`ERROR:<original_command>`

**Example:**
```
→ INVALID_CMD
← ERROR:INVALID_CMD
```

### Common Errors

| Situation | Response | Cause |
|-----------|----------|-------|
| Unknown command | `ERROR:<command>` | Command not recognized |
| Invalid parameter | `ERROR:<command>:<param>` | Parameter out of range or malformed |
| Not homed | (no error, motion clamped) | Attempting motion before homing |
| Buffer overflow | (no response) | Command too long (>64 chars) |

---

## Position System

### Coordinate Frame

- **Pan Zero:** Camera facing forward (relative to base)
- **Tilt Zero:** Camera horizontal
- **Pan Positive:** Clockwise rotation (viewed from above)
- **Tilt Positive:** Upward tilt

### Software Limits

| Axis | Minimum | Maximum | Range |
|------|---------|---------|-------|
| **Pan** | -8000 | +8000 | 16000 steps (±180°) |
| **Tilt** | -2000 | +2000 | 4000 steps (±90°) |

### Steps to Degrees Conversion

With 1/8 microstepping on 1.8° motors:

```
Steps per revolution = 200 × 8 = 1600 steps
Degrees per step = 360° / 1600 = 0.225°

Pan:  ±8000 steps = ±1800° → Approximately ±180° with gear reduction
Tilt: ±2000 steps = ±450°  → Approximately ±90° with gear reduction
```

**Note:** Actual degrees depend on gear ratios in gimbal mechanism.

---

## Boot Sequence

When the Nucleo powers on or resets:

1. **Initialization** (200ms): GPIOs configured, UART started
2. **Ready Message:** `READY` transmitted
3. **Idle State:** Waits for commands

**Example Boot Log:**
```
[Power on]
← READY
→ PING
← PONG
→ GET_STATUS
← STATUS PAN_SW:0 TILT_SW:0 PAN_OK:0 TILT_OK:0
```

---

## Python Client Example

```python
import serial
import time

class GimbalController:
    def __init__(self, port='/dev/ttyACM0', baudrate=115200):
        self.ser = serial.Serial(port, baudrate, timeout=1)
        time.sleep(0.5)  # Wait for Nucleo boot
        self.ser.reset_input_buffer()

    def send_command(self, cmd):
        """Send command and return response."""
        self.ser.write(f"{cmd}\n".encode('ascii'))
        return self.ser.readline().decode('ascii').strip()

    def ping(self):
        """Test connection."""
        return self.send_command("PING") == "PONG"

    def home_all(self):
        """Home both axes."""
        responses = []
        self.ser.write(b"HOME_ALL\n")
        while True:
            resp = self.ser.readline().decode('ascii').strip()
            responses.append(resp)
            if resp == "ALL HOMED":
                break
        return responses

    def move_relative(self, pan_steps=0, tilt_steps=0):
        """Move relative to current position."""
        results = {}
        if pan_steps != 0:
            results['pan'] = self.send_command(f"PAN_REL:{pan_steps:+d}")
        if tilt_steps != 0:
            results['tilt'] = self.send_command(f"TILT_REL:{tilt_steps:+d}")
        return results

    def get_position(self):
        """Get current position as (pan, tilt) tuple."""
        resp = self.send_command("GET_POS")
        # Parse "POS PAN:500 TILT:-200"
        parts = resp.split()
        pan = int(parts[1].split(':')[1])
        tilt = int(parts[2].split(':')[1])
        return (pan, tilt)

    def close(self):
        self.ser.close()

# Usage
gimbal = GimbalController()
assert gimbal.ping(), "Nucleo not responding"
gimbal.home_all()
gimbal.move_relative(pan_steps=100, tilt_steps=50)
pos = gimbal.get_position()
print(f"Current position: Pan={pos[0]}, Tilt={pos[1]}")
gimbal.close()
```

---

## Timing & Performance

### Command Latency

| Operation | Typical Duration |
|-----------|------------------|
| PING | <5ms |
| GET_POS | <10ms |
| GET_STATUS | <10ms |
| PAN_REL/TILT_REL | Depends on steps |
| HOME_PAN | 2-5 seconds |
| HOME_TILT | 1-3 seconds |

### Motion Speed

- **Homing Speed:** ~1000 steps/sec
- **Normal Move Speed:** Firmware-dependent (currently no ramping)
- **Max Throughput:** ~100 commands/sec (limited by UART parsing)

---

## Future Enhancements

Potential protocol extensions (not yet implemented):

- `SET_SPEED:<axis>:<speed>` - Adjust motion speed
- `SET_ACCEL:<axis>:<accel>` - Configure acceleration
- `GET_CURRENT` - Read motor current draw
- `EMERGENCY_STOP` - Immediate halt
- `GET_TEMP` - Read driver temperature
- `SET_LIMIT:<axis>:<min>:<max>` - Runtime limit adjustment

---

**Protocol Version:** 1.0
**Last Updated:** November 28, 2025
**Firmware Compatibility:** stepper_control v1.0+
