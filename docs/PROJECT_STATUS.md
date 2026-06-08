# Project Status — WaveCam (updated 2026-06-06)

## Project Goal

**WaveCam** is a personal "robot cameraman" — a vision-based auto-filming **PTZ camera** that films Zack foil-surfing **50-300m offshore** (a SoloShot replacement). The Jetson Orin Nano runs YOLOv8 person detection plus a bright-**orange-rashguard color cue**; a **Prisual NDI PTZ camera** does pan/tilt/zoom; a native **iOS app** is the operator console. LoRa GPS will coarse-point/zoom at distance while vision refines.

> Supersedes the old "jetsonTracker" design (DIY 2xNEMA17 stepper gimbal, Apple-Watch/BN-220 GPS, DroidCam). Canonical architecture: `.claude` memory `wavecam-architecture-pivot`.

Two agents build it: **Codex** = Orin backend + deploy; **Claude** = iOS app + device installs.

## Current State

### Done / Live
- [x] **Backend** (`orin/wavecam/`): FastAPI control API `/api/v1` (status, safety, ptz, media, config, telemetry, agent, system) on `:8088`
- [x] PTZ control over **RAW VISCA / UDP** to the Prisual camera; RTSP video; MJPEG operator feed
- [x] **Live tuning** via `config/hot` (color preset, YOLO class, aim-Y, confidence, motion gains) — no restart
- [x] **Recording** (`media/record/start|stop`) wired end-to-end (backend + iOS)
- [x] **Cinematic Zoom** (vision auto-zoom-to-frame, **default off**, gated by `ptz.cinematic_zoom_enabled`) — shipped + deployed
- [x] **Supervisor** layer + systemd `wavecam.service` (watchdog, auto-resume on restart); `system/restart` endpoint
- [x] Optional bearer auth + role gate (operator/viewer/supervisor/agent), **default-off** so it can't break the live app
- [x] **iOS app** (`ios/WaveCam/`): 5 tabs (Live / PTZ / Calibrate / Tools[Tune+Agent+Web] / Connect), Emergency Stop, Keychain token, feature-detection against `GET /config` — built + installed on Zack's iPhone
- [x] Live model on the Orin = **`yolov8n.engine`** (TensorRT)

### In Progress
- [ ] **LoRa GPS phase** — hardware **on the bench (2026-06-06)**: 2× Wio Tracker L1 Lite (remote = GPS + IMU on the subject; base = GPS on the Orin via USB-A serial, its fix = camera position). Decisions locked; next = Meshtastic ingest. Spec: `docs/superpowers/specs/2026-06-05-gps-lora-cueing-design.md`.
- [ ] Field power buildout using fused 12V camera and 18V Orin buck-converter branches.

### Pending
- [ ] **On-device test of Cinematic Zoom** on the rig (Zack)
- [ ] **LoRa GPS ingest**: Meshtastic serial reader on the base Wio → `NormalizedFix` → existing `gps_fusion` pointing; pan-home heading; base-Wio GPS = camera position; remote IMU enhances target prediction
- [ ] **YOLO26 TensorRT engine** export (maintenance window) — code default `yolo26n.pt` is not built/loaded; live = `yolov8n.engine`
- [ ] Deferred iOS polish and on-device accessibility validation

## Live System Map

- **`:8088`** — WaveCam control API (`/api/v1`) + live web control page. The **active tracker** the iOS app drives.
- **`:8080`** — retired legacy Dash service. It should stay stopped/disabled.

## Connection Info

### Orin
- SSH: `ssh orin` (alias) or `ssh zack@192.168.1.155`
- Wired LAN: Orin `192.168.100.10`, camera `192.168.100.88`
- Field uplink: iPhone USB tether on the Orin **USB-A host port** → `172.20.10.8/28` (Wi-Fi hotspot = fallback)
- **Credentials are NOT stored in this repo** — keep the sudo/login password in your password manager only.

### Camera (Prisual NDI PTZ)
- Control: RAW VISCA over UDP `192.168.100.88:1259` (no auth; NOT Sony 8-byte framed)
- Video: RTSP `rtsp://192.168.100.88/1` (1080p60), `/2` (640x360); ONVIF `:81` backup

### Control API
- `http://<orin>:8088/api/v1` — status / safety / ptz / media / config / telemetry / agent / system

### Legacy gimbal and GPS relay
- Archived under `archive/legacy-20260606/`
- Not part of the active WaveCam runtime

## Architecture Flow (current)

```
Prisual PTZ (VISCA/UDP) ──► orin/wavecam: vision_tracker (YOLOv8n + orange cue)
                                   │
LoRa GPS (Wio Tracker, future) ──► fusion ──► controller.compute_pan/tilt/zoom
                                   │
iOS WaveCam app (operator) ──────► control API :8088 ──► PTZ (pan/tilt/zoom)
                                   │
                              MJPEG feed + recording
```

## Next Steps

1. **Zack:** on-device Cinematic Zoom test (Tune → CINEMATIC ZOOM → on → set Subject size)
2. **LoRa GPS phase:** hardware on bench — build the Meshtastic serial ingest (base Wio `/dev/ttyACM*` → `NormalizedFix`); map GPS bearing → pan via pan-home; base-Wio fix = camera position
3. **YOLO26 engine:** export `yolo26n.engine` (TensorRT) in a maintenance window if upgrading the detector
4. **iOS:** finish landscape parity + deferred polish

## Collaboration

Claude + Codex coordinate over `.agent-collab/` (event bus, claims/leases, audit log). Claim before editing shared files; stage commits explicitly; democracy + pre-commit review.
