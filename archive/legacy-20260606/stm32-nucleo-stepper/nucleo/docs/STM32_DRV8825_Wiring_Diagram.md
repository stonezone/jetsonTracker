# STM32 Nucleo + DRV8825 Stepper Driver Wiring Diagram

## Hardware Components
- **MCU**: STM32 Nucleo-64 (MB1136 Rev C)
- **Drivers**: 2x DRV8825 Stepper Motor Driver Breakout Boards
- **Motors**: 2x Bipolar Stepper Motors (not shown)

## Power Architecture

```
MOTOR POWER SUPPLY (8-35V)
│
├─── VMOT (Driver 1)
├─── VMOT (Driver 2)
│
└─── GND ────┐
             │
NUCLEO BOARD │
│            │
├─── GND ────┴─── Common Ground (CRITICAL!)
├─── 5V (optional for driver logic)
└─── 3.3V
```

## Wiring Table

### Driver 1 (Motor A)

| DRV8825 Pin | STM32 Nucleo Pin | Function | Notes |
|-------------|------------------|----------|-------|
| VMOT | Motor PSU (+) | Motor Power | 8-35V DC |
| GND (Power) | Motor PSU (-) & Nucleo GND | Common Ground | **MUST be common** |
| GND (Logic) | Nucleo GND | Logic Ground | Same as power GND |
| STEP | D2 (PA10) | Step Pulse | One pulse = one step |
| DIR | D3 (PB3) | Direction | HIGH/LOW for CW/CCW |
| EN | D4 (PB5) | Enable | LOW=enabled, HIGH=disabled |
| RST | 5V or D5 (PB4) | Reset | Tie HIGH or GPIO control |
| SLP | 5V or D6 (PB10) | Sleep | Tie HIGH or GPIO control |
| M0 | GND or 5V | Microstep 1 | See microstepping table |
| M1 | GND or 5V | Microstep 2 | See microstepping table |
| M2 | GND or 5V | Microstep 3 | See microstepping table |
| A1, A2 | Motor Coil A | Motor Phase A | Connect to motor |
| B1, B2 | Motor Coil B | Motor Phase B | Connect to motor |

### Driver 2 (Motor B)

| DRV8825 Pin | STM32 Nucleo Pin | Function | Notes |
|-------------|------------------|----------|-------|
| VMOT | Motor PSU (+) | Motor Power | Shared with Driver 1 |
| GND (Power) | Motor PSU (-) & Nucleo GND | Common Ground | **MUST be common** |
| GND (Logic) | Nucleo GND | Logic Ground | Same as power GND |
| STEP | D7 (PA8) | Step Pulse | One pulse = one step |
| DIR | D8 (PA9) | Direction | HIGH/LOW for CW/CCW |
| EN | D9 (PC7) | Enable | LOW=enabled, HIGH=disabled |
| RST | 5V or D10 (PB6) | Reset | Tie HIGH or GPIO control |
| SLP | 5V or D11 (PA7) | Sleep | Tie HIGH or GPIO control |
| M0 | GND or 5V | Microstep 1 | See microstepping table |
| M1 | GND or 5V | Microstep 2 | See microstepping table |
| M2 | GND or 5V | Microstep 3 | See microstepping table |
| A1, A2 | Motor Coil A | Motor Phase A | Connect to motor |
| B1, B2 | Motor Coil B | Motor Phase B | Connect to motor |

## Microstepping Configuration

| M0 | M1 | M2 | Microstep Resolution |
|----|----|----|---------------------|
| L  | L  | L  | Full step |
| H  | L  | L  | 1/2 step |
| L  | H  | L  | 1/4 step |
| H  | H  | L  | 1/8 step |
| L  | L  | H  | 1/16 step |
| H  | L  | H  | 1/32 step |
| L  | H  | H  | 1/32 step |
| H  | H  | H  | 1/32 step |

