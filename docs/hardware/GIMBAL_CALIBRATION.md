# Gimbal PAN Axis Calibration - December 8, 2025

**Date Saved**: 2025-12-08
**Category**: Hardware Calibration / Testing

---

## What Was Done

### PAN Axis Full Calibration

Tested both PAN limit switches manually and via motor movement. Both switches confirmed working.

### Calibration Results

| Limit | Position | Switch | Pin | Status |
|-------|----------|--------|-----|--------|
| PAN LEFT (PN) | +1135 steps | Reed switch | D6 (PB10) | ✅ Working |
| PAN RIGHT (PP) | +5700 steps | Reed switch | D11 (PA7) | ✅ Working |
| **Center** | +3417 steps | Calculated | - | ✅ Set |
| **Total Range** | 4565 steps | ~102° | - | - |

### TILT Axis (From Previous Session)

| Limit | Position | Status |
|-------|----------|--------|
| TILT UP (TP) | +1428 steps | ✅ Working |
| TILT DOWN (TN) | -1238 steps | ✅ Working |
| **Center** | +95 steps | Calculated |
| **Total Range** | 2666 steps | ~60° |

## Key Findings

1. **Both PAN switches work** - Manual testing confirmed triggering
2. **Switch naming clarified**:
   - PN = PAN Negative = PAN LEFT (D6/PB10)
   - PP = PAN Positive = PAN RIGHT (D11/PA7)
   - TN = TILT Negative = TILT DOWN (D7/PA8)
   - TP = TILT Positive = TILT UP (D12/PA6)

3. **Calibration method** - Move in 200-step increments, poll GET_STATUS for limit trigger

## Calibration Script Pattern

```python
# Find limits and center
for i in range(80):
    ser.write(b'PAN_REL:-200\n')  # or +200 for opposite
    time.sleep(0.35)
    ser.read(200)

    ser.write(b'GET_STATUS\n')
    time.sleep(0.15)
    status = ser.readline().decode().strip()

    if 'PN:1' in status:  # or PP:1, TN:1, TP:1
        ser.write(b'GET_POS\n')
        pos = ser.readline().decode().strip()
        pan_left = int(pos.split('PAN:')[1].split()[0])
        print('LEFT LIMIT:', pan_left)
        break

# Calculate and move to center
center = (pan_right + pan_left) // 2
move = center - current_pos
ser.write(f'PAN_REL:{move}\n'.encode())
```

## Commands Reference

```bash
GET_STATUS → STATUS PN:x PP:x TN:x TP:x PH:x TH:x
GET_POS → POS PAN:x TILT:y
PAN_REL:200 → OK PAN:xxx
TILT_REL:-200 → OK TILT:xxx
```

## Current Position

After calibration: **PAN:3417 TILT:95** (both axes centered)

## Next Steps

1. Test TILT axis calibration (find fresh limits)
2. Verify HOME_ALL command works with new switch positions
3. Test full motion range
4. Update firmware with calibrated center values (optional)

---

*Saved to Claude OS - Your AI Memory System*
