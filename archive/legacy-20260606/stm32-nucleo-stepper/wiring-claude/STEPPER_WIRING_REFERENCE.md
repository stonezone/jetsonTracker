# STM32 Nucleo + Dual Stepper Driver Wiring Reference

## System Overview

**Microcontroller**: STM32 Nucleo-F103RB (3.3V logic)  
**Drivers**: Two DRV8825 or A4988 stepper motor drivers  
**Power**: External PSU (12V–36V for VMOT)  
**Motors**: Two NEMA 17/23 stepper motors (typical 2A max per phase)

---

## 🚨 CRITICAL SAFETY WARNINGS

### 1. **Capacitors Are Mandatory**
- **What**: 100µF electrolytic or ceramic capacitor
- **Rating**: >35V (48V+ recommended for headroom)
- **Placement**: Directly across VMOT and GND on **each driver board**
- **Position**: As close as physically possible to the driver IC pins
- **Why**: Prevents catastrophic voltage spikes when motors disconnect or decelerate rapidly. **Without this, the driver IC dies instantly.**

### 2. **Never Disconnect Motors While Powered**
- Disconnecting motor phase wires while the driver is active will generate back-EMF spikes that exceed the driver's voltage tolerance (likely >50V from a stepper motor with momentum).
- Always power down drivers before rewiring motors.

### 3. **Common Ground is Essential**
- The PSU's negative rail must connect to STM32's GND.
- Every ground connection from PSU → drivers → Nucleo must be at the same potential.
- Floating grounds cause signal noise and driver malfunction.

### 4. **Motor Phase Current Limiting**
- Before powering up, adjust the Vref potentiometer on each driver.
- **Formula**: `Current_Limit (Amps) = Vref (Volts) × 2`
- Example: For 1A limit, set Vref to 0.5V using a multimeter.
- Failure to set this allows excessive current → motor overheating and driver damage.

---

## Power Connections

### Power Supply to Drivers

| Component | Pin | Wire | Connect To | Notes |
|-----------|-----|------|-----------|-------|
| PSU | + (12–36V) | Red | Driver A, B: VMOT | Do NOT use Nucleo 5V; needs external PSU |
| PSU | − (GND) | Black | Driver A, B: GND (Power) | Also connect to STM32 GND (common reference) |
| Nucleo | 3.3V (CN9) | Orange | Driver A, B: VDD | Powers driver logic circuitry |
| Nucleo | GND (CN9) | Black | Driver A, B: GND (Logic) | Return path for logic signals |

### Capacitor Installation (Per Driver)

| Capacitor Lead | Connect To |
|-----------------|-----------|
| + (positive) | VMOT |
| − (negative) | GND |

Position within 1–2 cm of driver IC package.

---

## Signal Connections

### Driver A (Motor #1)

| Driver Pin | Nucleo Header | STM32 Pin | Function | Wire Color | Notes |
|-----------|---------------|-----------|----------|------------|-------|
| STEP | D3 | PB3 | Motor step trigger | Green | Rising edge = 1 microstep |
| DIR | D2 | PA10 | Rotation direction | Green | HIGH = CW / LOW = CCW (typical) |
| ENABLE | GND | N/A | Enable driver | Gray | Hardwired to GND = always enabled. **Optional**: connect to D7 for software control |

### Driver B (Motor #2)

| Driver Pin | Nucleo Header | STM32 Pin | Function | Wire Color | Notes |
|-----------|---------------|-----------|----------|------------|-------|
| STEP | D5 | PB4 | Motor step trigger | Green | Rising edge = 1 microstep |
| DIR | D4 | PB5 | Rotation direction | Green | HIGH = CW / LOW = CCW (typical) |
| ENABLE | GND | N/A | Enable driver | Gray | Hardwired to GND = always enabled. **Optional**: connect to D8 for software control |

---

## Driver Configuration Pins

### MS1, MS2, MS3 (Microstepping)

