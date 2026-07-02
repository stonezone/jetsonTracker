"""Audit 2026-07-01 Wave-1 loop-path fixes, driven through the REAL _run loop
with a scripted frame grabber (deterministic: one read() per iteration).

  M5  TTL-cached YOLO boxes are NOT reused while a moving pan/tilt command is
      active (image-frame boxes shift under the camera), but still reused when
      the camera is holding still.
  M7  annotate+imencode are skipped with zero MJPEG clients and resume when a
      client registers; the web layer's counter drives it.
  M2  calibration_status() (control-API lock + full dict build) is called at
      <=1 Hz from the loop, not per frame.
"""
from __future__ import annotations

import threading
import time
import types

import numpy as np

from wavecam.fusion import FusionResult
from wavecam.pipeline import Pipeline

W, H = 640, 360
FRAME = np.zeros((H, W, 3), dtype=np.uint8)
BOX = types.SimpleNamespace(x1=100, y1=100, x2=140, y2=190, conf=0.8,
                            xywh=(100, 100, 40, 90), center=(120.0, 145.0),
                            track_id=None)


class _RecordingPtz:
    def __init__(self):
        self.calls = []

    def pan_tilt(self, *a):
        self.calls.append(("pan_tilt",) + a)

    def stop(self):
        self.calls.append("stop")

    def zoom(self, direction, speed=0):
        self.calls.append(("zoom", direction, speed))


class _ScriptedGrab:
    """Feeds n_frames real frames then stops the pipeline — read() is called
    exactly once per loop iteration, so tests are frame-deterministic."""

    def __init__(self, n_frames):
        self.n_frames = n_frames
        self.n = 0
        self.frames = 0
        self.connected = True
        self.pipe = None

    def start(self):
        pass

    def stop(self):
        pass

    def read(self):
        self.n += 1
        if self.n > self.n_frames:
            self.pipe._stop_evt.set()  # R3 (audit round-2): renamed from _stop
            return None
        self.frames += 1
        return FRAME


def _cfg():
    return types.SimpleNamespace(
        camera=types.SimpleNamespace(source=0, use_gstreamer=False, codec="h264",
                                     reconnect_sec=1.0),
        color=types.SimpleNamespace(enabled=False),
        detector=types.SimpleNamespace(enabled=True, every_n=100000,
                                       box_ttl_sec=5.0),
        fusion=types.SimpleNamespace(
            lock_threshold=0.6, unlock_threshold=0.35, require_person=False,
            match_dist=120, person_aim_x=0.5, person_aim_y=0.5, ema_alpha=0.5,
            lost_grace_sec=0.8, gps_boost=0.2, gps_boost_radius_frac=0.25),
        ptz=types.SimpleNamespace(
            enabled=True, command_min_interval=0.0, stop_resend_interval=0.25,
            cinematic_zoom_enabled=False, zoom_target_frac=0.35,
            zoom_deadband=0.02, zoom_max_speed=4, invert_pan=False,
            invert_tilt=False, deadzone=0.1, max_pan_speed=12,
            max_tilt_speed=9, min_speed=1, ff_gain=0.0, ff_deadzone_mult=1.5),
        gps=types.SimpleNamespace(lock_frames=1, grace_sec=1.0,
                                  stale_threshold_sec=10.0, drive_stale_sec=8.0,
                                  max_pan_speed=4, max_tilt_speed=3,
                                  drive_zoom=False),
        web=types.SimpleNamespace(jpeg_quality=80, show_hud=True),
        loop=types.SimpleNamespace(target_fps=200, log_every_sec=100),
    )


def _loop_pipe(n_frames, target_xy):
    """Real Pipeline whose fusion stub reports a locked target at target_xy and
    records the persons list each frame."""
    pipe = Pipeline(_cfg(), _RecordingPtz(), detector_factory=lambda:
                    types.SimpleNamespace(detect=lambda f: []))
    grab = _ScriptedGrab(n_frames)
    grab.pipe = pipe
    pipe.grab = grab
    pipe.ptz_state = types.SimpleNamespace(
        start=lambda: None, stop=lambda: None, is_alive=lambda: True,
        latest=lambda: ((0, 0), 0.05), latest_zoom=lambda: (0, 0.1))

    persons_seen = []

    def _fusion_update(blobs, persons, gps_cue_px=None):
        persons_seen.append(persons)
        return FusionResult(target_xy=target_xy, bbox=BOX.xywh,
                            person_bbox=BOX.xywh, conf=0.9, locked=True,
                            state="TRACKING", has_color=True, has_person=True,
                            matched=True)

    pipe.fusion = types.SimpleNamespace(update=_fusion_update)
    # pre-seed the YOLO box cache (every_n is huge, so YOLO never re-runs)
    pipe._last_boxes = [BOX]
    pipe._last_boxes_time = time.time()
    return pipe, persons_seen


def _run(pipe):
    t = threading.Thread(target=pipe.run, daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "pipeline loop did not stop"


def test_m5_cached_boxes_skipped_while_panning():
    """Locked target far right -> a moving pan command is issued every frame;
    from the second frame on, the stale cached boxes must NOT be reused."""
    pipe, persons_seen = _loop_pipe(n_frames=6, target_xy=(600.0, 180.0))
    _run(pipe)
    assert len(persons_seen) == 6
    # frame 1: no cmd sent yet since the boxes were cached -> reuse OK
    assert persons_seen[0] == [BOX]
    # frames 2+: a moving cmd postdates the cache -> reuse suppressed
    assert all(p is None for p in persons_seen[1:]), \
        "image-frame boxes must not be reused while the camera pans"
    assert any(c[0] == "pan_tilt" for c in pipe.ptz.calls
               if isinstance(c, tuple)), "setup: the servo must actually pan"


def test_m5_cached_boxes_reused_while_still():
    """Locked target at frame center -> servo STOPs; cached boxes stay usable
    for the whole TTL."""
    pipe, persons_seen = _loop_pipe(n_frames=6, target_xy=(W / 2.0, H / 2.0))
    _run(pipe)
    assert all(p == [BOX] for p in persons_seen), \
        "a still camera must keep reusing cached boxes within the TTL"


def test_m7_no_encode_without_preview_clients_then_resumes():
    pipe, _ = _loop_pipe(n_frames=4, target_xy=(W / 2.0, H / 2.0))
    _run(pipe)
    assert pipe.state.get_jpeg() is None, \
        "with zero MJPEG clients the loop must not encode JPEGs"

    pipe2, _ = _loop_pipe(n_frames=4, target_xy=(W / 2.0, H / 2.0))
    pipe2.state.preview_client_add()          # what web._frames does on connect
    _run(pipe2)
    assert pipe2.state.get_jpeg() is not None, \
        "with a connected client the annotated JPEG must be produced"
    pipe2.state.preview_client_remove()
    assert pipe2.state.preview_client_count() == 0


def test_m2_calibration_status_called_at_most_once_per_second():
    pipe, _ = _loop_pipe(n_frames=10, target_xy=(W / 2.0, H / 2.0))
    calls = []
    pipe.calibration_status = lambda: calls.append(1) or {}
    _run(pipe)
    # 10 frames at 200 fps target is well under a second -> exactly one refresh
    assert len(calls) == 1, \
        f"calibration_status must be time-gated to <=1 Hz, got {len(calls)} calls"
