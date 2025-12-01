# Jetson Orin Nano Complete Software Setup Guide

*Verified working: November 29, 2025*

This guide covers the complete software setup for the Robot Cameraman vision tracking system on Jetson Orin Nano, starting from a fresh JetPack installation.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [JetPack Verification](#jetpack-verification)
3. [System Configuration](#system-configuration)
4. [Python Environment](#python-environment)
5. [PyTorch & torchvision](#pytorch--torchvision)
6. [Project Setup](#project-setup)
7. [Camera Configuration](#camera-configuration)
8. [YOLO & TensorRT](#yolo--tensorrt)
9. [GPS Server](#gps-server)
10. [Gimbal Controller](#gimbal-controller)
11. [Running the Tracker](#running-the-tracker)
12. [Verification Checklist](#verification-checklist)

---

## Prerequisites

### Hardware
- Jetson Orin Nano Developer Kit (8GB recommended)
- MicroSD card (64GB+ recommended) or NVMe SSD
- USB camera (or IP camera)
- Power supply (USB-C PD or barrel jack)
- Optional: STM32 Nucleo for gimbal control
- Optional: iPhone/Apple Watch for GPS tracking

### Software
- JetPack 6.2 (L4T R36.4.x) flashed to device
- SSH access configured
- Internet connection

---

## JetPack Verification

After flashing JetPack, verify your installation:

```bash
# Check L4T version
cat /etc/nv_tegra_release
# Expected: R36 (release), REVISION: 4.x

# Check CUDA
nvcc --version
# Expected: Cuda compilation tools, release 12.x

# Check TensorRT
dpkg -l | grep tensorrt | head -1
# Expected: tensorrt 10.3.x

# Check cuDNN
cat /usr/include/cudnn_version.h | grep CUDNN_MAJOR -A 2
# Expected: CUDNN_MAJOR 9

# Check Python
python3 --version
# Expected: Python 3.10.x
```

### Expected JetPack 6.2 Stack

| Component | Version |
|-----------|---------|
| L4T | R36.4.7 |
| CUDA | 12.5 |
| cuDNN | 9.3.0 |
| TensorRT | 10.3.0 |
| Python | 3.10 |

---

## System Configuration

### 1. Update System Packages

```bash
sudo apt-get update
sudo apt-get upgrade -y
```

### 2. Install System Dependencies

```bash
sudo apt-get install -y \
  build-essential \
  cmake \
  git \
  wget \
  curl \
  htop \
  nano \
  v4l-utils \
  libv4l-dev \
  libjpeg-dev \
  zlib1g-dev \
  libpython3-dev \
  libopenblas-dev \
  libavcodec-dev \
  libavformat-dev \
  libswscale-dev \
  libgstreamer1.0-dev \
  libgstreamer-plugins-base1.0-dev \
  gstreamer1.0-tools \
  gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad
```

### 3. Configure Power Mode (Optional but Recommended)

For maximum performance:

```bash
# Check current power mode
sudo nvpmodel -q

# Set to maximum performance (15W mode)
sudo nvpmodel -m 0

# Maximize clocks
sudo jetson_clocks
```

For balanced power/performance:
```bash
sudo nvpmodel -m 1  # 10W mode
```

### 4. Set Up Project Directory

```bash
mkdir -p /data/projects/gimbal
cd /data/projects/gimbal
```

---

## Python Environment

### 1. Verify pip

```bash
python3 -m pip --version
# If not installed:
sudo apt-get install python3-pip
```

### 2. Upgrade pip

```bash
python3 -m pip install --upgrade pip
```

### 3. Install Python Dependencies (Non-PyTorch)

```bash
pip3 install --user \
  numpy>=1.24.0 \
  opencv-python>=4.7.0 \
  filterpy>=1.4.5 \
  pyyaml>=6.0 \
  pyserial>=3.5 \
  websockets>=12.0 \
  requests \
  tqdm \
  pandas \
  seaborn \
  psutil \
  py-cpuinfo \
  pillow
```

---

## PyTorch & torchvision

> **CRITICAL**: Do NOT use `pip install torch torchvision`. This will install CPU-only versions that don't work with the Jetson GPU. See [TORCH.md](TORCH.md) for detailed explanation.

### 1. Install NVIDIA's Jetson PyTorch

```bash
pip3 install --no-cache \
  https://developer.download.nvidia.com/compute/redist/jp/v61/pytorch/torch-2.5.0a0+872d972e41.nv24.08.17622132-cp310-cp310-linux_aarch64.whl
```

### 2. Verify CUDA Works

```bash
python3 -c "import torch; print(f'CUDA: {torch.cuda.is_available()}')"
# Must output: CUDA: True
```

### 3. Build torchvision from Source

```bash
cd /tmp
git clone --branch v0.20.0 https://github.com/pytorch/vision.git
cd vision
pip3 install pillow
python3 setup.py install --user
```

This takes 10-20 minutes. When complete, verify:

```bash
python3 -c "
import torch, torchvision
from torchvision.ops import nms
print(f'torch: {torch.__version__}')
print(f'torchvision: {torchvision.__version__}')
print(f'CUDA: {torch.cuda.is_available()}')
boxes = torch.tensor([[0.,0.,10.,10.],[1.,1.,11.,11.]], device='cuda')
scores = torch.tensor([0.9, 0.8], device='cuda')
print(f'NMS GPU test: {nms(boxes, scores, 0.5).tolist()}')
"
```

### 4. Create Constraints File

Protect your setup from future pip accidents:

```bash
cat > ~/constraints.txt << 'EOF'
torch==2.5.0a0+872d972e41.nv24.08
torchvision==0.20.0a0+afc54f7
EOF
```

---

## Project Setup

### 1. Clone the Project

```bash
cd /data/projects
git clone <your-repo-url> gimbal
cd gimbal
```

Or sync from your development machine:
```bash
rsync -avz --exclude '__pycache__' --exclude '.git' \
  /path/to/jetsonTracker/orin/ orin:/data/projects/gimbal/
```

### 2. Create Required Directories

```bash
mkdir -p /data/projects/gimbal/models
mkdir -p /data/projects/gimbal/logs
```

---

## Camera Configuration

### 1. List Available Cameras

```bash
v4l2-ctl --list-devices
```

### 2. Check Camera Capabilities

```bash
v4l2-ctl -d /dev/video0 --list-formats-ext
```

### 3. Test Camera

```bash
# Quick test with OpenCV
python3 -c "
import cv2
cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
ret, frame = cap.read()
print(f'Camera working: {ret}')
print(f'Frame shape: {frame.shape if ret else None}')
cap.release()
"
```

### 4. DroidCam Phone Webcam (Recommended for Testing)

DroidCam turns an Android phone into a webcam. Two connection methods:

#### Method A: USB (Recommended - Lower Latency)

1. Install DroidCam app on Android phone
2. Enable USB Debugging in Developer Options
3. Connect phone to Orin's **USB-A port** (USB-C is device-mode only!)
4. Set up adb port forwarding:

```bash
# Verify phone is connected
adb devices

# Forward DroidCam port
adb forward tcp:4747 tcp:4747

# Test the stream
python3 -c "
import cv2
cap = cv2.VideoCapture('http://localhost:4747/video')
ret, frame = cap.read()
print(f'Working: {ret}, Shape: {frame.shape if ret else None}')
cap.release()
"
```

#### Method B: WiFi

1. Connect phone and Orin to same network
2. Open DroidCam, note the IP address shown
3. Access stream at `http://<phone-ip>:4747/video`

```bash
# Test WiFi stream
python3 -c "
import cv2
cap = cv2.VideoCapture('http://192.168.1.33:4747/video')  # Your phone's IP
ret, frame = cap.read()
print(f'Working: {ret}')
cap.release()
"
```

> **Note**: The Orin's USB-C port is in device mode (tegra-xudc) and cannot act as a USB host. Use the USB-A port with an adapter if needed.

---

## YOLO & TensorRT

### 1. Install ultralytics (Safely)

```bash
pip3 install ultralytics --no-deps
```

### 2. Download YOLOv8n Model

```bash
cd /data/projects/gimbal/models
python3 -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"
```

### 3. Export to TensorRT Engine

This creates a GPU-optimized model for your specific Jetson:

```bash
cd /data/projects/gimbal/models
python3 -c "
from ultralytics import YOLO
model = YOLO('yolov8n.pt')
model.export(format='engine', device=0, half=True)
"
```

Wait for export to complete (~2-5 minutes). This creates `yolov8n.engine`.

### 4. Benchmark Performance

```bash
python3 -c "
from ultralytics import YOLO
import numpy as np
import time

model = YOLO('yolov8n.engine', task='detect')
dummy = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)

# Warmup
for _ in range(5): model(dummy, verbose=False)

# Benchmark
times = []
for _ in range(50):
    start = time.time()
    model(dummy, verbose=False)
    times.append(time.time() - start)

print(f'Avg: {sum(times)/len(times)*1000:.1f}ms')
print(f'FPS: {1/(sum(times)/len(times)):.1f}')
"
```

Expected: 50+ FPS on Orin Nano.

---

## GPS Server

The GPS server receives location data from iPhone/Apple Watch via Cloudflare Tunnel.

### 1. Install websockets

```bash
pip3 install websockets
```

### 2. Test GPS Server

```bash
cd /data/projects/gimbal
python3 gps_server.py
```

The server listens on `0.0.0.0:8765`. It expects connections from:
- Cloudflare Tunnel (external iPhone/Watch connections)
- Local clients (vision tracker, fusion engine)

### 3. Set Up as systemd Service (Optional)

```bash
sudo tee /etc/systemd/system/gps-server.service << 'EOF'
[Unit]
Description=Robot Cameraman GPS Server
After=network.target

[Service]
Type=simple
User=zack
WorkingDirectory=/data/projects/gimbal
ExecStart=/usr/bin/python3 /data/projects/gimbal/gps_server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable gps-server
sudo systemctl start gps-server
```

---

## Gimbal Controller

### 1. Connect STM32 Nucleo

Connect via USB. Check the serial port:

```bash
ls /dev/ttyACM*
# Usually /dev/ttyACM0
```

### 2. Set Permissions

```bash
sudo usermod -a -G dialout $USER
# Log out and back in for this to take effect
```

### 3. Test Gimbal Connection

```bash
python3 -c "
from gimbal_controller import GimbalController
gc = GimbalController('/dev/ttyACM0')
if gc.connect():
    print('Connected!')
    if gc.ping():
        print('Gimbal responding!')
    gc.disconnect()
"
```

---

## Running the Tracker

### Basic Vision Tracker (No GPS)

```bash
cd /data/projects/gimbal
python3 vision_tracker.py --camera 0 --model models/yolov8n.engine
```

### Integrated GPS-Vision Tracker

```bash
# Terminal 1: Start GPS server
python3 gps_server.py

# Terminal 2: Start integrated tracker
python3 -m gps_fusion.tracker_integration \
  --camera 0 \
  --gps-uri ws://localhost:8765
```

### Command Line Options

```
--camera ID       Camera device ID (default: 0)
--gps-uri URL     GPS server WebSocket URL
--no-gps          Disable GPS fusion
--no-gimbal       Disable gimbal control
--no-predict      Disable predictive tracking
```

---

## Verification Checklist

Run this script to verify everything is working:

```bash
python3 << 'EOF'
import sys

def check(name, condition):
    status = "✓" if condition else "✗"
    print(f"[{status}] {name}")
    return condition

results = []

# PyTorch & CUDA
try:
    import torch
    results.append(check("PyTorch installed", True))
    results.append(check("CUDA available", torch.cuda.is_available()))
    results.append(check("PyTorch version correct", "nv24" in torch.__version__))
except ImportError:
    results.append(check("PyTorch installed", False))

# torchvision
try:
    import torchvision
    from torchvision.ops import nms
    results.append(check("torchvision installed", True))
    # Test GPU op
    boxes = torch.tensor([[0.,0.,10.,10.]], device='cuda')
    scores = torch.tensor([0.9], device='cuda')
    nms(boxes, scores, 0.5)
    results.append(check("torchvision GPU ops work", True))
except Exception as e:
    results.append(check(f"torchvision: {e}", False))

# ultralytics
try:
    from ultralytics import YOLO
    results.append(check("ultralytics installed", True))
except ImportError:
    results.append(check("ultralytics installed", False))

# OpenCV
try:
    import cv2
    results.append(check("OpenCV installed", True))
except ImportError:
    results.append(check("OpenCV installed", False))

# websockets
try:
    import websockets
    results.append(check("websockets installed", True))
except ImportError:
    results.append(check("websockets installed", False))

# pyserial
try:
    import serial
    results.append(check("pyserial installed", True))
except ImportError:
    results.append(check("pyserial installed", False))

# TensorRT engine
import os
engine_path = "/data/projects/gimbal/models/yolov8n.engine"
results.append(check("TensorRT engine exists", os.path.exists(engine_path)))

# Summary
print("\n" + "="*40)
passed = sum(results)
total = len(results)
print(f"Passed: {passed}/{total}")
if passed == total:
    print("All checks passed! System ready.")
else:
    print("Some checks failed. Review above.")
    sys.exit(1)
EOF
```

---

## Quick Reference

### Start Everything

```bash
# Terminal 1 - GPS Server
cd /data/projects/gimbal && python3 gps_server.py

# Terminal 2 - Vision Tracker
cd /data/projects/gimbal && python3 -m gps_fusion.tracker_integration --camera 0
```

### Check GPU Status

```bash
tegrastats
# or
jtop  # if installed: pip3 install jetson-stats
```

### Monitor Logs

```bash
tail -f /data/projects/gimbal/logs/*.log
```

### If Things Break

1. Check CUDA: `python3 -c "import torch; print(torch.cuda.is_available())"`
2. If False, re-run PyTorch setup from [TORCH.md](TORCH.md)
3. Check camera: `v4l2-ctl --list-devices`
4. Check gimbal: `ls /dev/ttyACM*`

---

## Related Documentation

- [TORCH.md](TORCH.md) - Detailed PyTorch/torchvision setup and troubleshooting
- [WHEEL.md](WHEEL.md) - Wheel compatibility reference
- [PIN_MAPPING.md](wiring/PIN_MAPPING.md) - Hardware wiring reference

---

## Support

For issues:
1. Check the verification script output
2. Review logs in `/data/projects/gimbal/logs/`
3. Consult the troubleshooting sections in TORCH.md