| Configuration | MS1 | MS2 | MS3 | Result | Steps/Rotation |
|---|---|---|---|---|---|
| Full Step (default) | OPEN | OPEN | OPEN | 1 step = 1 full step | 200 steps (NEMA 17) |
| Half Step | VDD | OPEN | OPEN | 1 step = 1/2 full step | 400 steps |
| 1/4 Step | OPEN | VDD | OPEN | 1 step = 1/4 full step | 800 steps |
| 1/8 Step | VDD | VDD | OPEN | 1 step = 1/8 full step | 1600 steps |
| 1/16 Step | OPEN | OPEN | VDD | 1 step = 1/16 full step | 3200 steps |
| 1/32 Step (default) | VDD | VDD | VDD | 1 step = 1/32 full step | 6400 steps |

**Default Setting (1/32 Step)**: Leave all three pins **open** or connect to VDD for 1/32 microstepping.

### SLEEP & RESET Pins

| Pin | Connect To | Effect | Notes |
|-----|-----------|--------|-------|
| SLEEP | RESET (jumper together) | Driver enabled | Use a short jumper wire directly on driver board |
| RESET | SLEEP (jumper together) | Driver enabled | Remove this jumper only to sleep the driver (power saving) |

---

## Motor Connections

### Motor A

- **Coil Phase A**: Driver A OUT1, OUT2
- **Coil Phase B**: Driver A OUT3, OUT4
- **Current**: Check motor datasheet; set Vref accordingly using potentiometer

### Motor B

- **Coil Phase A**: Driver B OUT1, OUT2
- **Coil Phase B**: Driver B OUT3, OUT4
- **Current**: Check motor datasheet; set Vref accordingly using potentiometer

**Motor Wire Color Coding** (typical for NEMA 17):
- **Red**: Coil A (+)
- **Green**: Coil A (−)
- **Yellow**: Coil B (+)
- **Blue**: Coil B (−)

If motor coils are marked A1, A2, B1, B2, map them to OUT pins:
- A1, A2 → OUT1, OUT2
- B1, B2 → OUT3, OUT4

---

## Current Limiting Procedure

**Equipment Needed**:
- Digital multimeter (DC voltage mode)
- Small flathead screwdriver
- Power supply with current monitoring (optional but recommended)

### Steps

1. **Power down** the entire system.
2. **Locate** the small potentiometer (trim pot) on each driver board. It's usually labeled with "ADJ" or a white dot.
3. **Power on** the PSU and Nucleo (but do NOT send step/direction signals).
4. **Set multimeter** to DC Voltage mode.
5. **Probe** the potentiometer:
   - Positive lead: Metal screw of potentiometer
   - Negative lead: GND pin on driver
6. **Measure** the voltage displayed.
7. **Calculate** target Vref:
   - `Vref = Desired_Current ÷ 2`
   - Example: For 1A limit on a NEMA 17 running 1.5A max, set Vref = 0.75V
8. **Adjust** the potentiometer screw clockwise (increase Vref) or counterclockwise (decrease Vref) until the multimeter reads the target voltage.
9. **Repeat** for Driver B with the same target current.
10. **Power down** and connect your motors.

### Current Limiting Formula

```
Vref (Volts) × 2 = RMS Current Limit (Amps)

Examples:
Vref = 0.5V  →  Current Limit = 1.0A
Vref = 0.75V →  Current Limit = 1.5A
Vref = 1.0V  →  Current Limit = 2.0A
```

---

## Wiring Checklist

- [ ] PSU +12V → VMOT (both drivers)
- [ ] PSU GND → Driver GND (Power) (both drivers)
- [ ] PSU GND → STM32 GND (common ground)
- [ ] Nucleo 3.3V → VDD (both drivers)
- [ ] Nucleo GND → Driver GND (Logic) (both drivers)
- [ ] D3 (PB3) → Driver A STEP
- [ ] D2 (PA10) → Driver A DIR
- [ ] D5 (PB4) → Driver B STEP
- [ ] D4 (PB5) → Driver B DIR
- [ ] **100µF capacitor across VMOT↔GND on Driver A** (within 2cm of IC)
- [ ] **100µF capacitor across VMOT↔GND on Driver B** (within 2cm of IC)
- [ ] SLEEP + RESET jumpered together (both drivers)
- [ ] Motor coil phases connected to OUT pins (A1/A2 → OUT1/OUT2, B1/B2 → OUT3/OUT4)
- [ ] Vref adjusted for both drivers using multimeter
- [ ] Test step signals with multimeter before connecting power to motors

