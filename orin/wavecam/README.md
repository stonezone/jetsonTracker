# WaveCam Backend

`orin/wavecam/` is the canonical backend for the live WaveCam rig. It runs on
the Jetson Orin, serves the iOS app and browser UI on `:8088`, drives the
Prisual PTZ camera over RAW VISCA UDP, tracks the orange-confirmed person target,
records MP4 segments, and exposes guide/assets/logs/presets/media APIs.

This backend replaces the old Watch/Cloudflare/Nucleo/stepper design. The old
directories remain in the repo for reference, but they are not the active field
runtime.

## Live Service

| Item | Value |
|---|---|
| Service | `wavecam.service` |
| Working directory | `/data/projects/gimbal/wavecam` |
| Config | `config.orin.servo.yaml` |
| API base | `http://<orin>:8088/api/v1` |
| Orin Wi-Fi | `192.168.1.155` |
| iPhone tether default | `172.20.10.8` |
| PTZ camera | `192.168.100.88` |
| Camera LAN | Orin `192.168.100.10/24` to camera `192.168.100.88` |
| Detector model | `/data/projects/gimbal/models/yolov8n.engine` |
| Target loop FPS | `35`; live validation has shown 30+ FPS |

## Data Flow

```text
RTSP /2 sub-stream
    -> capture
    -> YOLOv8n TensorRT person detector
    -> HSV orange/red color cue
    -> fusion lock state
    -> visual servo + cinematic zoom
    -> RAW VISCA UDP pan/tilt/zoom commands

RTSP /1 main stream
    -> ffmpeg segmented MP4 recorder
    -> /api/v1/media list/download/delete/status
```

PTZ ownership is centralized by `PtzOwner`. Manual controls, auto tracking,
home, kill, and deadman behavior should route through that owner model rather
than issuing side-channel camera commands.

## Main Modules

| Module | Role |
|---|---|
| `run.py` | Service entry point. Loads YAML and starts the WaveCam app. |
| `wavecam/config.py` | YAML to typed dataclasses. |
| `wavecam/control_api.py` | FastAPI routes, auth, status/config/media/presets/logs/guide. |
| `wavecam/pipeline.py` | Capture/inference/fusion/control loop. |
| `wavecam/controller.py` | PTZ speed decisions and cinematic zoom. |
| `wavecam/ptz_owner.py` | PTZ owner/deadman coordination. |
| `wavecam/ptz_visca.py` | RAW VISCA-over-UDP camera transport. |
| `wavecam/fusion.py` | Color/person matching and lock/unlock state. |
| `wavecam/detector.py` | YOLO inference wrapper. |
| `wavecam/color_detector.py` | HSV color detection. |
| `wavecam/recorder.py` | FFmpeg segmented recorder for RTSP `/1`. |
| `wavecam/web.py` | MJPEG/live web surface and static guide assets. |

## Configuration

Production config is `config.orin.servo.yaml`.

Important current values:

- `camera.source`: `rtsp://192.168.100.88:554/2`
- `ptz.ip`: `192.168.100.88`
- `ptz.port`: `1259`
- `detector.model`: `/data/projects/gimbal/models/yolov8n.engine`
- `web.port`: `8088`
- `loop.target_fps`: `35`

Hot config is exposed through `POST /api/v1/config/hot`. Structural keys need a
service restart through `POST /api/v1/system/restart`.

Current hot controls include PTZ deadzone/speeds/inversion/feed-forward,
cinematic zoom, fusion thresholds, color preset/areas, detector confidence and
cadence, JPEG quality, and HUD visibility. Query `GET /api/v1/config` for the
authoritative `supported`, `hot_keys`, and `restart_required_keys` sets.

## API Checks

```bash
curl -s http://<orin>:8088/api/v1/status
curl -s http://<orin>:8088/api/v1/config
curl -s http://<orin>:8088/api/v1/media/status
curl -s http://<orin>:8088/api/v1/presets
curl -s http://<orin>:8088/guide
```

Recording controls:

```bash
curl -X POST http://<orin>:8088/api/v1/media/record/start \
  -H 'Content-Type: application/json' \
  -d '{"segment_seconds":120}'

curl -X POST http://<orin>:8088/api/v1/media/record/stop \
  -H 'Content-Type: application/json' \
  -d '{}'
```

Recorder metadata contract:

- `segment_pattern` and `segment_prefix` identify the active recording pattern.
- `segment_name` is only an actual current segment while recording; it is `null`
  until ffmpeg has created a segment file.
- When not recording, `segment_name` may report the latest existing clip for
  convenience.

This avoids treating an older clip as the active recording immediately after
`record/start`.

## Tests

From the repo root:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=orin/wavecam python3 -m pytest orin/wavecam/tests -q
PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q orin/wavecam/wavecam
git diff --check
```

OpenCV is required for `tests/test_color.py`. On desktop, install
`opencv-python-headless`; on Jetson, use the JetPack system OpenCV unless there
is a specific reason to override it.

## Live Deploy Pattern

Do not deploy by copying the whole repository over the live tree. Use a
backup-first, file-scoped deploy:

```bash
ssh orin systemctl show wavecam.service --property=WorkingDirectory --property=ExecStart --no-pager
ssh orin mkdir -p /data/projects/gimbal/wavecam/.codex-backups/<timestamp>-<reason>
scp <changed-file> orin:/data/projects/gimbal/wavecam/<path>
ssh orin sudo systemctl restart wavecam.service
curl -s http://192.168.1.155:8088/api/v1/status
```

If the change affects PTZ behavior, verify the camera is in a safe state before
and after restart. If the change affects recording or media, use a disposable
short recording and delete only the generated validation clip.

## Legacy Notes

- `yolo26n.pt` is not the live production model. The live Orin uses
  `yolov8n.engine`.
- `gps_server.py`, Watch/iPhone relay, and Cloudflare GPS are legacy.
- STM32/Nucleo stepper firmware is legacy; current movement is the Prisual PTZ.
- The retired dashboard on `:8080` should remain stopped/disabled for normal
  WaveCam operation.
