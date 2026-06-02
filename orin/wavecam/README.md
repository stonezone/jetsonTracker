# WAVECAM — Vision-Only Testbed

Bring-up rig for the surf tracker: **camera + Orin + you in an orange jersey.**
Proves the core before any GPS/LoRa work — color detection, YOLO26 person
validation, fusion, and the VISCA visual-servo loop. **No GPS, no wave-state
machine, no recording.** Drives the Prisual PTZ over the validated VISCA-over-IP
path; you watch and tune from a phone/laptop browser.

```
camera (RTSP /2) ──► Orin ──► color(HSV) ┐
                                          ├─ fusion ─► visual servo ─► VISCA pan/tilt
                          YOLO26 person ──┘
                          web console (MJPEG + PTZ owner + live tuning)
```

---

## 1. Install (Jetson Orin Nano, JetPack 6.2.x)

```bash
# system OpenCV (CUDA) is already on JetPack; verify:
python3 -c "import cv2; print(cv2.__version__)"

python3 -m pip install --upgrade pip
pip install numpy pyyaml fastapi "uvicorn[standard]" "pydantic>=2"
```

**Torch / Ultralytics — do NOT `pip install torch` blindly on Jetson.**
Install NVIDIA's JetPack 6.2 (CUDA 12.6) PyTorch wheel first, then Ultralytics:

```bash
# follow the Ultralytics NVIDIA Jetson guide for the exact JP6.2/cu126 wheel:
#   https://docs.ultralytics.com/guides/nvidia-jetson
pip install ultralytics
```

First run downloads `yolo26n.pt` (needs internet once). For speed, export a
TensorRT engine **on the Orin** and point `detector.model` at it:

```bash
yolo export model=yolo26n.pt format=engine half=True   # builds yolo26n.engine (TRT 10.3)
```

> Desktop dev (no camera): `pip install opencv-python ultralytics`, set
> `camera.source: 0` (webcam) and `ptz.enabled: false`.

---

## 2. Configure

Edit `config.yaml`:

- `camera.source` → your Prisual sub-stream, e.g. `rtsp://<ip>:554/2`
- `ptz.ip` → camera IP (VISCA on UDP `1259`)
- `camera_ai.off_path` → your real `set_aimode` CGI (or just turn AI-track **off**
  in the camera web UI)
- `color.preset` → `orange_red` by default; hot-switchable to `orange`, `blue`,
  `green`, `yellow`, or `pink`
- `fusion.person_aim_y` → `0.5` centers the person box; lower values aim higher
  in the YOLO box
- `ptz.ff_gain` and `ptz.ff_deadzone_mult` → feed-forward lead controls; default
  feed-forward is off, and feed-forward is suppressed near the deadzone
- leave **`ptz.enabled: false`** for the first run

---

## 3. Run — in this order

```bash
python run.py
# open  http://<orin-ip>:8088/   on your phone/laptop
```

**Step 1 — detection only (camera does NOT move).**
Stand in frame in the orange jersey. You should see an amber box on the orange
(color) and a grey `person` box (YOLO); the HUD shows `C Y  P Y  M Y` (color,
person, matched) and a green locked box. Tune live from the Orin web UI:
- glare giving false orange blobs → raise **min blob area**
- wrong marker color → change **Color preset** first; edit HSV YAML only when the
  preset is not enough
- wrong orange object stealing lock → enable **Require YOLO person** or lower
  **Color/YOLO match px**
- not locking → lower **Lock threshold** or YOLO confidence; check the **Mask**
  view to see what HSV is catching

**Step 2 — confirm onboard AI-track is OFF** (web UI logs it, or check the camera).

**Step 3 — enable the servo.** Set `ptz.enabled: true` (restart), keep
`max_pan_speed`/`max_tilt_speed` conservative. Open `http://<orin-ip>:8088/`,
then press **Start Auto** to give PTZ ownership to the autonomous tracker. Stand
in frame and step side-to-side; the camera should follow and re-center.
- **camera moves the WRONG way?** flip `invert_pan` / `invert_tilt` (sliders push
  live values; persist them in `config.yaml`).
- jittery / oscillating → raise **Deadband** or **FF deadband mult**, lower max
  speeds, and keep **Feed-forward gain** low/off until the target is stable.
- **KILL** button stops PTZ instantly and latches; **RESUME** re-enables.
- **Stop PTZ** sends stop and holds manual ownership; **Start Auto** hands PTZ
  back to the tracker.

---

## 4. Web console

