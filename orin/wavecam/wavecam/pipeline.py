"""
Pipeline: the deterministic loop. capture -> color + (throttled) YOLO -> fusion
-> visual servo -> VISCA command. Holds thread-safe shared state (latest JPEG,
status, live-mutable tunables, kill latch) that the web layer reads/writes.
"""
from __future__ import annotations
import os
import threading
import time
from typing import Optional

import cv2

from .capture import FrameGrabber
from .color_detector import ColorDetector
from .controller import VisualServo, STOP_CMD, PtzAbsoluteCommand
from .fusion import Fusion
from .gps_geo import GeoPoint
from .gps_pointing import compute_target, ZoomCurve
from .overlay import annotate
from .ptz_owner import PtzOwner
from .tracking_arbiter import TrackingArbiter

DEFAULT_STOP_RESEND_INTERVAL_SEC = 0.25


class SharedState:
    def __init__(self):
        self.lock = threading.Lock()
        self.jpeg: Optional[bytes] = None
        self.status: dict = {"state": "INIT", "fps": 0.0, "connected": False, "killed": False}
        self.killed = False
        self.show_mask = True
        self.show_hud = True

    def set_jpeg(self, b: bytes):
        with self.lock:
            self.jpeg = b

    def get_jpeg(self) -> Optional[bytes]:
        with self.lock:
            return self.jpeg

    def set_status(self, **kw):
        with self.lock:
            self.status.update(kw)

    def get_status(self) -> dict:
        with self.lock:
            return dict(self.status)


