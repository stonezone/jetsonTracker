# End-to-End Testing Procedure

This document provides a step-by-step procedure to verify the complete JetsonTracker system from GPS input to gimbal movement.

## Prerequisites

### Hardware Checklist
- [ ] Jetson Orin Nano powered and on network (192.168.1.155)
- [ ] Nucleo-F401RE connected via USB to Orin (/dev/ttyACM0)
- [ ] DRV8825 drivers powered (18V supply)
- [ ] Vref verified: Pan 0.60V, Tilt 0.50V
- [ ] Motors connected (Pan: NEMA17, Tilt: Moons MS17HD5P4100)
- [ ] Android phone with DroidCam on network (192.168.1.33:4747)
- [ ] Apple Watch + iPhone for GPS testing

### Software Checklist
- [ ] Cloudflare tunnel running (`cloudflared tunnel run jetson-tracker`)
- [ ] iOS/Watch apps built and deployed
- [ ] Python dependencies installed on Orin

## Phase 1: Hardware Verification

### 1.1 Verify Nucleo Connection

```bash
ssh orin
ls -la /dev/ttyACM0
# Expected: crw-rw---- 1 root dialout ... /dev/ttyACM0
```

### 1.2 Test Serial Communication

```bash
cd /data/projects/gimbal
python3 -c "
from gimbal_control.gimbal_controller import GimbalController
g = GimbalController()
result = g.ping()
print(f'PING result: {result}')
"
# Expected: PING result: PONG
```

### 1.3 Test Motor Movement

```bash
python3 -c "
from gimbal_control.gimbal_controller import GimbalController
g = GimbalController()

# Small test movements (50 steps = ~11 degrees)
g.send_command('PAN_REL:+50')
import time; time.sleep(1)
g.send_command('PAN_REL:-50')
print('Pan movement test complete')

g.send_command('TILT_REL:+50')
time.sleep(1)
g.send_command('TILT_REL:-50')
print('Tilt movement test complete')
"
```

### 1.4 Test Limit Switches

```bash
python3 -c "
from gimbal_control.gimbal_controller import GimbalController
g = GimbalController()
status = g.send_command('GET_STATUS')
print(f'Status: {status}')
"
# Expected: STATUS PAN_SW:0 TILT_SW:0 PAN_OK:1 TILT_OK:1
```

### 1.5 Home Gimbal

```bash
python3 -c "
from gimbal_control.gimbal_controller import GimbalController
g = GimbalController()
result = g.send_command('HOME_ALL')
print(f'Home result: {result}')
"
# Expected: ALL HOMED
```

## Phase 2: Camera Verification

### 2.1 Check DroidCam Accessibility

```bash
# From Orin
curl -s -o /dev/null -w '%{http_code}' http://192.168.1.33:4747/video
# Expected: 200
```

### 2.2 Test Camera Capture

```bash
python3 scripts/check_camera.py
# Should show available cameras
```

### 2.3 Test YOLO Detection

```bash
python3 scripts/test_detection.py
# Expected: Detections on test image
```

## Phase 3: GPS/WebSocket Verification

### 3.1 Verify Cloudflare Tunnel

```bash
# On Orin, check tunnel status
systemctl status cloudflared
# or
ps aux | grep cloudflared
```

### 3.2 Start GPS Server

```bash
cd /data/projects/gimbal
python3 gps_server.py
# Expected: "Starting GPS Server on 0.0.0.0:8765"
```

### 3.3 Test WebSocket Connection (from another terminal)

```bash
# Install wscat if needed: npm install -g wscat
wscat -c ws://localhost:8765

# Send test heartbeat
{"type": "ping", "id": "test-123"}
# Expected response: {"type": "pong", "id": "test-123"}
```

### 3.4 Test with iPhone App

1. Open iPhone tracker app
2. Ensure WebSocket URL is set to `wss://ws.stonezone.net`
3. Start location streaming
4. Verify GPS server logs show incoming fixes

## Phase 4: Integration Testing

### 4.1 Vision-Only Tracking Test

```bash
cd /data/projects/gimbal
python3 vision/vision_tracker.py --mode vision-only

# Stand in front of camera, verify:
# - Person detection boxes appear
# - Gimbal moves to track detected person
```

### 4.2 GPS-Only Tracking Test

```bash
# Terminal 1: Start GPS server
python3 gps_server.py

# Terminal 2: Start GPS tracking
python3 -c "
from gps_fusion import FusionEngine, GeoPoint
import time

fusion = FusionEngine()

# Set gimbal position (replace with actual coordinates)
gimbal = GeoPoint(lat=37.7749, lon=-122.4194, alt=10, heading=0, timestamp=time.time())

# Simulate target 10m north
target = GeoPoint(lat=37.7750, lon=-122.4194, alt=10, timestamp=time.time())

fusion.update_gps(gimbal, target)
result = fusion.compute()
print(f'Mode: {result.mode}')
print(f'Pan offset: {result.pan_offset}')
print(f'Tilt offset: {result.tilt_offset}')
"
```

### 4.3 Full Fusion Test

```bash
# Start full system
python3 vision/vision_tracker.py --mode fusion

# Walk around with Apple Watch
# Verify gimbal tracks subject using both GPS and vision
```

## Phase 5: Stress Testing

### 5.1 Sustained Tracking (5 minutes)

```bash
# Run tracker for extended period
timeout 300 python3 vision/vision_tracker.py --mode fusion

# Monitor:
# - Memory usage: watch -n 5 free -h
# - CPU usage: htop
# - GPS fix rate in logs
```

### 5.2 Edge Cases

| Test Case | How to Test | Expected Behavior |
|-----------|-------------|-------------------|
| Subject leaves frame | Walk out of camera view | Switch to GPS-only mode |
| GPS signal lost | Turn off iPhone app | Switch to vision-only mode |
| Both lost | Leave frame + stop GPS | Hold last position, recover when data returns |
| Rapid movement | Run quickly | Smooth tracking with prediction |
| Limit switch trigger | Move gimbal to physical limit | Stop movement, log warning |

## Troubleshooting

### No Serial Response
```bash
# Check permissions
sudo usermod -a -G dialout $USER
# May need to logout/login

# Check device exists
ls -la /dev/ttyACM*
```

### Camera Not Opening
```bash
# Check DroidCam is running on phone
# Verify phone and Orin on same network
ping 192.168.1.33
```

### WebSocket Disconnects
- Check Cloudflare tunnel is running
- Verify heartbeat handler in gps_server.py ({"type": "ping"} → {"type": "pong"})
- Check iPhone app WebSocket URL setting

### Detection Too Slow
- Verify using TensorRT engine (not .pt file)
- Check GPU is being used: `tegrastats`
- Model path: /data/projects/gimbal/models/yolov8n.engine

## Metrics to Record

| Metric | Target | How to Measure |
|--------|--------|----------------|
| GPS latency | <200ms | Check fix.age_ms() in logs |
| Detection FPS | >15 FPS | vision_tracker.py log output |
| Tracking latency | <100ms | Time from detection to motor command |
| Position accuracy | ±5° | Compare gimbal angle to actual subject position |

## Sign-off

| Phase | Tested By | Date | Pass/Fail | Notes |
|-------|-----------|------|-----------|-------|
| Phase 1: Hardware | | | | |
| Phase 2: Camera | | | | |
| Phase 3: GPS/WS | | | | |
| Phase 4: Integration | | | | |
| Phase 5: Stress | | | | |

---
**Created:** November 29, 2025
**Last Updated:** November 29, 2025
