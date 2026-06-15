# WaveCam

WaveCam is a Jetson Orin + Prisual PTZ camera stack for filming foil surfing.
The live system is vision-first: YOLOv8n TensorRT detects a person, an
orange/red HSV cue identifies the subject, fusion locks the target, and the
Orin drives pan/tilt/zoom over RAW VISCA. A custom direct-LoRa Wio GPS link
provides long-range coarse pointing and helps vision reacquire the subject.

The old Apple Watch/iPhone/Cloudflare GPS relay and STM32/Nucleo stepper gimbal
design is superseded. Retired code and docs are preserved under
`archive/legacy-20260606/`, not deleted.

## Current Runtime

| Item | Current value |
|---|---|
| Orin host | `ssh orin`, Wi-Fi `192.168.1.155` |
| iPhone tether default | `172.20.10.8` |
| WaveCam API/Web | `http://<orin>:8088` |
| Remote web UI (Cloudflare) | `https://wavecam.freddieland.com` |
| Live service | `wavecam.service` |
| Live service tree | `/data/projects/gimbal/wavecam` |
| Base config | `config.orin.servo.yaml` |
| Runtime overlay | `config.local.yaml` (rig-owned, survives deploys) |
| PTZ camera | Prisual NDI PTZ at `192.168.100.88` |
| Orin camera LAN | `192.168.100.10/24` |
| PTZ control | RAW VISCA UDP `192.168.100.88:1259` |
| Tracking video | RTSP sub-stream `rtsp://192.168.100.88:554/2` |
| Recording video | RTSP main stream `rtsp://192.168.100.88:554/1` |
| Detector model | `/data/projects/gimbal/models/yolov8n.engine` |
| Loop target | `35` FPS; live validation has shown 30+ FPS |

## Architecture

```text
iOS WaveCam app / WaveCamWatch / browser
        |
        v
Jetson Orin :8088 FastAPI + web  (also exposed via Cloudflare tunnel)
        |
        +-- RTSP /2 -> YOLOv8n TensorRT + orange/red color cue -> fusion lock
        |
        +-- direct-LoRa Wio GPS -> coarse absolute PTZ pointing
        |
        +-- visual servo + cinematic zoom -> RAW VISCA UDP -> Prisual PTZ
        |
        +-- RTSP /1 -> FFmpeg segmented MP4 recorder
```

Movement authority is centralized on the Orin. KILL/resume, manual PTZ, auto
tracking, home, zoom, and deadman behavior must route through the backend owner
model instead of side-channel camera commands.

## Active Features

- FastAPI control API on `:8088` (and via Cloudflare at `wavecam.freddieland.com`).
- Native iOS operator app + bundled watchOS companion (WaveCamWatch).
- YOLOv8n TensorRT person detection.
- HSV orange/red color cue and person/color fusion.
- Direct-LoRa Wio GPS for coarse pointing and vision reacquisition.
- PTZ pan/tilt/zoom over RAW VISCA UDP.
- `tracking.mode` selector: `auto`, `gps_only`, `vision_only`.
- Cinematic Zoom feature-detected through `GET /api/v1/config`.
- Hot config patching for tuning controls (persisted to `config.local.yaml`).
- Presets, logs, guide, guide assets, media list/download/delete.
- FFmpeg segmented recording from the camera main RTSP stream.
- Persistent journald enabled on the Orin for post-reboot diagnostics.

## Open Items

| Item | Status |
|---|---|
| Full bare-metal Orin restore image | Not done; critical restore bundle exists in iCloud |
| Long field soak under sustained tracking + recording | Not yet a full-session proof |
| Legacy docs/assets cleanup | Archived under `archive/legacy-20260606/` |

## Quick Checks

On the Orin:

```bash
systemctl is-active wavecam.service
curl -s http://localhost:8088/api/v1/status
curl -s http://localhost:8088/api/v1/config
curl -s http://localhost:8088/guide
```

From the Mac:

```bash
curl -s http://192.168.1.155:8088/api/v1/status
curl -s http://192.168.1.155:8088/api/v1/config
```

Manual foreground run for maintenance only:

```bash
ssh orin
cd /data/projects/gimbal/wavecam
python3 run.py config.orin.servo.yaml
```

Do not start the retired `dashboard.service` for normal WaveCam operation.
`:8088` is active; `:8080` is legacy.

## Project Structure

```text
jetsonTracker/
├── orin/
│   ├── wavecam/              # Current backend: API, tracking, PTZ, recording
│   │   ├── run.py
│   │   ├── config.orin.servo.yaml
│   │   ├── wavecam/
│   │   └── tests/
│   ├── gps_fusion/           # Legacy GPS math (superseded by wavecam/gps_*.py)
│   ├── vision/               # Earlier standalone vision experiments
│   └── camera_control/       # Reusable VISCA/PTZ adapter experiments
├── firmware/direct-lora/     # Active Wio Tracker L1 tracker/base firmware
├── ios/WaveCam/              # Native iOS operator app + watchOS companion
│   ├── Sources/              # iOS app
│   └── Sources-Watch/        # WaveCamWatch
├── docs/
│   ├── WaveCam_Guide.html
│   ├── ORIN_MAINTENANCE_RUNBOOK.md
│   ├── ORIN_FIELD_RELIABILITY.md
│   ├── hardware/WAVECAM_POWER_WIRING.md
│   └── superpowers/specs/
├── archive/legacy-20260606/  # Preserved retired GPS, Nucleo, gimbal, dashboard material
└── .agent-collab/            # Claude/Codex coordination bus
```

