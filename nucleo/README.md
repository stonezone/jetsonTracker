# Nucleo Stepper Control

Bare-metal STM32 firmware for dual DRV8825 stepper motor control.

## Hardware

| Component | Model |
|-----------|-------|
| MCU Board | STM32 Nucleo-F401RE |
| Motor Drivers | 2x Pololu DRV8825 |
| PAN Motor | Tevo NEMA17 (Vref 0.60V) |
| TILT Motor | Moons MS17HD5P4100 (Vref 0.50V) |
| Motor Power | 18V laptop brick |

## Firmware

Located in `firmware/stepper_control/Sources/main.c`

### Building

1. Open `firmware/stepper_control/` in STM32CubeIDE
2. Build (Ctrl+B)
3. Connect Nucleo via USB
4. Flash (Run → Debug)

### Serial Protocol

| Parameter | Value |
|-----------|-------|
| Baud | 115200 |
| Config | 8N1 |
| Line ending | LF (\n) |

### Commands

| Command | Response | Description |
|---------|----------|-------------|
| `PING` | `PONG` | Connection test |
| `PAN_REL:<steps>` | `OK PAN:<actual>` | Relative pan |
| `TILT_REL:<steps>` | `OK TILT:<actual>` | Relative tilt |
| `HOME_ALL` | `ALL HOMED` | Home both axes |
| `CENTER` | `CENTERED` | Move to (0,0) |
| `GET_POS` | `POS PAN:x TILT:y` | Current position |
| `GET_STATUS` | `STATUS PN:x PP:x...` | Limit switch status |

## Pin Mapping

See `docs/hardware-contract.md` for complete wiring.

### Quick Reference

| Function | Arduino Pin | STM32 |
|----------|-------------|-------|
| PAN STEP | D3 | PB3 |
| PAN DIR | D2 | PA10 |
| TILT STEP | D5 | PB4 |
| TILT DIR | D4 | PB5 |
| PAN NEG LIMIT | D6 | PB10 |
| TILT NEG LIMIT | D7 | PA8 |
| PAN POS LIMIT | D11 | PA7 |
| TILT POS LIMIT | D12 | PA6 |

## Limit Switches

Reed switches with magnets, active-low with internal pull-ups.
Not yet physically installed - firmware ready.
