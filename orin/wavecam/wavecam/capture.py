"""
Frame grabber. Runs capture in a thread and always hands back the LATEST frame
(drops stale frames) — essential so the servo loop isn't chasing buffered lag.
Handles RTSP dropouts with reconnect.
"""
from __future__ import annotations
import threading
import time
from typing import Optional

import cv2
import numpy as np


def _gst_pipeline(url: str, codec: str) -> str:
    depay = "rtph265depay ! h265parse" if codec == "h265" else "rtph264depay ! h264parse"
    return (
        f"rtspsrc location={url} latency=50 ! {depay} ! nvv4l2decoder ! "
        f"nvvidconv ! video/x-raw,format=BGRx ! videoconvert ! "
        f"video/x-raw,format=BGR ! appsink drop=1 max-buffers=1 sync=false"
    )


class FrameGrabber(threading.Thread):
    def __init__(self, cfg):
        super().__init__(daemon=True)
        self.cfg = cfg
        self._latest: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._connected = False
        self._frames = 0

    def _open(self) -> Optional[cv2.VideoCapture]:
        src = self.cfg.source
        if isinstance(src, str) and src.startswith("rtsp") and self.cfg.use_gstreamer:
            cap = cv2.VideoCapture(_gst_pipeline(src, self.cfg.codec), cv2.CAP_GSTREAMER)
        else:
            cap = cv2.VideoCapture(src)
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # minimize ffmpeg buffering
            except Exception:
                pass
        if not cap or not cap.isOpened():
            if cap:
                cap.release()
            return None
        return cap

    def run(self) -> None:
        cap = None
        while not self._stop.is_set():
            if cap is None:
                cap = self._open()
                if cap is None:
                    self._connected = False
                    time.sleep(self.cfg.reconnect_sec)
                    continue
                self._connected = True
            ok, frame = cap.read()
            if not ok or frame is None:
                self._connected = False
                # Drop the stale frame so read() returns None on disconnect; the pipeline's
                # `if frame is None` guard then goes NO_VIDEO instead of running YOLO +
                # tracking on a frozen frame until RTSP reconnects (CAP-1).
                with self._lock:
                    self._latest = None
                cap.release()
                cap = None
                time.sleep(self.cfg.reconnect_sec)
                continue
            with self._lock:
                self._latest = frame
                self._frames += 1
        if cap:
            cap.release()

    def read(self) -> Optional[np.ndarray]:
        with self._lock:
            return None if self._latest is None else self._latest.copy()

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def frames(self) -> int:
        """Total frames captured. A wedged grabber returns the same non-None frame
        from read() while this stops advancing — the zombie-detection signal."""
        return self._frames

    def stop(self) -> None:
        self._stop.set()