## Backend API

**Base:** `http://<orin>:8088/api/v1`

Common checks:

```bash
curl -s http://<orin>:8088/api/v1/status
curl -s http://<orin>:8088/api/v1/config
curl -s http://<orin>:8088/api/v1/media/status
curl -s http://<orin>:8088/api/v1/presets
curl -s http://<orin>:8088/guide
```

Current surfaces include status, config, hot config, system restart, PTZ movement,
zoom, home, KILL/resume, owner/deadman behavior, media list/download/delete,
record start/stop, presets, logs, guide, and guide assets.

Use `GET /api/v1/config` as the authoritative source for supported features,
hot keys, and restart-only keys. Do not infer support from stale documentation.

## Recording Contract

`POST /api/v1/media/record/start` returns `segment_pattern` and
`segment_prefix`. `segment_name` is `null` until FFmpeg creates a real current
segment file. When recording is stopped, `segment_name` may report the latest
existing clip.

That contract prevents clients and cleanup scripts from treating an older clip
as the active recording immediately after start.

## Local Backend Tests

From the repo root:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=orin/wavecam python3 -m pytest orin/wavecam/tests -q
PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q orin/wavecam/wavecam
git diff --check
```

The full backend suite imports OpenCV for color tests. On desktop, install
`opencv-python-headless` or `opencv-python`; on Jetson, prefer the JetPack system
OpenCV unless there is a specific reason to override it.

## Safe Backend Deploy

Do not deploy by copying the whole repo over the live tree. Use a file-scoped,
backup-first deploy:

```bash
ssh orin "systemctl show wavecam.service --property=WorkingDirectory --property=ExecStart --no-pager"
ssh orin "mkdir -p /data/projects/gimbal/wavecam/.codex-backups/<timestamp>-<reason>"
scp <changed-file> orin:/data/projects/gimbal/wavecam/<path>
ssh orin "sudo systemctl restart wavecam.service"
curl -s http://192.168.1.155:8088/api/v1/status
```

If PTZ behavior changed, verify the camera is stopped/safe before and after the
restart. If recording/media changed, run a short disposable recording and delete
only the generated validation clip.

## Current Production Config

`orin/wavecam/config.orin.servo.yaml` is the production-style servo config.
The live Orin also loads `/data/projects/gimbal/wavecam/config.local.yaml` as an
overlay on top of the base YAML; hot-config changes and the active GPS source
selection live there and survive deploys.

Key live values:

- `camera.source`: `rtsp://192.168.100.88:554/2`
- `ptz.ip`: `192.168.100.88`
- `ptz.port`: `1259`
- `detector.model`: `/data/projects/gimbal/models/yolov8n.engine`
- `gps.source`: `direct_lora` (set in `config.local.yaml` overlay)
- `gps.direct_dev_path`: `/dev/ttyACM0`
- `web.port`: `8088`
- `web.show_hud`: `true`
- `loop.target_fps`: `35`

## Documentation

- [WaveCam Guide](docs/WaveCam_Guide.html)
- [Orin backend docs](orin/README.md)
- [WaveCam backend docs](orin/wavecam/README.md)
- [Orin maintenance runbook](docs/ORIN_MAINTENANCE_RUNBOOK.md)
- [Orin field reliability](docs/ORIN_FIELD_RELIABILITY.md)
- [Field power wiring](docs/hardware/WAVECAM_POWER_WIRING.md)
- [Direct-LoRa tracker spec](docs/superpowers/specs/2026-06-12-direct-lora-tracker.md)

## Archived Legacy Areas

| Path | Legacy status |
|---|---|
| `archive/legacy-20260606/apple-gps-cloudflare/` | Retired Watch/iPhone GPS relay, Cloudflare config, GPS server, GPS service files, and submodule pointer |
| `archive/legacy-20260606/stm32-nucleo-stepper/` | Retired STM32/Nucleo firmware, DRV8825 wiring, serial protocol, and UART stepper controller |
| `archive/legacy-20260606/custom-printed-gimbal/` | Retired custom printed gimbal docs |
| `archive/legacy-20260606/retired-dashboard/` | Retired `:8080` dashboard |
| `archive/legacy-20260606/retired-docs/` | Superseded architecture/setup/BOM/failure-mode docs |
| `orin/vision/` | Earlier standalone vision experiments; left in place for now |
| `orin/gps_fusion/` | Reusable GPS math/pointing pieces for future Wio/LoRa cueing |

## License

To be determined.

---

**Last Updated:** June 6, 2026
**Project Status:** WaveCam backend live on Orin; LoRa GPS is future work.
