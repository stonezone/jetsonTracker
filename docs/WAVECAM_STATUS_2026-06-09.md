# WaveCam — Detailed Status Report


**Date:** 2026-06-14 (originally 2026-06-09; updated to reflect direct-LoRa deployment)
**Live branch/commit:** `fix/b13-calibrate-restore` / `4a25265` (live on Orin)
**Live config:** `config.orin.servo.yaml` + `config.local.yaml` overlay on `/data/projects/gimbal/wavecam/`

---

## 1. Hardware Map

| Component | Device | Connection | Status |
|-----------|--------|------------|--------|
| Camera | Prisual NDI PTZ | LAN `192.168.100.88`, VISCA UDP `:1259` | Operational |
| Compute | Jetson Orin Nano | LAN `192.168.100.10`, SSH `192.168.1.155` | Operational |
| Remote GPS | Wio Tracker L1 Lite (`!9f5802d5`) | LoRa direct to base | Operational; tracker beacon 1–5 Hz depending on firmware config |
| Base GPS | Wio Tracker L1 Lite (`!38c3f1fd`) | Orin USB-A `/dev/ttyACM0` | Operational; has battery installed — acquire fix on battery, then connect USB data |
| Operator | iPhone 15 Pro Max + Apple Watch | USB tether `172.20.10.8/28` or Wi-Fi `192.168.1.155` | Operational; watch records offline sessions and provides safety controls |

---

## 2. Software Stack — What's Deployed

The live backend runs `orin/wavecam/` with the custom direct-LoRa GPS ingest:

- `wavecam/gps_direct_lora.py` (`DirectRadioGps`) reads base Wio JSONL over USB serial.
- `wavecam/tracking_arbiter.py` mediates vision vs GPS ownership.
- `wavecam/fusion.py` performs color+person fusion locking.
- `wavecam/controller.py` / `ptz_visca.py` drive RAW VISCA-over-UDP pan/tilt/zoom.
- `firmware/direct-lora/` is the active GPS transport; Meshtastic is retired.
- iOS app bundles the WaveCamWatch companion (`Sources-Watch/`).

Recent key commits:
- `4a25265` — B13 fix: restore `calibrate` PTZ owner after standalone capture.
- `ca86830` — Backend review fixes B1–B12 (except B3 invalidated).

Tests: **419 passed**.

---

## 3. GPS Control Loop — Phase Status

### 3.1 P0: Data + Display — SHIPPED ✅

GPS data flows end-to-end: tracker Wio → LoRa → base Wio → Orin USB serial → `DirectRadioGps` (off-thread) → `/api/v1/status` GPS snapshot (source, target_age, base_age, distance_m, bearing_deg, stale, target_sats, target_battery_mv). iOS `GlassGPSChip` displays it on the Live HUD.

### 3.2 P1: Arbiter + Absolute Pointing — SHIPPED ✅

The `TrackingArbiter` handoff state machine is implemented and live:

```
vision locked (K consecutive frames) → vision_follow (velocity servo)
vision unlocked + GPS viable → gps_tracker (absolute pan/tilt/zoom to bearing)
neither → idle (hold position)
```

GPS viability requires all three:
1. `gps_fresh` — target age < `stale_threshold_sec` (45 s on the rig)
2. `gps_calibrated` — camera pose with reference heading captured
3. `base_locked` — base fix stable (HDOP ≤ 2.5 for 20 s+)

A hot `tracking.mode` key gates the arbiter: `auto` (default), `gps_only`, `vision_only`.

### 3.2 P2: GPS→Fusion Integration — OBSERVE-ONLY / SHADOW MODE

The shadow Kalman estimator (`estimator.shadow=true`, `enabled=false`) runs observe-only, logging what it would command without touching the camera. GPS-informed fusion confidence injection is still future work; `gps_boost` currently provides a coarse confidence lift to blobs near the GPS bearing.

---

## 4. Known Issues (by severity)

### 🟢 Low: Cross-device TensorRT engine (resolved)

The original `yolov8n.engine` was built on a different GPU and produced unreliable person detections. The live model at `/data/projects/gimbal/models/yolov8n.engine` has been rebuilt/validated on the Orin Nano.

### 🟢 Low: Base Wio acquisition on USB power (workaround known)

The base L76K can show 0 sats when powered from the Orin USB rail due to host RF noise. **Workaround:** the base Wio now has a battery installed; acquire the fix on battery power, then plug USB data to the Orin.

### 🟢 Low: MJPEG preview through Cloudflare Access

Controls and API work through `wavecam.freddieland.com`, but the live MJPEG preview may not stream reliably through Cloudflare Access. Use the local `:8088` preview when on-site.

### 🔵 Historical (resolved by direct-LoRa switch)

- Meshtastic `power.ls_secs` reverts and smart-broadcast 5 m thresholds no longer apply.
- Base Wio no-fix issue was traced to the old PMTK/57600 baud mismatch; the live firmware uses CASIC/PCAS at fixed 9600 baud.

---

## 5. Config State (Live on Orin)

| Key | Current Value | Notes |
|-----|--------------|-------|
| `gps.source` | `direct_lora` | Set in `config.local.yaml` overlay |
| `color.preset` | `orange_red` | Default calibrated preset |
| `color.enabled` | `true` | |
| `fusion.lock_threshold` | `0.6` | |
| `fusion.unlock_threshold` | `0.35` | |
| `fusion.require_person` | `false` | |
| `ptz.cinematic_zoom_enabled` | user-toggled | |
| `gps.stale_threshold_sec` | `45` | Rig overlay |
| `tracking.mode` | `auto` | Hot key: `auto` / `gps_only` / `vision_only` |
| `detector.model` | `yolov8n.engine` | Rebuilt on this Orin |

---

## 6. Immediate Next Steps (in order)

1. **Field hardening** — 100/300/800 m over water, on-body antenna orientation, wet-case packet-age histogram.
2. **External GNSS evaluation** — SparkFun MAX-M10S if L76K rate/quality becomes the binding constraint.
3. **GPS→Fusion confidence injection** — search ROI, zoom-by-distance, full shadow estimator enable.
4. **Watch session scoring pipeline** — ingest 1 Hz GPS + 4 Hz IMU JSONL from WaveCamWatch for offline analysis.

---

## 7. Reference: Specs & Docs

| Document | Path |
|----------|------|
| Direct-LoRa tracker spec | `docs/superpowers/specs/2026-06-12-direct-lora-tracker.md` |
| GPS control loop design | `docs/superpowers/specs/2026-06-09-gps-control-loop-design.md` |
| Wio GPS optimization | `docs/hardware/WIO_TRACKER_GPS_OPTIMIZATION.md` |
| Operator guide | `docs/wavecam_operator_guide.html` |
| Firmware README | `firmware/direct-lora/README.md` |

---

## 8. Historical Context

The original 2026-06-09 version of this report described the Meshtastic-based GPS path and a base-Wio no-fix blocker. That path has been superseded by the custom direct-LoRa firmware. The archived report text is preserved in git history if needed.