---

## Software Setup (STM32CubeIDE / HAL)

### GPIO Configuration

```c
// Driver A
GPIO_InitStruct.Pin = GPIO_PIN_3 | GPIO_PIN_10;  // PB3 (STEP), PA10 (DIR)
GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
GPIO_InitStruct.Speed = GPIO_SPEED_HIGH;
HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);  // PB3
HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);  // PA10

// Driver B
GPIO_InitStruct.Pin = GPIO_PIN_4 | GPIO_PIN_5;  // PB4 (STEP), PB5 (DIR)
GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
GPIO_InitStruct.Speed = GPIO_SPEED_HIGH;
HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
```

### Minimal Step Function

```c
void stepper_step(uint8_t motor, uint8_t direction) {
    if (motor == 0) {  // Motor A
        HAL_GPIO_WritePin(GPIOA, GPIO_PIN_10, direction ? GPIO_PIN_SET : GPIO_PIN_RESET);  // DIR
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_3, GPIO_PIN_SET);    // STEP high
        HAL_Delay(1);  // Pulse width
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_3, GPIO_PIN_RESET);  // STEP low
    } else {  // Motor B
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_5, direction ? GPIO_PIN_SET : GPIO_PIN_RESET);  // DIR
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_4, GPIO_PIN_SET);    // STEP high
        HAL_Delay(1);
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_4, GPIO_PIN_RESET);  // STEP low
    }
}
```

---

## Troubleshooting

| Symptom | Cause | Solution |
|---------|-------|----------|
| Driver gets hot / burns out | No capacitor; motor disconnected while powered | Add 100µF caps; always power down before rewiring |
| Motor won't move | Vref set too low; missing DIR/STEP signal | Increase Vref; verify signal with oscilloscope |
| Motor stutters / loses steps | Vref too high or too low; noisy signals | Adjust Vref; shield signal wires; reduce step frequency |
| Nucleo resets intermittently | Power surge from motor | Add larger capacitors (220µF+); check PSU current rating |
| DIR/STEP signals not reaching driver | GND not common; floating signal | Verify PSU GND → STM32 GND connection; use short, twisted signal wires |

---

## Testing Procedure

1. **Visual Inspection**: Check all capacitor placements, wire colors, and pin assignments against the wiring diagram.
2. **Multimeter GND Check**: Verify continuity (0Ω) from PSU GND → Driver GND → STM32 GND.
3. **Voltage Check**: Measure 3.3V on VDD pins; 12–36V on VMOT pins.
4. **Signal Test**: Send a 100 Hz square wave to STEP pin; measure with oscilloscope (2–5V peak).
5. **Motor Test**: Manually apply LOW/HIGH to DIR pin; send STEP pulses and verify motor rotates smoothly.
6. **Load Test**: Apply mechanical load to motor; monitor Vref potentiometer temperature (should be warm, not hot).

---

## References

- **DRV8825 Datasheet**: [TI DRV8825 PDF](https://www.ti.com/lit/ds/symlink/drv8825.pdf)
- **A4988 Datasheet**: [Allegro A4988 PDF](https://www.allegromicro.com/en/products/motor-drivers/stepper-motor-driver-ics/a4988)
- **STM32 Nucleo F103RB Datasheet**: [STM32F103RB Reference Manual](https://www.st.com/resource/en/reference_manual/cd00171190-stm32f103xx-reference-manual.pdf)

---

**Last Updated**: November 23, 2025  
**Diagram Version**: 1.0
