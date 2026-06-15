# Orin Backend

This directory contains the Jetson Orin software for WaveCam. The current
production backend is `orin/wavecam/`: a FastAPI control API, vision tracker,
PTZ controller, recorder, and live MJPEG/web surface for the Prisual PTZ camera.

The older Watch/Cloudflare/Nucleo/stepper pipeline is archived under
`archive/legacy-20260606/`. It is preserved for reference and reuse, but it is
not the active WaveCam runtime.

## Current Runtime

| Item | Current value |
|---|---|
| Service | `wavecam.service` |
| Live API | `http://<orin>:8088/api/v1` |
| Guide/Web | `http://<orin>:8088/guide` and `http://<orin>:8088/` |
| Orin Wi-Fi | `192.168.1.155` (`ssh orin`) |
| Orin camera LAN | `192.168.100.10/24` |
| PTZ camera | Prisual NDI PTZ at `192.168.100.88` |
| PTZ control | RAW VISCA UDP `192.168.100.88:1259` |
| Detection stream | RTSP sub-stream `rtsp://192.168.100.88:554/2` |
| Recording stream | RTSP main stream `rtsp://192.168.100.88:554/1` |
| Production model | `/data/projects/gimbal/models/yolov8n.engine` |
| Loop target | `35` FPS; live validation has shown 30+ FPS |
| GPS source | Direct-LoRa base Wio → `DirectRadioGps` (`/dev/ttyACM0`) |

## Canonical Backend

| Path | Purpose |
|---|---|
| `wavecam/run.py` | Entry point used by `wavecam.service` |
| `wavecam/config.orin.servo.yaml` | Live Orin servo config; camera will move |
| `wavecam/wavecam/control_api.py` | FastAPI routes for status, config, PTZ, media, logs, presets, guide |
| `wavecam/wavecam/pipeline.py` | Vision loop orchestration |
| `wavecam/wavecam/controller.py` | Visual servo, PTZ command decision logic, cinematic zoom |
| `wavecam/wavecam/ptz_visca.py` | RAW VISCA-over-UDP adapter for the Prisual camera |
| `wavecam/wavecam/fusion.py` | Person/color fusion and lock state |
| `wavecam/wavecam/detector.py` | YOLO detector wrapper |
| `wavecam/wavecam/color_detector.py` | HSV color cue detector |
| `wavecam/wavecam/recorder.py` | FFmpeg segmented recorder for the main RTSP stream |
| `wavecam/tests/` | Backend regression tests |

## Legacy Directories

These are not the active field runtime:

| Path | Status |
|---|---|
| `vision/` | Earlier standalone vision-follow experiments |
| `gps_fusion/` | Reusable GPS math/pointing pieces consumed by `wavecam/` via the direct-LoRa path |
| `scripts/phone_webcam.sh` | Old DroidCam/scrcpy helper; not used by the Prisual PTZ stack |

Archived retired paths:

| Archived path | Contents |
|---|---|
| `../archive/legacy-20260606/apple-gps-cloudflare/` | Old GPS relay, Cloudflare config, GPS server, and GPS service files |
| `../archive/legacy-20260606/stm32-nucleo-stepper/` | Old STM32/Nucleo firmware, DRV8825 wiring, and UART stepper controller |
| `../archive/legacy-20260606/retired-dashboard/` | Old `:8080` dashboard service and Python files |

## Run On The Orin

The deployed service runs from `/data/projects/gimbal/wavecam`:

```bash
ssh orin
systemctl is-active wavecam.service
systemctl status wavecam.service --no-pager
curl -s http://localhost:8088/api/v1/status
```

Manual foreground run for maintenance only:

```bash
cd /data/projects/gimbal/wavecam
python3 run.py config.orin.servo.yaml
```

Do not start the retired `dashboard.service` for normal WaveCam use. `:8088` is
the active API/web surface; `:8080` is legacy.

## Test Locally

From the repo root on the Mac:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=orin/wavecam python3 -m pytest orin/wavecam/tests -q
PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q orin/wavecam/wavecam
```

The full backend suite imports OpenCV for color tests. On desktop, install
`opencv-python-headless` or `opencv-python`; on Jetson, prefer the system OpenCV
from JetPack.

## Deploy Safely

Backend deploys are Codex/Zack lane and should be backup-first:

1. Confirm the target tree from systemd:
   ```bash
   systemctl show wavecam.service --property=WorkingDirectory --property=ExecStart --no-pager
   ```
2. Back up changed live files under
   `/data/projects/gimbal/wavecam/.codex-backups/<timestamp>-<reason>/`.
3. Copy only the changed files.
4. Restart only `wavecam.service`.
5. Verify:
   ```bash
   systemctl is-active wavecam.service
   curl -s http://localhost:8088/api/v1/status
   curl -s http://localhost:8088/api/v1/config
   ```
6. If media/recording changed, run a short record start/stop validation and
   remove only the generated validation clip.

## More Docs

- `orin/wavecam/README.md` - WaveCam backend details.
- `docs/ORIN_MAINTENANCE_RUNBOOK.md` - Orin maintenance, tether, backup, update, boot notes.
- `docs/ORIN_FIELD_RELIABILITY.md` - IP/reachability and field reliability checks.
- `docs/hardware/WAVECAM_POWER_WIRING.md` - Current field-power wiring target.
- `docs/WaveCam_Guide.html` - Operator guide served by the backend.
