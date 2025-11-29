# Jetson Orin Nano PyTorch Setup Guide

*Verified working: November 29, 2025*

This guide documents the exact steps to set up PyTorch with CUDA support on Jetson Orin Nano running JetPack 6.2. Following standard `pip install` commands will **break your setup** - this guide explains why and provides the correct approach.

## System Requirements

| Component | Version |
|-----------|---------|
| Hardware | Jetson Orin Nano |
| JetPack | 6.2 (L4T R36.4.7) |
| CUDA | 12.5 |
| cuDNN | 9.3.0 |
| TensorRT | 10.3.0 |
| Python | 3.10 |

## The Problem

Standard pip wheels for PyTorch on ARM64/aarch64 are **CPU-only**. When you run:

```bash
# DON'T DO THIS - breaks CUDA support
pip install torch torchvision
```

You get a CPU-only PyTorch that cannot use the Jetson's GPU. Even worse, if you have NVIDIA's Jetson PyTorch installed and then run `pip install torchvision`, pip will **overwrite** your working CUDA-enabled torch with a CPU-only version.

### Why This Happens

1. PyPI's torch wheels for aarch64 have no CUDA support
2. pip's dependency resolver sees NVIDIA's `torch-2.5.0a0+872d972e41.nv24.08` as "non-standard"
3. When installing torchvision, pip "helpfully" replaces it with generic torch from PyPI
4. Result: `torch.cuda.is_available()` returns `False`

## Solution Overview

1. Install NVIDIA's Jetson-specific PyTorch wheel (has CUDA support)
2. Build torchvision from source (links against installed CUDA torch)
3. Install ultralytics with `--no-deps` (prevents pip from touching torch)
4. Create constraints file to protect the setup forever

## Step-by-Step Setup

### Step 1: Clean Up Any Existing Installation

```bash
pip3 uninstall -y torch torchvision torchaudio ultralytics
```

### Step 2: Install Build Dependencies

```bash
sudo apt-get update
sudo apt-get install -y \
  libjpeg-dev zlib1g-dev libpython3-dev libopenblas-dev \
  libavcodec-dev libavformat-dev libswscale-dev
```

### Step 3: Install NVIDIA's Jetson PyTorch Wheel

This is the official NVIDIA wheel with CUDA support for JetPack 6.x:

```bash
pip3 install --no-cache \
  https://developer.download.nvidia.com/compute/redist/jp/v61/pytorch/torch-2.5.0a0+872d972e41.nv24.08.17622132-cp310-cp310-linux_aarch64.whl
```

**Verify CUDA works:**

```bash
python3 -c "import torch; print(f'torch: {torch.__version__}'); print(f'CUDA: {torch.cuda.is_available()}')"
```

Expected output:
```
torch: 2.5.0a0+872d972e41.nv24.08
CUDA: True
```

If CUDA is `False`, do not proceed - troubleshoot first.

### Step 4: Build torchvision from Source

This is the critical step. Do NOT use `pip install torchvision` - it will break everything.

```bash
# Clone the matching version (0.20.0 matches torch 2.5.0)
cd /tmp
git clone --branch v0.20.0 https://github.com/pytorch/vision.git
cd vision

# Install pillow dependency
pip3 install pillow

# Build and install (takes 10-20 minutes on Orin Nano)
python3 setup.py install --user
```

**Verify torchvision works with CUDA:**

```bash
python3 -c "
import torch, torchvision
from torchvision.ops import nms

print(f'torch: {torch.__version__}')
print(f'torchvision: {torchvision.__version__}')
print(f'CUDA: {torch.cuda.is_available()}')

# Test GPU NMS operation
boxes = torch.tensor([[0.,0.,10.,10.],[1.,1.,11.,11.]], device='cuda')
scores = torch.tensor([0.9, 0.8], device='cuda')
result = nms(boxes, scores, 0.5)
print(f'NMS test (GPU): {result.tolist()} - PASSED')
"
```

Expected output:
```
torch: 2.5.0a0+872d972e41.nv24.08
torchvision: 0.20.0a0+afc54f7
CUDA: True
NMS test (GPU): [0] - PASSED
```

### Step 5: Install ultralytics (YOLO) Safely

Use `--no-deps` to prevent pip from touching torch:

```bash
pip3 install ultralytics --no-deps
```

