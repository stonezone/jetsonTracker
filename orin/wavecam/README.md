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
                          web console (MJPEG + kill + live sliders)
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
person, matched) and a green locked box. Tune live with the sliders:
- glare giving false orange blobs → raise **min blob area**
- not locking → lower **conf threshold**; check the **Mask** view to see what HSV
  is catching (tighten/loosen HSV bands in `config.yaml` if needed)

**Step 2 — confirm onboard AI-track is OFF** (web UI logs it, or check the camera).

**Step 3 — enable the servo.** Set `ptz.enabled: true` (restart), keep
`max_pan_speed`/`max_tilt_speed` conservative. Stand in frame and step
side-to-side; the camera should follow and re-center.
- **camera moves the WRONG way?** flip `invert_pan` / `invert_tilt` (sliders push
  live values; persist them in `config.yaml`).
- jittery / oscillating → raise **deadzone**, lower max speeds.
- **KILL** button stops PTZ instantly and latches; **RESUME** re-enables.

---

## 4. Web console

| Control | Effect |
|---|---|
| live MJPEG | annotated: mask, color/person boxes, locked target, center + deadzone, command arrow, HUD |
| KILL / RESUME | latch PTZ stopped / re-enable |
| PTZ stop / Zoom± | manual nudges |
| Mask | toggle mask overlay |
| sliders | live conf threshold, min blob area, deadzone, max pan/tilt speed |
| `GET /status` | JSON state |

---

## 5. Verify the tricky bits offline (no hardware)

```bash
python -m tests.test_offline
```

Checks the RAW VISCA byte sequences (no Sony VISCA-over-IP header — what the
Prisual uses on UDP 1259), speed clamps + stop, and servo direction/speed
mapping. Run this before trusting the control path.

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
  color_detector.py    HSV dual-band orange/red blobs
  detector.py          YOLO26 person validator (.pt or .engine)
  fusion.py            color + YOLO -> smoothed target + lock hysteresis
  controller.py        image error -> VISCA velocity (P + deadzone)
  ptz_visca.py         VISCA-over-IP UDP client
  camera_http.py       best-effort onboard-AI-off (CGI)
  overlay.py           annotated debug frame
  pipeline.py          the deterministic loop + shared state
  web.py               FastAPI: MJPEG, status, tune, kill
tests/test_offline.py  RAW VISCA bytes + servo math (no deps)
```

## Not in this rig (next steps)
GPS/LoRa cueing, wave-state machine (riding vs back-out), recording + pre-roll
buffer, encoder readback / GPS↔encoder calibration. See WAVECAM-EDS v2.2 §05–§16.
