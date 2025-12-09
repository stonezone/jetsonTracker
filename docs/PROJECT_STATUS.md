# Project Status - December 7, 2025

## Project Goal

Personal "robot cameraman" to film watersports. Replacement for SoloShot camera that didn't work as advertised. Uses Jetson Orin Nano for YOLOv8 vision tracking, GPS from Apple Watch for target triangulation, and STM32 Nucleo for stepper motor control.

**Gimbal Hardware:** [Thingiverse thing:4547074](https://www.thingiverse.com/thing:4547074)

## Current State

### Completed
- [x] Project reorganized: `orin/`, `nucleo/`, `config/`, `docs/`, `hardware/`
- [x] ARCHITECTURE.md written with full system documentation
- [x] Cloudflare tunnel configured and running on Orin
- [x] Nucleo serial communication tested and working
- [x] Camera working via DroidCam USB (adb forward method)
- [x] YOLO detection working at 42+ FPS with TensorRT
- [x] PyTorch/torchvision installed on Orin
- [x] Claude OS integration with 4 knowledge bases
- [x] Limit switches physically installed (4x reed switches at ±90° for both axes)
- [x] GPS architecture decision: Remove iPhone, use GPS module + motor heading

### In Progress
- [ ] Wire limit switches to STM32 Nucleo (see `docs/wiring/LIMIT_SWITCH_WIRING_GUIDE.md`)
- [ ] Add Phase 1 logging to gps_server.py (latency histogram, path indicator)

### Pending (Phase 1 - Test with iPhone)
- [ ] Test limit switch detection and homing
- [ ] Build and deploy iOS/Watch apps
- [ ] Test Watch → iPhone → Cloudflare → Orin GPS pipeline

### Pending (Phase 2-4 - GPS Module + Remove iPhone)
- [ ] Order BN-220 GPS module (~$15, multi-constellation)
- [ ] Install GPS module on Orin (UART or USB)
- [ ] Implement motor position as heading (pan=0 after HOME = "forward")
- [ ] Remove iPhone from architecture (Watch LTE direct only)
- [ ] Test full GPS-Vision fusion pipeline

### Future
- [ ] Tune Kalman filter for GPS/vision fusion
- [ ] Implement lost target behavior (5 min timeout, return home, keep looking)
- [ ] Mobile power setup (6S LiPo with buck converters)

## Connection Info

### Orin SSH
```
ssh orin
# or: ssh zack@192.168.1.155
# sudo password: motherfucker
```

### Cloudflare Tunnel
- Tunnel ID: `3ea6c1a2-5b5a-4d91-b0df-5e458b0fbbf5`
- Public endpoint: `wss://ws.stonezone.net`
- Routes to: `localhost:8765`
- Config: `/etc/cloudflared/config.yml`

### Camera (DroidCam)
- Android IP: `192.168.1.33`
- Port: `4747`
- URL: `http://192.168.1.33:4747/video`

### Nucleo Serial
- Port: `/dev/ttyACM0`
- Baud: `115200`
- Commands: PING, PAN_REL, TILT_REL, HOME_ALL, CENTER, GET_POS, GET_STATUS

## File Locations

### Local (Mac - jetsonTracker/)
- `orin/` - All Orin Python code (source of truth)
- `nucleo/firmware/stepper_control/Sources/main.c` - Nucleo firmware
- `config/cloudflare/config.yml` - Tunnel config copy
- `ARCHITECTURE.md` - Full system documentation

### Orin (/data/projects/gimbal/)
- `vision_tracker.py` - YOLO tracking
- `gimbal_controller.py` - Serial to Nucleo
- `gps_fusion/` - GPS processing modules
- `gps_server.py` - DEPLOY THIS (WebSocket server for Cloudflare)
- `models/yolov8n.pt` - YOLO model

## Architecture Flow

### Current (Phase 1 - Testing with iPhone)
```
Watch GPS → iPhone → wss://ws.stonezone.net → Cloudflare → Orin:8765 (gps_server.py)
                                                              ↓
Camera (DroidCam) → vision_tracker.py → fusion_engine.py → gimbal_controller.py
                                                              ↓
                                              Nucleo (/dev/ttyACM0) → Steppers
```

### Target (Phase 4 - No iPhone)
```
Watch GPS (LTE direct) → wss://ws.stonezone.net → Cloudflare → Orin:8765
                                                                  ↓
BN-220 GPS Module ──────────────────────────────────► fusion_engine.py
Motor Position (heading) ───────────────────────────►      ↓
Camera (DroidCam) → vision_tracker.py ──────────────► gimbal_controller.py
                                                           ↓
                                             Nucleo (/dev/ttyACM0) → Steppers
```

**Key Decision**: Motor position after HOME_ALL = heading. No magnetometer needed.

## Next Steps

1. **Wire limit switches** - Connect 4 reed switches to Nucleo (D6, D7, D11, D12)
2. **Add Phase 1 logging** - Latency histogram, path indicator (BT vs LTE)
3. **Test iOS/Watch app** - Verify tunnel at `wss://ws.stonezone.net` works
4. **Order BN-220 GPS** - ~$15, multi-constellation for better accuracy

## Limit Switch Wiring Quick Reference

| Switch | Position | Pin | Wire To |
|--------|----------|-----|---------|
| PAN_NEG | -90° | D6 (PB10) | Reed → D6, Reed → GND |
| TILT_NEG | -90° | D7 (PA8) | Reed → D7, Reed → GND |
| PAN_POS | +90° | D11 (PA7) | Reed → D11, Reed → GND |
| TILT_POS | +90° | D12 (PA6) | Reed → D12, Reed → GND |

See `docs/wiring/LIMIT_SWITCH_WIRING_GUIDE.md` for full instructions.
