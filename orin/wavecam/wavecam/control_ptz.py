"""PTZ dispatcher and deadman timer management for the WaveCam control API.

Moved from control_api.py.  PtzDispatcher owns all manual owner-gate logic
and the deadman timers for pan/tilt and zoom.  It receives the pipeline and
a bump_revision callable so that timer expiry paths can notify callers of
state changes without reaching back into ControlApiAdapter.
"""
from __future__ import annotations

import threading
from typing import Callable

from .control_snapshots import map_axis, zoom_speed
from .ptz_owner import AUTONOMOUS, IDLE
from .ptz_visca import PAN_STOP, TILT_STOP

HOME_ZOOM_WIDE_DEADMAN_MS = 4000


class PtzDispatcher:
    """Owns manual PTZ ownership gate and deadman timers."""

    def __init__(self, pipeline, bump_revision: Callable[[], None]) -> None:
        self.pipeline = pipeline
        self._bump_revision = bump_revision
        self._lock = threading.RLock()
        self._manual_deadman: threading.Timer | None = None
        self._zoom_deadman: threading.Timer | None = None
        self._manual_deadman_generation = 0
        self._zoom_deadman_generation = 0
        self._manual_pan_tilt_active = False
        self._restore_owner_after_manual: str | None = None

    def reset_restore_owner(self) -> None:
        """Clear the saved owner so resume/restart flows don't accidentally re-enter."""
        with self._lock:
            self._restore_owner_after_manual = None

    def claim_manual(self, takeover: bool = False) -> bool:
        with self._lock:
            if self.pipeline.owner.request("manual"):
                return True
            current_owner = self.pipeline.owner.owner
            if not takeover or current_owner not in AUTONOMOUS:
                return False
            self.pipeline.ptz.stop()
            self.pipeline.ptz.zoom("stop")
            if not self.pipeline.owner.release(current_owner):
                return False
            self._restore_owner_after_manual = current_owner
            return self.pipeline.owner.request("manual")

    def release_manual_owner(self, restore_autonomous: bool = True) -> None:
        with self._lock:
            released = self.pipeline.owner.release("manual")
            restore_owner = self._restore_owner_after_manual
            self._restore_owner_after_manual = None
            self._manual_pan_tilt_active = False
            if (
                released
                and restore_autonomous
                and restore_owner in AUTONOMOUS
                and not self.pipeline.owner.killed
            ):
                self.pipeline.owner.request(restore_owner)

    def start_autonomous(self, owner: str) -> bool:
        with self._lock:
            self.cancel_manual_deadman()
            self.cancel_zoom_deadman()
            self.pipeline.ptz.stop()
            self.pipeline.ptz.zoom("stop")
            self._restore_owner_after_manual = None
            self._manual_pan_tilt_active = False
            current_owner = self.pipeline.owner.owner
            if current_owner != IDLE:
                self.pipeline.owner.release(current_owner)
            if not self.pipeline.owner.request(owner):
                return False
            self.pipeline.state.set_status(killed=False, state="SEARCHING")
            return True

    def stop_ptz(self, hold: bool = True) -> None:
        with self._lock:
            self.cancel_manual_deadman()
            self.cancel_zoom_deadman()
            self._manual_pan_tilt_active = False
            self.pipeline.ptz.stop()
            self.pipeline.ptz.zoom("stop")
            if hold:
                self.hold_manual_owner()
            elif self.pipeline.owner.owner == "manual":
                self.release_manual_owner()

    def home_ptz(self) -> None:
        with self._lock:
            self.cancel_manual_deadman()
            self.cancel_zoom_deadman()
            self._manual_pan_tilt_active = False
            self.pipeline.ptz.stop()
            self.pipeline.ptz.zoom("stop")
            self.pipeline.ptz.home()
            self.pipeline.ptz.zoom(
                "wide",
                int(getattr(self.pipeline.cfg.ptz, "zoom_max_speed", 5)),
            )
            self.schedule_zoom_deadman(HOME_ZOOM_WIDE_DEADMAN_MS)

    def hold_manual_owner(self) -> None:
        with self._lock:
            current_owner = self.pipeline.owner.owner
            if current_owner == "manual":
                return
            if current_owner in AUTONOMOUS:
                self._restore_owner_after_manual = current_owner
                if not self.pipeline.owner.release(current_owner):
                    return
            self.pipeline.owner.request("manual")

    def send_manual_velocity(self, req) -> None:
        with self._lock:
            cfg = self.pipeline.cfg.ptz
            pan_dir, pan_speed = map_axis(req.pan, cfg, "pan")
            tilt_dir, tilt_speed = map_axis(req.tilt, cfg, "tilt")
            pan_tilt_active = pan_dir != PAN_STOP or tilt_dir != TILT_STOP

            if not pan_tilt_active and req.zoom == 0:
                self._manual_pan_tilt_active = False
                self.pipeline.ptz.stop()
                self.pipeline.ptz.zoom("stop")
                self.release_manual_owner()
                return

            if pan_tilt_active:
                self.pipeline.ptz.pan_tilt(pan_speed, tilt_speed, pan_dir, tilt_dir)
            else:
                self.pipeline.ptz.stop()
            self._manual_pan_tilt_active = pan_tilt_active
            self.send_manual_zoom(req.zoom, req.deadman_ms)

    def send_manual_zoom_velocity(self, zoom: float, deadman_ms: int = 800) -> None:
        with self._lock:
            if zoom == 0:
                self.pipeline.ptz.zoom("stop")
                return
            self.send_manual_zoom(zoom, deadman_ms)

    def send_manual_zoom(self, zoom: float, deadman_ms: int = 800) -> None:
        with self._lock:
            if zoom != 0:
                suppress = getattr(self.pipeline, "suppress_cinematic_zoom", None)
                if callable(suppress):
                    suppress(deadman_ms / 1000.0)
            if zoom > 0:
                self.pipeline.ptz.zoom("tele", zoom_speed(zoom))
            elif zoom < 0:
                self.pipeline.ptz.zoom("wide", zoom_speed(-zoom))

    @property
    def manual_pan_tilt_active(self) -> bool:
        with self._lock:
            return self._manual_pan_tilt_active

    def schedule_manual_deadman(self, deadman_ms: int) -> int:
        with self._lock:
            self.cancel_manual_deadman()
            self._manual_deadman_generation += 1
            generation = self._manual_deadman_generation
            timer = threading.Timer(
                deadman_ms / 1000.0,
                self.manual_deadman_expired,
                args=(generation,),
            )
            timer.daemon = True
            self._manual_deadman = timer
            timer.start()
            return generation

    def cancel_manual_deadman(self) -> None:
        with self._lock:
            self._manual_deadman_generation += 1
            if self._manual_deadman is not None:
                self._manual_deadman.cancel()
                self._manual_deadman = None

    def schedule_zoom_deadman(self, deadman_ms: int) -> int:
        with self._lock:
            self.cancel_zoom_deadman()
            self._zoom_deadman_generation += 1
            generation = self._zoom_deadman_generation
            timer = threading.Timer(
                deadman_ms / 1000.0,
                self.zoom_deadman_expired,
                args=(generation,),
            )
            timer.daemon = True
            self._zoom_deadman = timer
            timer.start()
            return generation

    def cancel_zoom_deadman(self) -> None:
        with self._lock:
            self._zoom_deadman_generation += 1
            if self._zoom_deadman is not None:
                self._zoom_deadman.cancel()
                self._zoom_deadman = None

    def zoom_deadman_expired(self, generation: int | None = None) -> None:
        with self._lock:
            if generation is not None and generation != self._zoom_deadman_generation:
                return
            self.pipeline.ptz.zoom("stop")
            self._zoom_deadman = None
            self._bump_revision()

    def manual_deadman_expired(self, generation: int | None = None) -> None:
        with self._lock:
            if generation is not None and generation != self._manual_deadman_generation:
                return
            self._manual_deadman = None
            if self.pipeline.owner.owner == "manual":
                self.pipeline.ptz.stop()
                self.pipeline.ptz.zoom("stop")
                self._manual_pan_tilt_active = False
                self.release_manual_owner()
                self._bump_revision()
