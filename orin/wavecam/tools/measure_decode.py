#!/usr/bin/env python3
"""Decode-latency bench tool. Run ON the rig; not executed in CI.

Measures per-frame inter-frame gap, FPS, and wall-clock latency for two
decode paths on the RTSP sub-stream:

  (a) cv2.VideoCapture (software decode via ffmpeg)
  (b) GStreamer nvv4l2decoder pipeline (hardware decoder, Jetson-only)

Reuses capture.py's _gst_pipeline() builder — does NOT modify capture.py.

Usage:
  python3 tools/measure_decode.py --url rtsp://192.168.100.88:554/2
  python3 tools/measure_decode.py --url rtsp://192.168.100.88:554/2 --path b
  python3 tools/measure_decode.py --url rtsp://192.168.100.88:554/2 --frames 100
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from typing import List, Optional


# ---------------------------------------------------------------------------
# Decode backends
# ---------------------------------------------------------------------------

def _open_cv2(url: str):
    """Open the RTSP stream using cv2 VideoCapture (ffmpeg software decode)."""
    import cv2
    cap = cv2.VideoCapture(url)
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass
    if not cap.isOpened():
        raise RuntimeError(f"cv2.VideoCapture could not open {url}")
    return cap


def _open_gst(url: str, codec: str = "h264"):
    """Open the RTSP stream using the GStreamer nvv4l2decoder pipeline.

    Reuses capture.py's _gst_pipeline() builder unchanged.
    """
    import cv2
    # Import the pipeline string builder from capture.py (read-only, no modification)
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from wavecam.capture import _gst_pipeline
    pipeline = _gst_pipeline(url, codec)
    cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
    if not cap.isOpened():
        raise RuntimeError(f"GStreamer pipeline could not open {url}\n  pipeline: {pipeline}")
    return cap


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

def _grab_frames(cap, n_frames: int) -> List[float]:
    """Grab n_frames, return list of inter-frame wall-clock gaps in seconds.

    The first frame is a warm-up and its gap is discarded (pipeline startup
    latency is not representative of steady-state decode).
    """
    import cv2
    gaps: List[float] = []
    t_prev: Optional[float] = None
    captured = 0
    # Warm-up: read until we get the first valid frame
    for _ in range(30):
        ok, _ = cap.read()
        if ok:
            t_prev = time.perf_counter()
            captured = 1
            break
    if t_prev is None:
        raise RuntimeError("Could not capture a warm-up frame")

    while captured < n_frames:
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError(f"Frame read failed at frame {captured}")
        t_now = time.perf_counter()
        gaps.append(t_now - t_prev)
        t_prev = t_now
        captured += 1

    return gaps


def _report(label: str, gaps: List[float]) -> None:
    """Print a one-block stats report for a set of inter-frame gaps."""
    if not gaps:
        print(f"\n[{label}] no gaps measured")
        return
    total = sum(gaps)
    fps = len(gaps) / total if total > 0 else 0.0
    mean_ms = statistics.mean(gaps) * 1000
    stdev_ms = statistics.stdev(gaps) * 1000 if len(gaps) > 1 else 0.0
    sorted_gaps = sorted(gaps)
    p95_ms = sorted_gaps[int(len(sorted_gaps) * 0.95)] * 1000
    p99_ms = sorted_gaps[int(len(sorted_gaps) * 0.99)] * 1000
    # Wall-clock staleness proxy: p95 gap − expected period
    expected_ms = 1000.0 / fps if fps > 0 else 0.0
    staleness_ms = p95_ms - expected_ms

    print(f"\n{'='*55}")
    print(f"  Path : {label}")
    print(f"  Frames measured : {len(gaps)}")
    print(f"  FPS  : {fps:.1f}")
    print(f"  Mean gap   : {mean_ms:.1f} ms")
    print(f"  StdDev gap : {stdev_ms:.1f} ms")
    print(f"  p95 gap    : {p95_ms:.1f} ms")
    print(f"  p99 gap    : {p99_ms:.1f} ms")
    print(f"  Staleness proxy (p95 - expected) : {staleness_ms:.1f} ms")
    print(f"{'='*55}")


def run_bench(url: str, n_frames: int, paths: List[str], codec: str = "h264") -> None:
    """Run the decode latency bench on the requested paths."""
    for path in paths:
        label = f"cv2 software ({url})" if path == "a" else f"GStreamer nvv4l2 ({url})"
        print(f"\n[measure_decode] opening {label} ...")
        try:
            if path == "a":
                cap = _open_cv2(url)
            else:
                cap = _open_gst(url, codec)
            print(f"[measure_decode] grabbing {n_frames} frames ...")
            gaps = _grab_frames(cap, n_frames)
            cap.release()
            _report(label, gaps)
        except RuntimeError as exc:
            print(f"\n[measure_decode] ERROR ({label}): {exc}", file=sys.stderr)
        except Exception as exc:
            print(f"\n[measure_decode] UNEXPECTED ERROR ({label}): {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Decode-latency bench: measure inter-frame gap for cv2 and GStreamer paths.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--url", default="rtsp://192.168.100.88:554/2",
                   help="RTSP sub-stream URL (default: Prisual sub-stream)")
    p.add_argument("--frames", type=int, default=300,
                   help="Number of frames to grab per path (default: 300)")
    p.add_argument("--path", choices=["a", "b", "both"], default="both",
                   help="Decode path: a=cv2, b=GStreamer, both (default: both)")
    p.add_argument("--codec", choices=["h264", "h265"], default="h264",
                   help="Video codec for GStreamer depayloader (default: h264)")
    return p


def main() -> None:
    args = build_parser().parse_args()
    paths = ["a", "b"] if args.path == "both" else [args.path]
    run_bench(args.url, args.frames, paths, args.codec)


if __name__ == "__main__":
    main()
