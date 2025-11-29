# Project Status - November 29, 2025

## Current State

### Completed
- [x] Project reorganized: `orin/`, `nucleo/`, `config/`, `docs/`, `hardware/`
- [x] ARCHITECTURE.md written with full system documentation
- [x] Cloudflare tunnel configured and running on Orin
- [x] Nucleo serial communication tested and working
- [x] Camera working via DroidCam IP
- [x] YOLO detection working (CPU mode)

### In Progress
- [ ] Deploy gps_server.py to Orin at /data/projects/gimbal/

### Pending
- [ ] Fix TensorRT/PyTorch CUDA issue for GPU acceleration
- [ ] Build and deploy iOS/Watch apps
- [ ] Test full GPS-Vision fusion pipeline
- [ ] Install reed switch limit switches on gimbal

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
```
Watch GPS → iPhone → wss://ws.stonezone.net → Cloudflare → Orin:8765 (gps_server.py)
                                                              ↓
Camera (DroidCam) → vision_tracker.py → fusion_engine.py → gimbal_controller.py
                                                              ↓
                                              Nucleo (/dev/ttyACM0) → Steppers
```

## Next Steps
1. Deploy gps_server.py to Orin
2. Build iOS/Watch apps with ws.stonezone.net endpoint
3. Test full pipeline