**Recommended for most applications**: 1/16 step (M0=L, M1=L, M2=H)

## Visual Wiring Diagram

```
                    MOTOR POWER SUPPLY
                    (8-35V recommended)
                           │
                    ┌──────┴──────┐
                    │             │
                 VMOT          VMOT
                    │             │
        ┌───────────┴─────┐   ┌───┴─────────────┐
        │   DRV8825 #1    │   │   DRV8825 #2    │
        │   (Driver A)    │   │   (Driver B)    │
        ├─────────────────┤   ├─────────────────┤
        │ STEP ───────────┼───┼─────────── STEP │
        │ DIR  ───────────┼───┼─────────── DIR  │
        │ EN   ───────────┼───┼─────────── EN   │
        │ RST  ───────────┼───┼─────────── RST  │
        │ SLP  ───────────┼───┼─────────── SLP  │
        │ GND  ───────────┼───┼─────────── GND  │
        └─────────────────┘   └─────────────────┘
                 │                     │
                 └──────────┬──────────┘
                            │
                    ┌───────┴────────┐
                    │  STM32 NUCLEO  │
                    │   (MB1136)     │
                    ├────────────────┤
                    │ D2  (PA10)  ───┼─── STEP1
                    │ D3  (PB3)   ───┼─── DIR1
                    │ D4  (PB5)   ───┼─── EN1
                    │ D5  (PB4)   ───┼─── RST1 (optional)
                    │ D6  (PB10)  ───┼─── SLP1 (optional)
                    │                │
                    │ D7  (PA8)   ───┼─── STEP2
                    │ D8  (PA9)   ───┼─── DIR2
                    │ D9  (PC7)   ───┼─── EN2
                    │ D10 (PB6)   ───┼─── RST2 (optional)
                    │ D11 (PA7)   ───┼─── SLP2 (optional)
                    │                │
                    │ GND ───────────┼─── Common Ground
                    │ 5V (optional)  │    (for RST/SLP)
                    └────────────────┘

    MOTOR A                             MOTOR B
    ┌─────────┐                         ┌─────────┐
    │  Coil A │◄────A1/A2──────────────►│  Coil A │
    │  Coil B │◄────B1/B2──────────────►│  Coil B │
    └─────────┘                         └─────────┘
```

## Critical Safety & Design Notes

### 1. **GROUND IMPERATIVE**
```
⚠️  CRITICAL: Motor power supply GND MUST connect to STM32 GND
    Floating grounds = erratic behavior, damage, or fire hazard
```

### 2. **Current Limiting**
- Set current limit via potentiometer on each DRV8825
- Formula: `Vref = Current_Limit × 2 × Rsense`
- Typical Rsense = 0.1Ω for DRV8825
- Example: For 1A motor → Vref = 1A × 2 × 0.1Ω = 0.2V
- Measure Vref between potentiometer wiper and GND
- Adjust CCW to increase current, CW to decrease

### 3. **Power Supply Sizing**
```
Minimum PSU Current = (Motors × Peak_Current) × 1.5
Example: 2 motors @ 1.5A each → 2 × 1.5A × 1.5 = 4.5A minimum
```

### 4. **Enable Pin Behavior**
- `EN = LOW`: Driver active (default)
- `EN = HIGH`: Driver disabled (coils unpowered)
- Use `EN` to save power when motors idle

### 5. **Reset & Sleep Pins**
**Option A (Simple)**: Tie both RST and SLP to 5V via 10kΩ resistor
**Option B (Power Saving)**: Control via GPIO for sleep mode

### 6. **Decoupling Capacitors**
⚠️  **REQUIRED**: 100µF electrolytic capacitor across VMOT and GND on EACH driver
- Protects against voltage spikes
- Place physically close to driver board
- Consider adding 0.1µF ceramic cap in parallel

### 7. **Step Signal Timing**
- Minimum pulse width: 1.9µs
- Recommended: 5µs HIGH, 5µs LOW
- STM32 Timer/PWM recommended for smooth motion

