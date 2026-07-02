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
from .ptz_owner import AUTONOMOUS, CALIBRATE, IDLE
from .ptz_visca import PAN_STOP, TILT_STOP

HOME_ZOOM_WIDE_DEADMAN_MS = 4000
HOME_PAN_TILT_DEADMAN_MS = 8000


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
        # Explicit operator LOCK (STOP PTZ / hold_manual_owner). Unlike a transient velocity
        # grab, a held manual must survive zoom and must NOT auto-release on a deadman — only an
        # explicit release or start_autonomous clears it. Fixes: zoom dropping a STOP-PTZ lock.
        self._manual_held = False
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
            # Atomic handoff (SAFE-2): one locked release+grant so the arbiter
            # thread can't slip into the transient idle window and re-seize the
            # PTZ between release() and request("manual"). transition() refuses if
            # the owner moved underneath us, leaving ownership untouched (no restore
            # needed). Only overwrite the restore target if none is already staged.
            if not self.pipeline.owner.transition(current_owner, "manual"):
                return False
            if self._restore_owner_after_manual is None:
                self._restore_owner_after_manual = current_owner
            return True

    def claim_manual_from_calibrate(self) -> bool:
        """Take over from an active CALIBRATE session for a standalone capture.

        Runs the whole release-calibrate → claim-manual transition under the
        dispatcher lock (the restore-owner field must never be poked from outside)
        and stops PTZ before the capture samples encoders, mirroring claim_manual.
        On success owner=manual and the session is staged for restore by
        release_manual_owner; on failure the calibrate session is restored."""
        with self._lock:
            if self.pipeline.owner.owner != CALIBRATE:
                return False
            self.pipeline.ptz.stop()
            self.pipeline.ptz.zoom("stop")
            if not self.pipeline.owner.release(CALIBRATE):
                return False
            self._restore_owner_after_manual = CALIBRATE
            if self.pipeline.owner.request("manual"):
                return True
            # Could not claim manual after releasing calibrate — restore the session.
            self._restore_owner_after_manual = None
            if not self.pipeline.owner.killed:
                self.pipeline.owner.request(CALIBRATE)
            return False

    def release_manual_owner(self, restore_autonomous: bool = True) -> None:
        with self._lock:
            self._manual_held = False
            released = self.pipeline.owner.release("manual")
            restore_owner = self._restore_owner_after_manual
            self._restore_owner_after_manual = None
            self._manual_pan_tilt_active = False
            # Reset arbiter hysteresis so vision/gps must re-earn lock from
            # scratch after manual intervention. Prevents stale autonomous
            # owners from immediately fighting the operator.
            if hasattr(self.pipeline, "arbiter"):
                self.pipeline.arbiter.reset_vision_state()
            if released and restore_autonomous and not self.pipeline.owner.killed:
                # Restore calibrate session owner — session-scoped and must
                # survive standalone captures (heading/tilt/zoom/base-lock).
                # Autonomous owners (vision_follow/gps_tracker/testbed) are
                # left for the arbiter to re-decide in the next frame.
                if restore_owner == CALIBRATE:
                    self.pipeline.owner.request(CALIBRATE)

    def start_autonomous(self, owner: str) -> bool:
        with self._lock:
            self.cancel_manual_deadman()
            self.cancel_zoom_deadman()
            self.pipeline.ptz.stop()
            self.pipeline.ptz.zoom("stop")
            self._restore_owner_after_manual = None
            self._manual_pan_tilt_active = False
            self._manual_held = False
            current_owner = self.pipeline.owner.owner
            if current_owner == CALIBRATE:
                return False
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
            self.schedule_manual_deadman(HOME_PAN_TILT_DEADMAN_MS)

    def hold_manual_owner(self) -> None:
        with self._lock:
            current_owner = self.pipeline.owner.owner
            if current_owner == "manual":
                self._manual_held = True
                return
            if current_owner in AUTONOMOUS:
                # Atomic handoff (SAFE-2): close the release→request gap the arbiter
                # could exploit to re-seize PTZ and silently lose the operator's hold.
                if not self.pipeline.owner.transition(current_owner, "manual"):
                    return
                if self._restore_owner_after_manual is None:
                    self._restore_owner_after_manual = current_owner
                self._manual_held = True
                return
            if self.pipeline.owner.request("manual"):
                self._manual_held = True

    def send_manual_velocity(self, req) -> None:
        with self._lock:
            # Re-check KILL under the lock before issuing VISCA: a KILL that lands
            # after claim_manual succeeded but before these bytes go out would
            # otherwise move the camera for one frame (SAFE-3).
            if self.pipeline.owner.killed:
                self.pipeline.ptz.stop()
                return
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
            if self.pipeline.owner.killed:   # SAFE-3: no zoom motion after KILL
                self.pipeline.ptz.zoom("stop")
                return
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

    @property
    def manual_held(self) -> bool:
        # Self-correcting: an operator lock only means something while manual actually owns PTZ.
        with self._lock:
            return self._manual_held and self.pipeline.owner.owner == "manual"

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
        # R10 (audit round-2, ABBA deadlock): this used to call self._bump_revision()
        # (which reaches into the adapter's own lock) WHILE STILL HOLDING self._lock.
        # Meanwhile control_system.SystemManager.prepare_for_restart (and
        # control_calibration.CalibrationManager.start_session's takeover path) hold
        # the adapter lock and then call into THIS lock via cancel_zoom_deadman/
        # cancel_manual_deadman -- an A->B / B->A ABBA pair that could wedge the whole
        # API, including /safety/kill. Fix: decide under self._lock, then bump the
        # revision AFTER releasing it, so this side of the pair never holds both locks
        # at once.
        with self._lock:
            if generation is not None and generation != self._zoom_deadman_generation:
                return
            self.pipeline.ptz.zoom("stop")
            self._zoom_deadman = None
        self._bump_revision()

    def manual_deadman_expired(self, generation: int | None = None) -> None:
        # R10 (audit round-2): same ABBA hazard as zoom_deadman_expired above -- move
        # self._bump_revision() to after self._lock is released. `acted` tracks
        # whether we actually changed anything so a no-op expiry (owner already
        # non-manual) still skips the revision bump, matching prior behavior.
        acted = False
        with self._lock:
            if generation is not None and generation != self._manual_deadman_generation:
                return
            self._manual_deadman = None
            # A held manual (operator LOCK) never auto-releases on a deadman.
            if self._manual_held:
                return
            if self.pipeline.owner.owner == "manual":
                self.pipeline.ptz.stop()
                self.pipeline.ptz.zoom("stop")
                self._manual_pan_tilt_active = False
                self.release_manual_owner()
                acted = True
        if acted:
            self._bump_revision()