class Pipeline(threading.Thread):
    def __init__(self, cfg, ptz, detector_factory):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.ptz = ptz
        self.state = SharedState()
        self.state.show_hud = bool(getattr(cfg.web, "show_hud", True))

        self.grab = FrameGrabber(cfg.camera)
        self.color = ColorDetector(cfg.color) if cfg.color.enabled else None
        self.fusion = Fusion(cfg.fusion)
        self.servo = VisualServo(cfg.ptz)
        self.owner = PtzOwner()       # single PTZ writer + sticky KILL latch
        # Systemd restarts should come up stationary. Manual run.py launches do
        # not set this env var, so bench behavior stays unchanged.
        self.start_paused = bool(os.environ.get("WAVECAM_START_PAUSED"))

        # YOLO is optional + lazily built (so missing torch doesn't kill the rig)
        self.detector = None
        if cfg.detector.enabled:
            try:
                self.detector = detector_factory()
            except Exception as e:  # pragma: no cover - depends on torch/ultralytics
                print(f"[pipeline] YOLO disabled (load failed): {e}")

        # P1: GPS coarse-pointing handoff
        self.arbiter = TrackingArbiter(
            lock_frames=getattr(cfg.gps, "lock_frames", 5),
            grace_sec=getattr(cfg.gps, "grace_sec", 1.0),
            max_gps_age_sec=getattr(cfg.gps, "stale_threshold_sec", 10.0),
        )
        # CameraPose — loaded by calibration endpoint; uncalibrated by default
        from .camera_pose import CameraPose
        self.pose = CameraPose()
        # GPS source — set by run.py after connect(); None if GPS disabled
        self.gps = None
        # Health registry — every loop beat()s each component; /health exposes staleness
        from .health import HealthRegistry
        self.health = HealthRegistry()
        # Event ring — records lock/owner/gps/kill transitions for /events
        from .events import EventRing
        self.events = EventRing(maxlen=500)
        self._prev_locked: Optional[bool] = None
        self._prev_gps_viable: Optional[bool] = None
        self._last_abs_cmd_key = None
        self._last_abs_cmd_time = 0.0
        self._arbiter_state = "idle"

        self._stop = threading.Event()
        self._last_cmd_key = None
        self._last_cmd_time = 0.0
        self._last_zoom_key = None
        self._last_zoom_time = 0.0
        self._cinematic_zoom_suppressed_until = 0.0
        self._last_boxes = []
        self._last_boxes_time = 0.0
        self._frame_i = 0

        # P3: estimator shadow wiring — instantiated lazily via _init_estimator()
        # once the FOV curve is available (G2 gate).
        self.estimator: Optional["TargetEstimator"] = None
        self._shadow_writer: Optional["ShadowWriter"] = None
        self._est_tick = 0
        self._est_active_shadow: bool = False

    def kill(self, on: bool = True):
        self.state.killed = on
        if on:
            self.state.set_status(killed=True, state="KILLED")
            self.owner.kill()                  # sticky latch + owner -> idle
            if self.cfg.ptz.enabled:
                self.ptz.stop()                # immediate pan/tilt stop
                self.ptz.zoom("stop")          # + zoom stop
            self.events.record("kill", "killed")
        else:
            self.state.set_status(killed=False, state="SEARCHING")
            self.owner.resume()
            if self.cfg.ptz.enabled:
                self.owner.request("testbed")  # re-acquire on RESUME
            self.events.record("kill", "resumed")

    def _send_cmd(self, cmd):
        """Rate-limited, de-duped VISCA send. STOP always allowed through."""
        if not self.cfg.ptz.enabled:
            return
        now = time.time()
        key = cmd.key()
        changed = key != self._last_cmd_key
        due = (now - self._last_cmd_time) >= self.cfg.ptz.command_min_interval
        stop_due = (
            cmd.is_stop
            and (now - self._last_cmd_time)
            >= getattr(self.cfg.ptz, "stop_resend_interval", DEFAULT_STOP_RESEND_INTERVAL_SEC)
        )
        if changed or (due and not cmd.is_stop) or stop_due:
            if cmd.is_stop:
                self.ptz.stop()
            else:
                self.ptz.pan_tilt(cmd.pan_speed, cmd.tilt_speed, cmd.pan_dir, cmd.tilt_dir)
            self._last_cmd_key = key
            self._last_cmd_time = now

    def suppress_cinematic_zoom(self, seconds: float) -> None:
        """Suppress auto-zoom briefly after a manual zoom nudge.

        This deliberately does not touch PTZ owner state, so pan/tilt tracking can
        continue while the operator rides zoom.
        """
        until = time.time() + max(0.0, seconds)
        self._cinematic_zoom_suppressed_until = max(
            getattr(self, "_cinematic_zoom_suppressed_until", 0.0),
            until,
        )

    def _cinematic_zoom_suppressed(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return now < getattr(self, "_cinematic_zoom_suppressed_until", 0.0)

    def _maybe_init_estimator(self) -> None:
        """G2 gate check, callable repeatedly until the estimator exists.

        run() starts before ControlApiAdapter assigns pipeline._store, so a
        start-time-only check can never see the FOV curve on the rig (nor can
        it see a curve calibrated mid-session). The main loop re-invokes this
        until it succeeds.
        """
        est_cfg = getattr(self.cfg, "estimator", None)
        if not (est_cfg and getattr(est_cfg, "enabled", False)) or self.estimator is not None:
            return
        fov_curve = getattr(getattr(self, "_store", None), "fov_curve", [])
        if not fov_curve:
            return
        try:
            self._init_estimator(fov_curve)
            if self.estimator is not None:
                log_dir = getattr(self.cfg, "shadow_log_dir", "/data/shadow")
                session_id = time.strftime("%Y%m%dT%H%M%S")
                from .shadow_writer import ShadowWriter
                self._shadow_writer = ShadowWriter(log_dir=log_dir, session_id=session_id)
                print(f"[pipeline] estimator shadow started, log_dir={log_dir}")
        except Exception as e:
            # Shadow is observability, never control: a writer/init failure
            # (e.g. unwritable log_dir) must not take the vision loop with it.
            # 2026-06-11: PermissionError on /data/shadow killed the pipeline
            # thread at boot while /status kept answering — a zombie rig.
            self.estimator = None
            self._shadow_writer = None
            self._est_active_shadow = False
            print(f"[pipeline] estimator shadow DISABLED (init failed: {e})")

    def _init_estimator(self, fov_curve: list) -> None:
        """Create/replace the estimator once the FOV curve is populated (G2 gate).

        Called from run() when the curve first becomes non-empty, or from tests
        directly. No-ops when estimator.enabled is False or cfg.estimator is absent.
        """
        from .estimator import TargetEstimator
        est_cfg = getattr(self.cfg, "estimator", None)
        if est_cfg is None or not getattr(est_cfg, "enabled", False):
            return
        try:
            self.estimator = TargetEstimator(
                cfg=est_cfg, gps_cfg=self.cfg.gps,
                pose=self.pose, fov_curve=fov_curve,
            )
            self._est_active_shadow = bool(getattr(est_cfg, "shadow", True))
        except RuntimeError as e:
            print(f"[pipeline] estimator not started: {e}")
            self.estimator = None
            self._est_active_shadow = False

    def _send_zoom(self, direction: str, speed: int = 0) -> None:
        """Rate-limited, de-duped zoom send. Separate from pan/tilt de-dupe."""
        if not self.cfg.ptz.enabled:
            return
        direction = direction if direction in ("tele", "wide") and speed > 0 else "stop"
        speed = int(speed) if direction != "stop" else 0
        now = time.time()
        key = (direction, speed)
        changed = key != getattr(self, "_last_zoom_key", None)
        due = (now - getattr(self, "_last_zoom_time", 0.0)) >= self.cfg.ptz.command_min_interval
        stop_due = (
            direction == "stop"
            and (now - getattr(self, "_last_zoom_time", 0.0))
            >= getattr(self.cfg.ptz, "stop_resend_interval", DEFAULT_STOP_RESEND_INTERVAL_SEC)
        )
        if changed or (due and direction != "stop") or stop_due:
            self.ptz.zoom(direction, speed)
            self._last_zoom_key = key
            self._last_zoom_time = now

    def _auto_zoom_is_moving(self) -> bool:
        return getattr(self, "_last_zoom_key", None) not in (None, ("stop", 0))

    def _send_absolute_cmd(self, cmd):
        """Rate-limited, de-duped absolute pan/tilt/zoom for GPS mode."""
        if not self.cfg.ptz.enabled:
            return
        now = time.time()
        key = cmd.key()
        changed = key != self._last_abs_cmd_key
        due = (now - self._last_abs_cmd_time) >= self.cfg.ptz.command_min_interval
        if changed or due:
            gps_cfg = self.cfg.gps
            self.ptz.pan_tilt_absolute(
                cmd.pan_enc, cmd.tilt_enc,
                pan_speed=getattr(gps_cfg, "max_pan_speed", 4),
                tilt_speed=getattr(gps_cfg, "max_tilt_speed", 3),
            )
            if cmd.zoom_enc is not None:
                self.ptz.zoom_absolute(cmd.zoom_enc)
            self._last_abs_cmd_key = key
            self._last_abs_cmd_time = now

    def _gps_pointing_cmd(self, fix):
        """Compute a GPS absolute pointing command from a cached fix, or None.

        Uses the latched pose position when available (tripod is stationary once
        locked); falls back to live get_camera_position() for bench/manual flows."""
        if fix is None or not self.pose.calibrated:
            return None
        if self.pose.has_base:
            base = GeoPoint(lat=self.pose.lat, lon=self.pose.lon, alt_m=self.pose.alt_m)
        elif self.gps is not None:
            cam = self.gps.get_camera_position()
            if cam is None:
                return None
            base = GeoPoint(lat=cam[0], lon=cam[1], alt_m=cam[2] if len(cam) > 2 else 0.0)
        else:
            return None
        gps_cfg = self.cfg.gps
        drive_zoom = getattr(gps_cfg, "drive_zoom", False)
        target = GeoPoint(lat=fix.lat, lon=fix.lon,
                          speed_mps=fix.speed, course_deg=fix.course)
        pt = compute_target(base, target, self.pose, lead_s=0.65,
                            zoom=ZoomCurve() if drive_zoom else None)
        return PtzAbsoluteCommand(
            pan_enc=int(pt.pan_enc), tilt_enc=int(pt.tilt_enc),
            zoom_enc=int(pt.zoom_enc) if pt.zoom_enc is not None else None,
        )

    def _maybe_send_cinematic_zoom(self, fr, frame_h: int) -> str | None:
        if not self.cfg.ptz.enabled:
            return None
        if not bool(getattr(self.cfg.ptz, "cinematic_zoom_enabled", False)):
            if self._auto_zoom_is_moving():
                self._send_zoom("stop")
                return "hold"
            return None
        if self.owner.owner not in ("testbed", "vision_follow"):
            return None
        if self._cinematic_zoom_suppressed():
            if self._auto_zoom_is_moving():
                self._send_zoom("stop")
            return "manual_override"
        if not getattr(fr, "locked", False):
            if self._auto_zoom_is_moving():
                self._send_zoom("stop")
                return "hold"
            return None

        direction, speed = self.servo.compute_zoom(getattr(fr, "person_bbox", None), frame_h)
        self._send_zoom(direction, speed)
        return "hold" if direction == "stop" else f"{direction}{speed}"

    def run(self):
        try:
            self._run()
        finally:
            self._shutdown()

    def _run(self):
        self.grab.start()
        if self.cfg.ptz.enabled and not self.start_paused:
            self.owner.request("testbed")

        self._maybe_init_estimator()
        period = 1.0 / max(1.0, self.cfg.loop.target_fps)
        t_fps = time.time()
        n_fps = 0
        fps = 0.0
        t_log = time.time()

        while not self._stop.is_set():
            t0 = time.time()
            frame = self.grab.read()
            if frame is None:
                self.state.set_status(state="NO_VIDEO", connected=self.grab.connected)
                time.sleep(0.1)
                continue

            h, w = frame.shape[:2]
            if self.estimator is None and self._frame_i % 120 == 0:
                self._maybe_init_estimator()
            self.health.beat("capture", {"fps": round(fps, 1), "connected": self.grab.connected})
            blobs, mask = ([], None)
            if self.color is not None:
                blobs, mask = self.color.detect(frame)

            # throttled YOLO; reuse last boxes within TTL
            persons = None
            if self.detector is not None:
                self._frame_i += 1
                run_yolo = (self._frame_i % max(1, self.cfg.detector.every_n)) == 0
                if run_yolo:
                    try:
                        self._last_boxes = self.detector.detect(frame)
                        self._last_boxes_time = t0
                    except Exception as e:  # pragma: no cover
                        print(f"[pipeline] YOLO inference error: {e}")
                if (t0 - self._last_boxes_time) <= self.cfg.detector.box_ttl_sec:
                    persons = self._last_boxes
            self.health.beat("detector", {"enabled": self.detector is not None})

            # P2: GPS-cue boost — when gps_tracker owned last frame the camera is
            # already aimed at the subject; boost blobs near frame center.
            gps_cue_px = None
            if self._arbiter_state == "gps_tracker":
                radius_frac = float(getattr(self.cfg.fusion, "gps_boost_radius_frac", 0.25))
                r = radius_frac * min(w, h)
                gps_cue_px = (w / 2.0, h / 2.0, r)

            fr = self.fusion.update(blobs, persons, gps_cue_px=gps_cue_px)

            # control: always compute (for the overlay); SEND only while we own
            # the PTZ and are not killed.
            if self.state.killed:
                cmd = STOP_CMD
                abs_cmd = None
                self._send_cmd(cmd)               # killed -> force stop
                self._send_zoom("stop")
                zoom_cmd = "hold"
                self._arbiter_state = "killed"
            else:
                cmd = self.servo.compute(fr.target_xy, (w, h))
                zoom_cmd = None
                abs_cmd = None

                # P1: GPS arbiter handoff
                gps_fix = self.gps.get_fix() if self.gps else None
                gps_fresh = (
                    gps_fix is not None and
                    gps_fix.age_sec < getattr(self.cfg.gps, "stale_threshold_sec", 10.0)
                ) if gps_fix else False
                gps_calibrated = self.pose.calibrated
                # C1: base position latched once at setup; tripod is stationary.
                base_locked = self.pose.has_base
                # Sync arbiter hysteresis params from cfg so hot-config takes effect.
                self.arbiter.lock_frames = int(getattr(self.cfg.gps, "lock_frames",
                                                       self.arbiter.lock_frames))
                self.arbiter.grace_sec = float(getattr(self.cfg.gps, "grace_sec",
                                                       self.arbiter.grace_sec))
                decision = self.arbiter.decide(fr, gps_fresh, gps_calibrated,
                                               base_locked, t0)
                prev_state = self._arbiter_state
                self._arbiter_state = decision.owner

                # Record state transitions (once per change, not per frame)
                if self._prev_locked != fr.locked:
                    self.events.record("lock", "acquired" if fr.locked else "lost")
                    self._prev_locked = fr.locked
                if decision.owner != prev_state:
                    self.events.record("owner", decision.owner)
                if self._prev_gps_viable != gps_fresh:
                    self.events.record("gps", "viable" if gps_fresh else "unviable")
                    self._prev_gps_viable = gps_fresh

                # Atomic zoom handoff: kill old zoom before new owner takes zoom
                if decision.owner != prev_state:
                    self._send_zoom("stop")

                # Release outgoing autonomous owner BEFORE requesting new one.
                # ptz_owner.request refuses cross-owner steals (idle→owner only).
                _curr = self.owner.owner
                _want = decision.owner
                if _curr in ("vision_follow", "gps_tracker") and _curr != _want:
                    self.owner.release(_curr)

                if decision.owner == "vision_follow":
                    if _curr != "vision_follow":
                        self.owner.request("vision_follow")
                    if self.owner.owner in ("vision_follow", "testbed"):
                        self._send_cmd(cmd)
                        zoom_cmd = self._maybe_send_cinematic_zoom(fr, h)

                elif decision.owner == "gps_tracker":
                    if _curr != "gps_tracker":
                        self.owner.request("gps_tracker")
                    # Only drive GPS if we actually own it (not blocked)
                    if self.owner.owner == "gps_tracker":
                        abs_cmd = self._gps_pointing_cmd(gps_fix)
                        if abs_cmd is not None:
                            self._send_absolute_cmd(abs_cmd)
                        else:
                            self._send_cmd(STOP_CMD)
                    else:
                        self._send_cmd(STOP_CMD)

                else:
                    # idle — hold position, release any autonomous owner
                    if _curr in ("vision_follow", "gps_tracker", "testbed"):
                        self.owner.release(_curr)
                    self._send_cmd(STOP_CMD)

            # render
            hud = {
                "fps": fps,
                "ptz": "ON" if self.cfg.ptz.enabled else "off",
                "killed": self.state.killed,
            }
            annotated = (
                annotate(
                    frame,
                    mask,
                    blobs,
                    persons or [],
                    fr,
                    cmd,
                    self.cfg.ptz,
                    hud,
                    show_mask=self.state.show_mask,
                )
                if self.state.show_hud
                else frame
            )
            ok, buf = cv2.imencode(".jpg", annotated,
                                   [cv2.IMWRITE_JPEG_QUALITY, self.cfg.web.jpeg_quality])
            if ok:
                self.state.set_jpeg(buf.tobytes())

            self.state.set_status(
                state=("KILLED" if self.state.killed else fr.state),
                conf=fr.conf, locked=fr.locked,
                has_color=fr.has_color, has_person=fr.has_person, matched=fr.matched,
                fps=round(fps, 1), connected=self.grab.connected,
                ptz_enabled=self.cfg.ptz.enabled,
                owner=self.owner.owner,
                arbiter=self._arbiter_state,
                cmd=("stop" if cmd.is_stop or self.owner.owner not in ("testbed", "vision_follow", "gps_tracker")
                     else (f"GPS abs" if self._arbiter_state == "gps_tracker"
                           else f"p{cmd.pan_speed}/t{cmd.tilt_speed}")),
                zoom_cmd=zoom_cmd or "hold",
            )

            # Estimator shadow tick — additive read-only side channel; never commands.
            if self.estimator is not None:
                self._est_tick += 1
                _est_cfg = getattr(self.cfg, "estimator", None)
                _log_every_n = int(getattr(_est_cfg, "log_every_n", 3))
                _gps_updated = False
                _vision_updated = False

                _gps_fix = self.gps.get_fix() if self.gps else None
                if _gps_fix is not None:
                    self.estimator.update_gps(_gps_fix, now=t0)
                    _gps_updated = True

                # Vision update: only when locked and encoder data is fresh
                _ptz_state = getattr(self, "ptz_state", None)
                if _ptz_state is not None and fr.locked and fr.target_xy is not None:
                    _enc, _enc_age = _ptz_state.latest()
                    if _enc is not None and (_enc_age is None or _enc_age < 0.5):
                        # zoom_enc: read from ptz_state when zoom encoder is available
                        _zoom_enc = 0
                        self.estimator.update_vision(
                            pan_enc=_enc[0],
                            pixel_cx=fr.target_xy[0], frame_w=w,
                            zoom_enc=_zoom_enc, now=t0,
                        )
                        _vision_updated = True

                if self._est_tick % _log_every_n == 0:
                    _out = self.estimator.predict_output(now=t0)
                    if _out is not None:
                        _record = {
                            "t": t0,
                            "e": round(_out.e, 2), "n": round(_out.n, 2),
                            "ve": round(_out.ve, 3), "vn": round(_out.vn, 3),
                            "cov_trace": round(sum(_out.cov[i][i] for i in range(4)), 4),
                            "bearing_deg": round(_out.bearing_deg, 2),
                            "dist_m": round(_out.dist_m, 1),
                            "pan_enc_would": _out.pan_enc_would,
                            "tilt_enc_would": _out.tilt_enc_would,
                            "bearing_std_deg": round(_out.bearing_std_deg, 3),
                            "owner_actual": self._arbiter_state,
                            "cmd_actual": self.state.get_status().get("cmd", ""),
                            "gps_updated": _gps_updated,
                            "vision_updated": _vision_updated,
                        }
                        self.events.record("shadow", _record)
                        self._shadow_write(_record)

            self.health.beat("loop")

            # fps bookkeeping
            n_fps += 1
            t_now = time.time()
            if t_now - t_fps >= 1.0:
                fps = n_fps / (t_now - t_fps)
                n_fps = 0
                t_fps = t_now
            if time.time() - t_log >= self.cfg.loop.log_every_sec:
                s = self.state.get_status()
                print(f"[loop] {s['state']:9s} conf={s.get('conf',0):.2f} "
                      f"fps={s.get('fps',0):.1f} cmd={s.get('cmd')} "
                      f"conn={s.get('connected')}")
                t_log = time.time()

            dt = time.time() - t0
            if dt < period:
                time.sleep(period - dt)

    def _shadow_write(self, record: dict) -> None:
        """Shadow logging must never take down the vision loop (e.g. disk full)."""
        if self._shadow_writer is None:
            return
        try:
            self._shadow_writer.write(record)
        except OSError as e:
            print(f"[pipeline] shadow write failed ({e}); shadow logging disabled")
            try:
                self._shadow_writer.close()
            except OSError:
                pass
            self._shadow_writer = None

    def _shutdown(self):
        """Runs even when the loop crashes — the camera must never be left
        holding its last velocity command."""
        if self._shadow_writer is not None:
            try:
                self._shadow_writer.close()
            except OSError:
                pass
            self._shadow_writer = None
        try:
            if self.cfg.ptz.enabled:
                self.ptz.stop()
                self.owner.release("testbed")
        finally:
            self.grab.stop()

    def stop(self):
        self._stop.set()
