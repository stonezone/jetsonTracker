"""Frame sources for the tracker: RTSP (latest-frame reader thread) + mock.

The background reader thread is the key reliability detail: a backed-up RTSP
buffer is the classic cause of the camera lagging behind a fast subject. read()
always returns the MOST RECENT decoded frame, never a stale queued one.
"""

import threading
import time
from typing import Optional, Tuple

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


class FrameSource:
    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        raise NotImplementedError

    def release(self) -> None:
        pass

    @property
    def size(self) -> Tuple[int, int]:
        return (0, 0)


class RtspFrameSource(FrameSource):
    """RTSP via OpenCV/FFmpeg with a background thread holding only the latest frame."""

    def __init__(self, url: str, reconnect: bool = True):
        if cv2 is None:
            raise RuntimeError("opencv (cv2) not available")
        self.url = url
        self.reconnect = reconnect
        self._cap: Optional["cv2.VideoCapture"] = None
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._ok = False
        self._running = True
        self._open()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _open(self) -> None:
        self._cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        try:
            self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

    def _loop(self) -> None:
        while self._running:
            if self._cap is None or not self._cap.isOpened():
                if not self.reconnect:
                    break
                time.sleep(0.5)
                self._open()
                continue
            ok, frame = self._cap.read()
            if not ok:
                with self._lock:
                    self._ok = False
                if self.reconnect:
                    self._cap.release()
                    self._cap = None
                    time.sleep(0.3)
                continue
            with self._lock:
                self._frame = frame
                self._ok = True

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        with self._lock:
            if self._frame is None:
                return False, None
            return self._ok, self._frame.copy()

    def release(self) -> None:
        self._running = False
        if self._t.is_alive():
            self._t.join(timeout=1.0)
        if self._cap is not None:
            self._cap.release()

    @property
    def size(self) -> Tuple[int, int]:
        with self._lock:
            if self._frame is None:
                return (0, 0)
            h, w = self._frame.shape[:2]
            return (w, h)


class MockFrameSource(FrameSource):
    """Synthetic frames (a person-ish bar) for offline YOLO/loop tests."""

    def __init__(self, w: int = 640, h: int = 360):
        self._w, self._h = w, h

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        img = np.zeros((self._h, self._w, 3), dtype=np.uint8)
        if cv2 is not None:
            cx, cy = self._w // 2, self._h // 2
            cv2.rectangle(img, (cx - 20, cy - 60), (cx + 20, cy + 60), (200, 200, 200), -1)
        return True, img

    @property
    def size(self) -> Tuple[int, int]:
        return (self._w, self._h)


def make_frame_source(kind: str, url: str = "", **kw) -> FrameSource:
    if kind == "rtsp":
        return RtspFrameSource(url, **kw)
    if kind == "mock":
        return MockFrameSource(**kw)
    raise ValueError(f"unknown frame source: {kind!r}")