## Example STM32 Pin Configuration (STM32CubeIDE)

```c
// GPIO Configuration
// Driver 1
GPIO_InitStruct.Pin = GPIO_PIN_10;  // PA10 - STEP1
GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
GPIO_InitStruct.Pull = GPIO_NOPULL;
GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

GPIO_InitStruct.Pin = GPIO_PIN_3;   // PB3 - DIR1
HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

GPIO_InitStruct.Pin = GPIO_PIN_5;   // PB5 - EN1
HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

// Driver 2
GPIO_InitStruct.Pin = GPIO_PIN_8;   // PA8 - STEP2
HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

GPIO_InitStruct.Pin = GPIO_PIN_9;   // PA9 - DIR2
HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

GPIO_InitStruct.Pin = GPIO_PIN_7;   // PC7 - EN2
HAL_GPIO_Init(GPIOC, &GPIO_InitStruct);

// Initialize - Enable drivers
HAL_GPIO_WritePin(GPIOB, GPIO_PIN_5, GPIO_PIN_RESET); // EN1 = LOW
HAL_GPIO_WritePin(GPIOC, GPIO_PIN_7, GPIO_PIN_RESET); // EN2 = LOW
```

## Minimal Wiring (Quick Start)

If you want the absolute minimum connections:

**Driver 1:**
- VMOT → Motor PSU (+)
- GND → Motor PSU (-) AND Nucleo GND
- STEP → D2
- DIR → D3
- EN → GND (always enabled)
- RST → 5V (always active)
- SLP → 5V (never sleep)
- M0, M1, M2 → Choose microstepping (recommend all GND for full step testing)

**Driver 2:** Same pattern using D7/D8 instead

## Testing Procedure

1. **Power Off**: Connect all wiring with power disconnected
2. **Inspect**: Verify all connections, especially common ground
3. **Set Current**: Adjust Vref potentiometer BEFORE connecting motors
4. **Test Logic**: Power up STM32 only, verify 3.3V GPIO output
5. **Connect Motors**: Attach stepper motors to A1/A2/B1/B2
6. **Power Motor Supply**: Apply motor voltage, verify no smoke/heat
7. **Test Movement**: Send step pulses, verify motor rotation
8. **Tune Current**: Adjust if motors run too hot or skip steps

## Troubleshooting

| Symptom | Likely Cause | Solution |
|---------|--------------|----------|
| No movement | EN pin not LOW | Pull EN to GND |
| | RST or SLP not HIGH | Tie to 5V |
| | No common ground | Check ground connections |
| Erratic movement | Loose wiring | Secure all connections |
| | Interference | Add decoupling caps |
| Motors too hot | Current too high | Reduce Vref |
| Skipped steps | Current too low | Increase Vref |
| | Speed too high | Reduce step frequency |
| Driver shutdown | Overcurrent | Check motor current rating |
| | Overheat | Add heatsink, improve airflow |

## Bill of Materials (Additional Required)

- [ ] Power supply (12-24V, 3-5A minimum)
- [ ] 2× 100µF electrolytic capacitors (25V+)
- [ ] 2× 0.1µF ceramic capacitors (optional but recommended)
- [ ] Heatsinks for DRV8825 (recommended for currents >1A)
- [ ] 22-24 AWG wire for power connections
- [ ] 26-28 AWG wire for signal connections
- [ ] Multimeter (for setting Vref)

## References
- DRV8825 Datasheet: https://www.ti.com/lit/ds/symlink/drv8825.pdf
- STM32 Nucleo-64 User Manual: UM1724
- Stepper Motor Basics: https://www.ti.com/lit/an/slva488/slva488.pdf

---
**Document Version**: 1.0
**Last Updated**: 2025-11-23
**Board Verified**: STM32 Nucleo MB1136 Rev C
**Driver Verified**: DRV8825 Purple Breakout Board