Then install ultralytics' other dependencies manually (the ones that won't break torch):

```bash
pip3 install numpy opencv-python pyyaml requests tqdm pandas seaborn psutil py-cpuinfo
```

### Step 6: Create Protection (Constraints File)

Create a constraints file to prevent future pip operations from breaking torch:

```bash
cat > ~/constraints.txt << 'EOF'
torch==2.5.0a0+872d972e41.nv24.08
torchvision==0.20.0a0+afc54f7
EOF
```

From now on, when installing any package that might have torch as a dependency:

```bash
pip3 install <package> -c ~/constraints.txt
```

This tells pip to refuse any installation plan that would change torch versions.

### Step 7: Export YOLOv8 to TensorRT

For maximum inference speed, export to TensorRT engine:

```bash
cd /data/projects/gimbal/models  # or your models directory
python3 -c "
from ultralytics import YOLO
model = YOLO('yolov8n.pt')
model.export(format='engine', device=0)
"
```

This creates `yolov8n.engine` (~9MB) optimized for your specific GPU.

### Step 8: Verify Performance

```bash
python3 -c "
from ultralytics import YOLO
import time
import numpy as np

model = YOLO('yolov8n.engine', task='detect')
dummy = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)

# Warm up
for _ in range(3):
    model(dummy, verbose=False)

# Benchmark
times = []
for _ in range(20):
    start = time.time()
    model(dummy, verbose=False)
    times.append(time.time() - start)

fps = 1.0 / (sum(times) / len(times))
print(f'FPS: {fps:.1f}')
"
```

Expected: 50+ FPS on Orin Nano with TensorRT engine.

## Final Working Stack

| Component | Version | Notes |
|-----------|---------|-------|
| torch | 2.5.0a0+872d972e41.nv24.08 | NVIDIA JP 6.1 wheel |
| torchvision | 0.20.0a0+afc54f7 | Built from source |
| ultralytics | 8.3.x | Installed with --no-deps |
| TensorRT Engine | yolov8n.engine | ~53 FPS |

## Troubleshooting

### CUDA shows False after installing something

Something overwrote your torch. Fix:

```bash
pip3 uninstall torch torchvision -y
pip3 install --no-cache \
  https://developer.download.nvidia.com/compute/redist/jp/v61/pytorch/torch-2.5.0a0+872d972e41.nv24.08.17622132-cp310-cp310-linux_aarch64.whl
cd /tmp/vision && python3 setup.py install --user
```

### "operator torchvision::nms does not exist"

torchvision version mismatch. You need torchvision 0.20.x for torch 2.5.x:

```bash
pip3 uninstall torchvision -y
cd /tmp
rm -rf vision
git clone --branch v0.20.0 https://github.com/pytorch/vision.git
cd vision && python3 setup.py install --user
```

### libcudnn.so.8 not found

You're using a JetPack 6.0 wheel on JetPack 6.2. Use the JP 6.1 wheel instead (which supports cuDNN 9.x).

### TensorRT export fails

Make sure torch has CUDA before exporting:

```bash
python3 -c "import torch; assert torch.cuda.is_available(), 'CUDA not available!'"
```

## Version Compatibility Matrix

| PyTorch | torchvision | JetPack | cuDNN |
|---------|-------------|---------|-------|
| 2.4.0 | 0.19.x | 6.0 | 8.x |
| 2.5.0 | 0.20.x | 6.1/6.2 | 9.x |

Always match torch and torchvision versions. See: https://github.com/pytorch/vision#installation

## Key Takeaways

1. **Never** use `pip install torch` on Jetson - always use NVIDIA wheels
2. **Never** use `pip install torchvision` - always build from source
3. **Always** use `--no-deps` when installing packages that depend on torch
4. **Always** use constraints file for future pip operations
5. The build-from-source step only needs to be done once - after that, it's stable

## References

- [NVIDIA Jetson PyTorch](https://docs.nvidia.com/deeplearning/frameworks/install-pytorch-jetson-platform/index.html)
- [PyTorch-torchvision compatibility](https://github.com/pytorch/vision#installation)
- [JetPack 6.x Release Notes](https://developer.nvidia.com/embedded/jetpack)
- [NVIDIA Jetson Wheels](https://developer.download.nvidia.com/compute/redist/jp/v61/pytorch/)
