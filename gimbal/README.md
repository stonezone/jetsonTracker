# Gimbal Hardware

This folder will contain mechanical design files for the 2-axis pan/tilt gimbal assembly.

## Planned Contents

- **STL Files:** 3D printable parts for gimbal frame
- **Assembly Instructions:** Step-by-step build guide
- **CAD Files:** Source files (if available)
- **Photos:** Assembly progress and final build

## Current Status

The gimbal is currently assembled and operational with:
- 3D printed frame with herringbone gears
- 2x NEMA17 stepper motors (pan + tilt)
- Reed switch limit switch mounting points
- Camera mounting plate (for phone/SLR)

## Design Notes

- **Gear Ratio:** Herringbone gears provide smooth, backlash-free motion
- **Range of Motion:** (calibrated December 2025)
  - Pan: ~±70° from center (~4200 steps total travel)
  - Tilt: ~±90° down to up (~2600 steps total travel)
- **Motor Mounting:** NEMA17 standard mounting holes
- **Material:** PLA/PETG 3D printed parts
- **Limit Switches:** 4x reed switches (2 per axis, at both ends of travel)
  - Pan: D11=RIGHT (home), D6=LEFT (far limit)
  - Tilt: D7=DOWN (home), D12=UP 90° (far limit)

## To Do

- [ ] Export STL files from original CAD design
- [ ] Document assembly process with photos
- [ ] Create wiring diagram for limit switch placement
- [ ] Design phone mount adapter
- [ ] Design SLR camera mount (future)

---

**Note:** This folder is a placeholder. STL files and detailed mechanical documentation will be added as they become available.

**Last Updated:** December 10, 2025