| Control | Effect |
|---|---|
| live MJPEG | annotated: mask, color/person boxes, locked target, center + deadzone, command arrow, HUD |
| KILL / RESUME | latch PTZ stopped / re-enable |
| Start Auto | request autonomous PTZ owner `testbed`; refused while KILL is latched |
| Stop PTZ | durable manual hold; camera stays stopped until Start Auto |
| Zoom± / Zoom stop | manual zoom velocity commands through the owner gate |
| Color preset | hot-switch HSV presets: `orange_red`, `orange`, `blue`, `green`, `yellow`, `pink` |
| YOLO class / confidence / cadence | hot tune the validator trigger without restart |
| Person aim Y | hot tune where the servo centers inside the person box |
| PTZ tuning | hot tune deadband, feed-forward gain, feed-forward deadband multiplier, speeds, inversion |
| Mask / JPEG quality | hot tune overlay and preview quality |
| `GET /status` | JSON state |
| `GET /api/v1/config` | current config, supported presets/classes, hot keys, restart-only keys |
| `POST /api/v1/config/hot` | live-safe config patch; no restart |
| `POST /api/v1/system/restart` | CONFIG-scoped scheduled `wavecam.service` restart for restart-only changes |

Example hot patch:

```bash
curl -X POST http://<orin-ip>:8088/api/v1/config/hot \
  -H 'Content-Type: application/json' \
  -d '{"patch":{"color.preset":"blue","fusion.require_person":true,"ptz.ff_deadzone_mult":1.8}}'
```

Example restart after editing restart-only config:

```bash
curl -X POST http://<orin-ip>:8088/api/v1/system/restart \
  -H 'Content-Type: application/json' \
  -d '{"reason":"applied structural config","confirm_moving":true}'
```

If a PTZ owner is active, the restart request is refused until
`confirm_moving:true` is supplied. A confirmed restart stops PTZ, sets status to
`RESTARTING`, returns `202`, then asks systemd to restart `wavecam.service`.

---

## 5. Verify the tricky bits offline (no hardware)

```bash
cd orin/wavecam
python -m tests.test_offline
python -m tests.test_controller_extra
python -m tests.test_fusion
python -m tests.test_control_api
cd ..
python scripts/test_vision_follow_logic.py
```

Checks the RAW VISCA byte sequences (no Sony VISCA-over-IP header — what the
Prisual uses on UDP 1259), speed clamps + stop, servo direction/speed mapping,
feed-forward suppression near the deadzone, color/person fusion, Control API hot
config, and the legacy Vision Follow target picker. Run these before trusting
the control path.

---

## 6. VISCA reconciliation

`wavecam/ptz_visca.py` sends **RAW VISCA** — the classic `0x81 … 0xFF` command
bytes with **no** Sony VISCA-over-IP 8-byte header, and parses raw `90 50 … FF`
replies. That is what the Prisual speaks on UDP **1259** (no auth) —
bench-validated end-to-end (pan/tilt/zoom, position readback, two-point
calibration), ground truth in `orin/camera_control/visca_backend.py`. The
controller depends only on the method interface (`pan_tilt`, `stop`, `zoom`,
`home`, `inquire_pan_tilt`), so the transport stays swappable if a future camera
needs the Sony framing header.

---

## Layout

```
config.yaml            all tunables (safe defaults: PTZ off)
run.py                 entrypoint
wavecam/
  config.py            YAML -> dataclasses
  capture.py           threaded RTSP grabber (latest-frame, reconnect)
  color_presets.py     shared HSV preset table for config/API/detector
  color_detector.py    HSV preset blobs + contour filtering
  detector.py          YOLO26 person validator (.pt or .engine)
  fusion.py            color + YOLO -> smoothed target + lock hysteresis
  controller.py        image error -> VISCA velocity (P + deadzone + guarded feed-forward)
  ptz_visca.py         VISCA-over-IP UDP client
  camera_http.py       best-effort onboard-AI-off (CGI)
  overlay.py           annotated debug frame
  pipeline.py          the deterministic loop + shared state
  control_api.py       /api/v1 status, PTZ, safety, media, hot config
  web.py               FastAPI: MJPEG, status, tune UI, kill/PTZ controls
tests/test_offline.py  RAW VISCA bytes + servo math (no deps)
```

## Not in this rig (next steps)
GPS/LoRa cueing, wave-state machine (riding vs back-out), recording + pre-roll
buffer, encoder readback / GPS↔encoder calibration. See WAVECAM-EDS v2.2 §05–§16.
