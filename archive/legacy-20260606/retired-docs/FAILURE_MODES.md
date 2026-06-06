# Failure Mode Handling

This document describes how the JetsonTracker system handles various failure scenarios and the expected recovery behaviors.

## Tracking Modes

The fusion engine operates in five modes, transitioning automatically based on sensor availability:

| Mode | Description | Confidence |
|------|-------------|------------|
| VISUAL | Target visible in camera frame | High (from YOLO) |
| GPS_ASSISTED | Vision primary, GPS provides hints | High |
| GPS_PRIMARY | Target lost visually, using GPS only | Medium (0.5) |
| SEARCHING | Lost both GPS and vision | Low (0.0) |
| IDLE | No tracking active | N/A |

## Timeout Configuration

| Sensor | Timeout | Configurable In |
|--------|---------|-----------------|
| Visual | 1.0 sec | `FusionEngine.visual_timeout` |
| GPS | 5.0 sec | `FusionEngine.gps_timeout` |
| WebSocket Heartbeat | 15 sec | Swift app hardcoded |
| WebSocket Ping/Pong | 20 sec | `gps_server.py ping_interval` |

## Failure Scenarios

### 1. Target Leaves Camera Frame

**Trigger:** No person detection for > 1 second

**Expected Behavior:**
1. Mode transitions: VISUAL → GPS_PRIMARY (if GPS available) or SEARCHING
2. Gimbal continues tracking using GPS bearing/distance
3. GPS provides predicted position using course/speed
4. System attempts to reacquire visual target

**Recovery:** When person re-enters frame, immediately transitions back to VISUAL or GPS_ASSISTED

**Code Reference:** `orin/gps_fusion/fusion_engine.py:210-231`

---

### 2. GPS Signal Lost (Watch/iPhone)

**Trigger:** No GPS fix received for > 5 seconds

**Expected Behavior:**
1. Mode transitions: GPS_ASSISTED → VISUAL (if visual available) or SEARCHING
2. Last known GPS position may be used for coarse search
3. Visual tracking continues independently if target visible

**Recovery:** GPS automatically resumes when fixes arrive

**Code Reference:** `orin/gps_fusion/fusion_engine.py:143-151`

---

### 3. WebSocket Disconnection

**Trigger:**
- Network failure
- Cloudflare tunnel down
- iPhone app backgrounded/closed
- Heartbeat timeout (no pong for 15 sec)

**Expected Behavior:**
1. gps_server.py logs: "Client disconnected: {address}"
2. GPS updates stop, `_is_gps_fresh()` returns False
3. System falls back to VISUAL mode
4. Periodic log shows: "Clients: 0"

**Recovery:**
- iPhone app auto-reconnects on network restore
- Server accepts new connection, logs: "Client connected"

**Code Reference:** `orin/gps_server.py:166-171`

---

### 4. Both GPS and Vision Lost

**Trigger:** No visual detection + no fresh GPS for respective timeouts

**Expected Behavior:**
1. Mode transitions to SEARCHING
2. Confidence drops to 0.0
3. Kalman filters reset
4. Gimbal holds last known position
5. System awaits new sensor data

**Recovery:** First available sensor triggers mode change

**Code Reference:** `orin/gps_fusion/fusion_engine.py:252-256`

---

### 5. Camera Feed Lost

**Trigger:** DroidCam disconnects, network issue, phone sleeps

**Expected Behavior:**
1. OpenCV `cap.read()` returns `(False, None)`
2. Vision tracker logs error, continues polling
3. System operates in GPS_PRIMARY if available
4. No crash - graceful degradation

**Recovery:** Camera reconnects automatically, frames resume

**Mitigation:**
```python
ret, frame = cap.read()
if not ret:
    logger.warning("Camera frame read failed")
    continue  # Keep trying
```

---

### 6. Nucleo Serial Timeout

**Trigger:** No response to command within 1 second

**Expected Behavior:**
1. GimbalController logs: "Serial timeout"
2. Command is NOT retried automatically
3. Caller receives timeout exception
4. System should continue operating, skip gimbal command

**Recovery:**
- Nucleo typically recovers on next command
- If persistent: Check USB connection, reset Nucleo

**Prevention:**
- `PING` command to verify connectivity before tracking
- `GET_STATUS` to check motor controller health

---

### 7. Limit Switch Triggered

**Trigger:** Gimbal reaches physical limit

**Expected Behavior:**
1. Nucleo immediately stops motor in that direction
2. Returns status with limit flag set: `STATUS PAN_SW:1 TILT_SW:0`
3. Opposite direction movement still allowed
4. No damage to hardware

**Recovery:**
- Send opposite direction command to move away from limit
- Run `HOME_ALL` to reset position

**Code Reference:** `nucleo/firmware/stepper_control/Sources/main.c`

---

### 8. Cloudflare Tunnel Down

**Trigger:** `cloudflared` service stops, network to Cloudflare fails

**Detection:**
```bash
systemctl status cloudflared
# or
curl -s https://ws.stonezone.net
```

**Expected Behavior:**
1. iPhone cannot connect to `wss://ws.stonezone.net`
2. Falls back to local network mode (if iPhone on same WiFi)
3. Orin GPS server continues running, accepts local connections

**Recovery:**
```bash
sudo systemctl restart cloudflared
```

---

### 9. High Latency GPS

**Trigger:** GPS fixes arriving with >500ms delay

**Detection:** Check `fix.age_ms()` in logs

**Expected Behavior:**
1. `FusionEngine.prediction_horizon` compensates with motion prediction
2. GPS-based gimbal angles are "future predicted"
3. Visual tracking takes precedence for fine positioning

**Mitigation:** Already handled by Kalman filter smoothing

---

### 10. YOLO Detection False Positives

**Trigger:** Non-person detected as person

**Expected Behavior:**
1. Gimbal may track wrong object temporarily
2. Low confidence scores (<0.5) should be filtered
3. GPS_ASSISTED mode uses GPS to validate detections

**Mitigation:**
```python
# In vision tracker
MIN_CONFIDENCE = 0.65
if detection.confidence < MIN_CONFIDENCE:
    continue  # Ignore low confidence
```

## State Machine Diagram

```
                    ┌──────────────────┐
                    │      IDLE        │
                    └────────┬─────────┘
                             │ First sensor data
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        ┌──────────┐   ┌──────────┐   ┌──────────┐
        │  VISUAL  │◄──│GPS_ASSIST│──►│GPS_PRIMARY
        └──────────┘   └──────────┘   └──────────┘
              │              │              │
              │   Visual     │   Both       │   GPS
              │   Lost       │   Lost       │   Lost
              ▼              ▼              ▼
        ┌────────────────────────────────────────┐
        │              SEARCHING                 │
        └────────────────────────────────────────┘
                             │
                             │ Any sensor recovers
                             ▼
                    (Back to appropriate mode)
```

## Graceful Degradation Priority

1. **GPS_ASSISTED** - Best accuracy, both sensors available
2. **VISUAL** - Good for close tracking, no GPS
3. **GPS_PRIMARY** - Works at any distance, lower precision
4. **SEARCHING** - Holds position, awaits recovery

## Recommended Timeout Tuning

| Scenario | Visual Timeout | GPS Timeout | Notes |
|----------|---------------|-------------|-------|
| Outdoor filming | 1.0s | 5.0s | Default, balanced |
| Fast action sports | 0.5s | 3.0s | Quick fallback |
| Static/interview | 2.0s | 10.0s | Slower, more stable |
| GPS-only mode | N/A | 10.0s | Forgiving of gaps |

## Logging for Diagnostics

Enable debug logging to diagnose failure modes:

```python
import logging
logging.getLogger('gps_fusion').setLevel(logging.DEBUG)
logging.getLogger('vision_tracker').setLevel(logging.DEBUG)
logging.getLogger('gimbal_controller').setLevel(logging.DEBUG)
```

Key log patterns:
- `"Mode: SEARCHING"` - Both sensors lost
- `"Client disconnected"` - WebSocket dropped
- `"Serial timeout"` - Nucleo not responding
- `"Camera frame read failed"` - Video feed issue

---
**Created:** November 29, 2025
**Last Updated:** November 29, 2025
