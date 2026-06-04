"""Release-shaped Control API adapter for WaveCam.

The existing web console is still the hardware bring-up surface. This module
adds the production-facing /api/v1 contract beside it, using the same pipeline,
PTZ owner gate, and PTZ backend.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import threading
import time
import uuid
from typing import Any, Callable, Dict

from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .auth import CONFIG, PTZ, READ, SAFETY, SERVICE, install_auth, require, websocket_authorized
from .color_presets import COLOR_PRESETS, preset_hsv_ranges
from .ptz_owner import AUTONOMOUS, IDLE
from .ptz_visca import PAN_LEFT, PAN_RIGHT, PAN_STOP, TILT_DOWN, TILT_STOP, TILT_UP
from .supervisor import read_health, restart_systemd_unit, snapshot_services


FrameSource = Callable[[], Any]

HOT_CONFIG_KEYS = (
    "ptz.deadzone",
    "ptz.max_pan_speed",
    "ptz.max_tilt_speed",
    "ptz.min_speed",
    "ptz.command_min_interval",
    "ptz.ff_gain",
    "ptz.ff_deadzone_mult",
    "ptz.invert_pan",
    "ptz.invert_tilt",
    "ptz.cinematic_zoom_enabled",
    "ptz.zoom_target_frac",
    "ptz.zoom_deadband",
    "ptz.zoom_max_speed",
    "fusion.lock_threshold",
    "fusion.unlock_threshold",
    "fusion.require_person",
    "fusion.match_dist",
    "fusion.person_aim_x",
    "fusion.person_aim_y",
    "color.preset",
    "color.min_area",
    "color.max_area",
    "color.morph_kernel",
    "detector.conf",
    "detector.imgsz",
    "detector.person_class",
    "detector.every_n",
    "detector.box_ttl_sec",
    "web.show_mask",
    "web.jpeg_quality",
)

RESTART_REQUIRED_KEYS = (
    "camera.source",
    "camera.codec",
    "camera.use_gstreamer",
    "ptz.enabled",
    "ptz.ip",
    "ptz.port",
    "ptz.address",
    "ptz.reset_sequence",
    "camera_ai.disable_on_start",
    "color.enabled",
    "detector.enabled",
    "detector.model",
    "web.host",
    "web.port",
)

YOLO_CLASSES = (
    {"id": 0, "label": "person"},
    {"id": 1, "label": "bicycle"},
    {"id": 2, "label": "car"},
    {"id": 3, "label": "motorcycle"},
    {"id": 14, "label": "bird"},
    {"id": 15, "label": "cat"},
    {"id": 16, "label": "dog"},
    {"id": 32, "label": "sports ball"},
    {"id": 37, "label": "surfboard"},
    {"id": 41, "label": "cup"},
)


class SafetyKillRequest(BaseModel):
    reason: str | None = None
    source: str | None = None


class SafetyResumeRequest(BaseModel):
    source: str | None = None


class VelocityRequest(BaseModel):
    requested_owner: str = "manual"
    pan: float = Field(default=0.0, ge=-1.0, le=1.0)
    tilt: float = Field(default=0.0, ge=-1.0, le=1.0)
    zoom: float = Field(default=0.0, ge=-1.0, le=1.0)
    takeover: bool = False
    deadman_ms: int = Field(default=800, ge=100, le=5000)
    source: str | None = None


class PtzStopRequest(BaseModel):
    hold: bool = True
    source: str | None = None


class PtzHomeRequest(BaseModel):
    requested_owner: str = "manual"
    takeover: bool = False
    source: str | None = None


class ZoomRequest(BaseModel):
    requested_owner: str = "manual"
    mode: str = "velocity"
    value: float = Field(default=0.0, ge=-1.0, le=1.0)
    takeover: bool = False
    deadman_ms: int = Field(default=800, ge=100, le=5000)
    source: str | None = None


class CalibrationBaseRequest(BaseModel):
    requested_owner: str = "manual"
    takeover: bool = False
    source: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=256)


class HeadingCalibrationRequest(CalibrationBaseRequest):
    heading_deg: float = Field(ge=0.0, le=360.0)


class TiltCalibrationRequest(CalibrationBaseRequest):
    tilt_deg: float = Field(ge=-90.0, le=90.0)


class ZoomCalibrationRequest(CalibrationBaseRequest):
    zoom_fov_deg: float = Field(ge=1.0, le=180.0)


class RecordStartRequest(BaseModel):
    segment_seconds: int | None = Field(default=None, ge=1, le=21600)
    source: str | None = None


class RecordStopRequest(BaseModel):
    source: str | None = None


class HotConfigRequest(BaseModel):
    revision: int | None = None
    patch: Dict[str, Any]
    persist: bool = False


class RestartRequest(BaseModel):
    reason: str | None = None
    confirm_moving: bool = False
    delay_seconds: float = Field(default=0.35, ge=0.0, le=5.0)


class AgentSummonRequest(BaseModel):
    source: str | None = Field(default=None, max_length=64)
    reason: str | None = Field(default=None, max_length=256)


def register_control_api(app: FastAPI, pipeline, frames: FrameSource) -> None:
    adapter = ControlApiAdapter(pipeline, frames)
    app.state.control_api = adapter
    install_auth(app)
    register_status_routes(app, adapter)
    register_safety_routes(app, adapter)
    register_ptz_routes(app, adapter)
    register_calibration_routes(app, adapter)
    register_media_routes(app, adapter)
    register_config_routes(app, adapter)
    register_system_routes(app, adapter)
    register_agent_routes(app, adapter)


def register_status_routes(app: FastAPI, api: "ControlApiAdapter") -> None:
    @app.get("/api/v1/status", dependencies=[Depends(require(READ))])
    def status():
        return api.status_snapshot()

    @app.get("/api/v1/preview.mjpeg", dependencies=[Depends(require(READ))])
    def preview():
        return StreamingResponse(
            api.frames(),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    @app.websocket("/api/v1/telemetry")
    async def telemetry(websocket: WebSocket):
        await websocket.accept()
        if not websocket_authorized(websocket, READ):
            await websocket.close(code=1008)
            return
        try:
            while True:
                await websocket.send_json(
                    {"type": "status", "revision": api.revision, "status": api.status_snapshot()}
                )
                await asyncio.sleep(0.5)
        except WebSocketDisconnect:
            return


def register_safety_routes(app: FastAPI, api: "ControlApiAdapter") -> None:
    @app.post("/api/v1/safety/kill", dependencies=[Depends(require(SAFETY))])
    def safety_kill(_: SafetyKillRequest | None = None):
        api.pipeline.kill(True)
        api.media.stop_for_safety()
        api.cancel_manual_deadman()
        api.cancel_zoom_deadman()
        api.bump_revision()
        return api.ok()

    @app.post("/api/v1/safety/resume", dependencies=[Depends(require(SAFETY))])
    def safety_resume(_: SafetyResumeRequest | None = None):
        api.resume_without_autostart()
        api.bump_revision()
        return api.ok()


def register_ptz_routes(app: FastAPI, api: "ControlApiAdapter") -> None:
    @app.post("/api/v1/ptz/stop", dependencies=[Depends(require(PTZ))])
    def ptz_stop(req: PtzStopRequest | None = None):
        api.stop_ptz(hold=req.hold if req else True)
        api.bump_revision()
        return api.ok()

    @app.post("/api/v1/ptz/auto", dependencies=[Depends(require(PTZ))])
    def ptz_auto():
        if not api.start_autonomous("testbed"):
            return api.refusal("killed", "KILL is latched; resume before starting auto PTZ.")
        api.bump_revision()
        return api.ok()

    @app.post("/api/v1/ptz/home", dependencies=[Depends(require(PTZ))])
    def ptz_home(req: PtzHomeRequest | None = None):
        req = req or PtzHomeRequest()
        if api.pipeline.owner.killed:
            return api.refusal("killed", "KILL is latched; resume before movement commands.")
        if req.requested_owner != "manual":
            return api.refusal("invalid_request", "Only requested_owner=manual is accepted in v1.", 422)
        if not api.claim_manual(takeover=req.takeover):
            return api.refusal("owner_busy", "Another PTZ owner holds the camera.")

        api.home_ptz()
        api.bump_revision()
        return api.ok()

    @app.post("/api/v1/ptz/velocity", dependencies=[Depends(require(PTZ))])
    def ptz_velocity(req: VelocityRequest):
        if api.pipeline.owner.killed:
            return api.refusal("killed", "KILL is latched; resume before movement commands.")
        if req.requested_owner != "manual":
            return api.refusal("invalid_request", "Only requested_owner=manual is accepted in v1.", 422)
        if not api.claim_manual(takeover=req.takeover):
            return api.refusal("owner_busy", "Another PTZ owner holds the camera.")

        api.send_manual_velocity(req)
        api.schedule_manual_deadman(req.deadman_ms)
        api.bump_revision()
        return api.ok()

    @app.post("/api/v1/ptz/zoom", dependencies=[Depends(require(PTZ))])
    def ptz_zoom(req: ZoomRequest):
        if api.pipeline.owner.killed:
            return api.refusal("killed", "KILL is latched; resume before movement commands.")
        if req.requested_owner != "manual":
            return api.refusal("invalid_request", "Only requested_owner=manual is accepted in v1.", 422)
        if req.mode != "velocity":
            return api.refusal("invalid_request", "Only mode=velocity is accepted in v1.", 422)
        if api.pipeline.owner.owner in AUTONOMOUS:
            api.send_manual_zoom_velocity(req.value, req.deadman_ms)
            if req.value == 0:
                api.cancel_zoom_deadman()
            else:
                api.schedule_zoom_deadman(req.deadman_ms)
            api.bump_revision()
            return api.ok()
        if not api.claim_manual(takeover=req.takeover):
            return api.refusal("owner_busy", "Another PTZ owner holds the camera.")

        api.send_manual_zoom_velocity(req.value, req.deadman_ms)
        if req.value == 0:
            if not api.manual_pan_tilt_active:
                api.cancel_manual_deadman()
                api.release_manual_owner()
        else:
            api.schedule_manual_deadman(req.deadman_ms)
        api.bump_revision()
        return api.ok()


def register_calibration_routes(app: FastAPI, api: "ControlApiAdapter") -> None:
    @app.get("/api/v1/calibration", dependencies=[Depends(require(READ))])
    def calibration_get():
        return api.calibration_ok()

    @app.post("/api/v1/calibration/heading", dependencies=[Depends(require(PTZ))])
    def calibration_heading(req: HeadingCalibrationRequest):
        refusal = api.validate_calibration_capture(req)
        if refusal is not None:
            return refusal
        api.capture_calibration(
            "heading",
            {
                "heading_deg": req.heading_deg,
                "source": normalized_text(req.source, "unknown", 64),
                "note": normalized_optional_text(req.note, 256),
            },
        )
        api.bump_revision()
        return api.calibration_ok()

    @app.post("/api/v1/calibration/tilt", dependencies=[Depends(require(PTZ))])
    def calibration_tilt(req: TiltCalibrationRequest):
        refusal = api.validate_calibration_capture(req)
        if refusal is not None:
            return refusal
        api.capture_calibration(
            "tilt",
            {
                "tilt_deg": req.tilt_deg,
                "source": normalized_text(req.source, "unknown", 64),
                "note": normalized_optional_text(req.note, 256),
            },
        )
        api.bump_revision()
        return api.calibration_ok()

    @app.post("/api/v1/calibration/zoom", dependencies=[Depends(require(PTZ))])
    def calibration_zoom(req: ZoomCalibrationRequest):
        refusal = api.validate_calibration_capture(req)
        if refusal is not None:
            return refusal
        api.capture_calibration(
            "zoom",
            {
                "zoom_fov_deg": req.zoom_fov_deg,
                "source": normalized_text(req.source, "unknown", 64),
                "note": normalized_optional_text(req.note, 256),
            },
        )
        api.bump_revision()
        return api.calibration_ok()


def register_media_routes(app: FastAPI, api: "ControlApiAdapter") -> None:
    @app.get("/api/v1/media/status", dependencies=[Depends(require(READ))])
    def media_status():
        return api.media.status()

    @app.get("/api/v1/media/list", dependencies=[Depends(require(READ))])
    def media_list():
        try:
            files = api.media.list_files()
        except MediaUnavailable as exc:
            return api.refusal("media_unavailable", exc.message, 503)
        return JSONResponse(
            {
                "ok": True,
                "request_id": make_request_id(),
                "files": files,
                "status": api.status_snapshot(),
            }
        )

    @app.get("/api/v1/media/download/{name}", dependencies=[Depends(require(READ))])
    def media_download(name: str):
        try:
            path = api.media.download_path(name)
        except MediaUnavailable as exc:
            return api.refusal("media_unavailable", exc.message, 503)
        except MediaNotFound as exc:
            return api.refusal("media_not_found", exc.message, 404)
        return FileResponse(path, media_type="video/mp4", filename=path.name)

    @app.post("/api/v1/media/record/start", dependencies=[Depends(require(CONFIG))])
    def media_record_start(req: RecordStartRequest | None = None):
        try:
            result = api.media.start(req.segment_seconds if req else None)
        except MediaUnavailable as exc:
            return api.refusal("media_unavailable", exc.message, 503)
        api.bump_revision()
        return media_ok(api, result)

    @app.post("/api/v1/media/record/stop", dependencies=[Depends(require(CONFIG))])
    def media_record_stop(_: RecordStopRequest | None = None):
        try:
            result = api.media.stop()
        except MediaUnavailable as exc:
            return api.refusal("media_unavailable", exc.message, 503)
        api.bump_revision()
        return media_ok(api, result)


def register_config_routes(app: FastAPI, api: "ControlApiAdapter") -> None:
    @app.get("/api/v1/config", dependencies=[Depends(require(READ))])
    def config_get():
        return api.config_snapshot()

    @app.post("/api/v1/config/hot", dependencies=[Depends(require(CONFIG))])
    def config_hot(req: HotConfigRequest):
        refusal = api.validate_hot_config_request(req)
        if refusal is not None:
            return refusal
        refusal = api.apply_hot_config(req.patch)
        if refusal is not None:
            return refusal
        api.bump_revision()
        return api.ok()


def register_system_routes(app: FastAPI, api: "ControlApiAdapter") -> None:
    @app.post("/api/v1/system/restart", dependencies=[Depends(require(CONFIG))])
    def system_restart(req: RestartRequest | None = None):
        return api.request_service_restart(req or RestartRequest())


def register_agent_routes(app: FastAPI, api: "ControlApiAdapter") -> None:
    @app.post("/api/v1/agent/summon", dependencies=[Depends(require(SERVICE))])
    def agent_summon(req: AgentSummonRequest | None = None):
        return api.request_agent_summon(req or AgentSummonRequest())


class ControlApiAdapter:
    """Small state holder for /api/v1 command behavior."""

    def __init__(self, pipeline, frames: FrameSource) -> None:
        self.pipeline = pipeline
        self.frames = frames
        self.media = MediaAdapter(getattr(pipeline, "recorder", None))
        self._lock = threading.RLock()
        self._revision = 0
        self._manual_deadman: threading.Timer | None = None
        self._zoom_deadman: threading.Timer | None = None
        self._manual_deadman_generation = 0
        self._zoom_deadman_generation = 0
        self._manual_pan_tilt_active = False
        self._restore_owner_after_manual: str | None = None
        self._restart_timer: threading.Timer | None = None
        self._restart_pending = False
        self._restart_unit = "wavecam.service"
        self._calibration = empty_calibration_state()

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def bump_revision(self) -> None:
        with self._lock:
            self._revision += 1

    def status_snapshot(self) -> dict:
        return build_status_snapshot(self.pipeline, self.revision, self.media.status())

    def config_snapshot(self) -> dict:
        return build_config_snapshot(self.pipeline, self.revision, self.calibration_state())

    def ok(self) -> JSONResponse:
        return JSONResponse(
            {"ok": True, "request_id": make_request_id(), "status": self.status_snapshot()}
        )

    def refusal(self, code: str, message: str, status_code: int = 409) -> JSONResponse:
        return JSONResponse(
            {"ok": False, "code": code, "message": message, "status": self.status_snapshot()},
            status_code=status_code,
        )

    def calibration_ok(self) -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "request_id": make_request_id(),
                "revision": self.revision,
                "calibration": self.calibration_state(),
                "status": self.status_snapshot(),
            }
        )

    def calibration_state(self) -> dict:
        with self._lock:
            return {
                "reference_heading": self._calibration["reference_heading"],
                "heading": copy_optional_dict(self._calibration["heading"]),
                "tilt": copy_optional_dict(self._calibration["tilt"]),
                "zoom": copy_optional_dict(self._calibration["zoom"]),
                "updated_at_unix_ms": self._calibration["updated_at_unix_ms"],
            }

    def validate_calibration_capture(self, req: CalibrationBaseRequest) -> JSONResponse | None:
        if self.pipeline.owner.killed:
            return self.refusal("killed", "KILL is latched; resume before calibration capture.")
        if req.requested_owner != "manual":
            return self.refusal("invalid_request", "Only requested_owner=manual is accepted in v1.", 422)
        if not self.claim_manual(takeover=req.takeover):
            return self.refusal("owner_busy", "Another PTZ owner holds the camera.")
        return None

    def capture_calibration(self, step: str, values: dict) -> None:
        captured_at = int(time.time() * 1000)
        entry = {**values, "captured_at_unix_ms": captured_at}
        with self._lock:
            self._calibration[step] = entry
            self._calibration["updated_at_unix_ms"] = captured_at
            if step == "heading":
                self._calibration["reference_heading"] = entry["heading_deg"]

    def resume_without_autostart(self) -> None:
        with self._lock:
            self.cancel_manual_deadman()
            self.cancel_zoom_deadman()
            self._restore_owner_after_manual = None
            self._manual_pan_tilt_active = False
            self.pipeline.state.killed = False
            self.pipeline.owner.resume()
            if self.pipeline.owner.owner != IDLE:
                self.pipeline.owner.release(self.pipeline.owner.owner)
            self.pipeline.state.set_status(killed=False, state="SEARCHING")

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

    def send_manual_velocity(self, req: VelocityRequest) -> None:
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
            self.bump_revision()

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
                self.bump_revision()

    def apply_hot_config(self, patch: Dict[str, Any]) -> JSONResponse | None:
        for key, value in patch.items():
            refusal = self.apply_hot_key(key, value, dry_run=True)
            if refusal is not None:
                return refusal
        for key, value in patch.items():
            refusal = self.apply_hot_key(key, value, dry_run=False)
            if refusal is not None:
                return refusal
        return None

    def validate_hot_config_request(self, req: HotConfigRequest) -> JSONResponse | None:
        if req.persist:
            return self.refusal(
                "invalid_request",
                "persist=true is not supported by hot config in v1.",
                422,
            )
        if req.revision is not None and req.revision != self.revision:
            return self.refusal(
                "revision_conflict",
                "Hot config revision is stale; refresh /api/v1/config and retry.",
                409,
            )
        return None

    def apply_hot_key(self, key: str, value: Any, dry_run: bool = False) -> JSONResponse | None:
        cfg = self.pipeline.cfg
        setters = {
            "ptz.deadzone": lambda: set_float(cfg.ptz, "deadzone", value, 0.02, 0.30, dry_run=dry_run),
            "ptz.max_pan_speed": lambda: set_int(cfg.ptz, "max_pan_speed", value, 1, 24, dry_run=dry_run),
            "ptz.max_tilt_speed": lambda: set_int(cfg.ptz, "max_tilt_speed", value, 1, 20, dry_run=dry_run),
            "ptz.min_speed": lambda: set_int(cfg.ptz, "min_speed", value, 1, 8, dry_run=dry_run),
            "ptz.command_min_interval": lambda: set_float(
                cfg.ptz, "command_min_interval", value, 0.01, 0.50, dry_run=dry_run
            ),
            "ptz.ff_gain": lambda: set_float(cfg.ptz, "ff_gain", value, 0.0, 1.0, dry_run=dry_run),
            "ptz.ff_deadzone_mult": lambda: set_float(
                cfg.ptz, "ff_deadzone_mult", value, 1.0, 4.0, dry_run=dry_run
            ),
            "ptz.invert_pan": lambda: set_bool(cfg.ptz, "invert_pan", value, dry_run=dry_run),
            "ptz.invert_tilt": lambda: set_bool(cfg.ptz, "invert_tilt", value, dry_run=dry_run),
            "ptz.cinematic_zoom_enabled": lambda: set_bool(
                cfg.ptz, "cinematic_zoom_enabled", value, dry_run=dry_run
            ),
            "ptz.zoom_target_frac": lambda: set_float(cfg.ptz, "zoom_target_frac", value, 0.2, 0.8, dry_run=dry_run),
            "ptz.zoom_deadband": lambda: set_float(cfg.ptz, "zoom_deadband", value, 0.01, 0.30, dry_run=dry_run),
            "ptz.zoom_max_speed": lambda: set_int(cfg.ptz, "zoom_max_speed", value, 1, 7, dry_run=dry_run),
            "fusion.lock_threshold": lambda: set_float(cfg.fusion, "lock_threshold", value, 0.05, 0.95, dry_run=dry_run),
            "fusion.unlock_threshold": lambda: set_float(cfg.fusion, "unlock_threshold", value, 0.05, 0.95, dry_run=dry_run),
            "fusion.require_person": lambda: set_bool(cfg.fusion, "require_person", value, dry_run=dry_run),
            "fusion.match_dist": lambda: set_float(cfg.fusion, "match_dist", value, 20.0, 500.0, dry_run=dry_run),
            "fusion.person_aim_x": lambda: set_float(cfg.fusion, "person_aim_x", value, 0.0, 1.0, dry_run=dry_run),
            "fusion.person_aim_y": lambda: set_float(cfg.fusion, "person_aim_y", value, 0.0, 1.0, dry_run=dry_run),
            "color.preset": lambda: self.apply_color_preset(value, dry_run=dry_run),
            "color.min_area": lambda: set_int(cfg.color, "min_area", value, 1, 500000, dry_run=dry_run),
            "color.max_area": lambda: set_int(cfg.color, "max_area", value, 100, 1000000, dry_run=dry_run),
            "color.morph_kernel": lambda: self.apply_morph_kernel(value, dry_run=dry_run),
            "detector.conf": lambda: set_float(cfg.detector, "conf", value, 0.05, 0.95, dry_run=dry_run),
            "detector.imgsz": lambda: set_int(cfg.detector, "imgsz", value, 160, 1280, dry_run=dry_run),
            "detector.person_class": lambda: set_int(cfg.detector, "person_class", value, 0, 79, dry_run=dry_run),
            "detector.every_n": lambda: set_int(cfg.detector, "every_n", value, 1, 30, dry_run=dry_run),
            "detector.box_ttl_sec": lambda: set_float(cfg.detector, "box_ttl_sec", value, 0.1, 5.0, dry_run=dry_run),
            "web.show_mask": lambda: set_bool(self.pipeline.state, "show_mask", value, dry_run=dry_run),
            "web.jpeg_quality": lambda: set_int(cfg.web, "jpeg_quality", value, 30, 95, dry_run=dry_run),
        }
        setter = setters.get(key)
        if setter is None:
            return self.refusal("invalid_request", f"{key} is not a hot-config key.", 422)
        error = setter()
        if error is not None:
            return self.refusal("invalid_request", error, 422)
        return None

    def apply_color_preset(self, value: Any, dry_run: bool = False) -> str | None:
        if not isinstance(value, str):
            return "preset must be a string."
        if value not in COLOR_PRESETS:
            return f"preset must be one of {', '.join(sorted(COLOR_PRESETS))}."
        if dry_run:
            return None
        cfg = self.pipeline.cfg.color
        cfg.preset = value
        cfg.hsv_ranges = preset_hsv_ranges(value)
        color = getattr(self.pipeline, "color", None)
        if color is not None:
            color.update_ranges(cfg.hsv_ranges)
        return None

    def apply_morph_kernel(self, value: Any, dry_run: bool = False) -> str | None:
        cfg = self.pipeline.cfg.color
        error = set_int(cfg, "morph_kernel", value, 1, 31, dry_run=dry_run)
        if error is not None:
            return error
        if dry_run:
            return None
        color = getattr(self.pipeline, "color", None)
        if color is not None:
            color.update_kernel()
        return None

    def request_service_restart(self, req: RestartRequest) -> JSONResponse:
        if self.restart_pending:
            return self.refusal(
                "restart_pending",
                "A WaveCam restart request is already pending.",
            )
        if self.restart_requires_confirmation() and not req.confirm_moving:
            return self.refusal(
                "restart_confirmation_required",
                "Camera control is active; retry with confirm_moving=true to stop PTZ and restart.",
            )
        self.prepare_for_restart()
        self.schedule_service_restart(req.delay_seconds)
        self.bump_revision()
        return JSONResponse(
            {
                "ok": True,
                "request_id": make_request_id(),
                "action": "restart",
                "unit": self._restart_unit,
                "scheduled": True,
                "delay_seconds": req.delay_seconds,
                "status": self.status_snapshot(),
            },
            status_code=202,
        )

    def request_agent_summon(self, req: AgentSummonRequest) -> JSONResponse:
        source = normalized_text(req.source, "unknown", 64)
        reason = normalized_text(req.reason, "operator_diagnostics", 256)
        return JSONResponse(
            {
                "ok": True,
                "request_id": make_request_id(),
                "action": "agent_summon",
                "accepted": True,
                "source": source,
                "reason": reason,
                "message": (
                    "Agent diagnostics request accepted; no automatic shell, service, "
                    "or camera movement command was run."
                ),
                "status": self.status_snapshot(),
            },
            status_code=202,
        )

    @property
    def restart_pending(self) -> bool:
        with self._lock:
            return self._restart_pending

    def restart_requires_confirmation(self) -> bool:
        if self.pipeline.owner.killed:
            return False
        return self.pipeline.owner.owner != IDLE

    def prepare_for_restart(self) -> None:
        self.cancel_manual_deadman()
        self.cancel_zoom_deadman()
        self._restore_owner_after_manual = None
        self.pipeline.ptz.stop()
        self.pipeline.ptz.zoom("stop")
        current_owner = self.pipeline.owner.owner
        if current_owner != IDLE:
            self.pipeline.owner.release(current_owner)
        self.pipeline.state.set_status(state="RESTARTING", cmd="stop")

    def schedule_service_restart(self, delay_seconds: float) -> None:
        with self._lock:
            self._restart_pending = True
        timer = threading.Timer(delay_seconds, self.run_service_restart)
        timer.daemon = True
        with self._lock:
            self._restart_timer = timer
        timer.start()

    def run_service_restart(self) -> None:
        try:
            restart = getattr(self.pipeline, "restart_service", None)
            if callable(restart):
                restart(self._restart_unit)
            else:
                restart_systemd_unit(self._restart_unit)
        finally:
            with self._lock:
                self._restart_pending = False
                self._restart_timer = None


class MediaAdapter:
    """Small recorder facade used by /api/v1 status and media routes."""

    def __init__(self, recorder) -> None:
        self.recorder = recorder

    def status(self) -> dict:
        if self.recorder is None:
            return unknown_media()
        try:
            return normalize_media(self.recorder.status())
        except Exception as exc:
            media = unknown_media()
            media["error"] = str(exc)
            return media

    def start(self, segment_seconds: int | None) -> dict:
        if self.recorder is None:
            raise MediaUnavailable("Recorder is not configured.")
        return self.recorder.start(segment_seconds=segment_seconds)

    def stop(self) -> dict:
        if self.recorder is None:
            raise MediaUnavailable("Recorder is not configured.")
        return self.recorder.stop()

    def list_files(self) -> list[dict]:
        rec_dir = self.rec_dir()
        if not rec_dir.exists():
            return []
        files: list[dict] = []
        for path in rec_dir.iterdir():
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            files.append(
                {
                    "name": path.name,
                    "size_bytes": stat.st_size,
                    "ctime_unix_ms": int(stat.st_ctime * 1000),
                }
            )
        return sorted(files, key=lambda item: (item["ctime_unix_ms"], item["name"]), reverse=True)

    def download_path(self, name: str) -> Path:
        rec_dir = self.rec_dir().resolve()
        if (
            not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or Path(name).name != name
        ):
            raise MediaNotFound("Media file was not found.")
        path = (rec_dir / name).resolve()
        try:
            path.relative_to(rec_dir)
        except ValueError as exc:
            raise MediaNotFound("Media file was not found.") from exc
        if not path.is_file():
            raise MediaNotFound("Media file was not found.")
        return path

    def rec_dir(self) -> Path:
        if self.recorder is None:
            raise MediaUnavailable("Recorder is not configured.")
        config = getattr(self.recorder, "config", None)
        rec_dir = getattr(config, "rec_dir", None)
        if rec_dir is None:
            raise MediaUnavailable("Recorder directory is not configured.")
        return Path(rec_dir)

    def stop_for_safety(self) -> None:
        if self.recorder is not None:
            self.recorder.stop()


class MediaUnavailable(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class MediaNotFound(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def media_ok(api: ControlApiAdapter, result: dict) -> JSONResponse:
    return JSONResponse(
        {
            "ok": bool(result.get("ok", True)),
            "request_id": make_request_id(),
            "media": result,
            "status": api.status_snapshot(),
        }
    )


def normalized_text(value: str | None, fallback: str, max_len: int) -> str:
    text = (value or "").strip()
    if not text:
        return fallback
    return text[:max_len]


def normalized_optional_text(value: str | None, max_len: int) -> str | None:
    text = (value or "").strip()
    return text[:max_len] if text else None


def empty_calibration_state() -> dict:
    return {
        "reference_heading": None,
        "heading": None,
        "tilt": None,
        "zoom": None,
        "updated_at_unix_ms": None,
    }


def copy_optional_dict(value: dict | None) -> dict | None:
    return dict(value) if value is not None else None


def make_request_id() -> str:
    ms = int(time.time() * 1000) % 1000
    return f"{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}.{ms:03d}Z-{uuid.uuid4().hex[:8]}"


def build_config_snapshot(pipeline, revision: int, calibration: dict | None = None) -> dict:
    cfg = pipeline.cfg
    return {
        "revision": revision,
        "current": {
            "ptz": {
                "deadzone": cfg.ptz.deadzone,
                "max_pan_speed": cfg.ptz.max_pan_speed,
                "max_tilt_speed": cfg.ptz.max_tilt_speed,
                "min_speed": getattr(cfg.ptz, "min_speed", 1),
                "command_min_interval": getattr(cfg.ptz, "command_min_interval", 0.05),
                "ff_gain": getattr(cfg.ptz, "ff_gain", 0.0),
                "ff_deadzone_mult": getattr(cfg.ptz, "ff_deadzone_mult", 1.5),
                "invert_pan": cfg.ptz.invert_pan,
                "invert_tilt": cfg.ptz.invert_tilt,
                "cinematic_zoom_enabled": bool(
                    getattr(cfg.ptz, "cinematic_zoom_enabled", False)
                ),
                "zoom_target_frac": getattr(cfg.ptz, "zoom_target_frac", 0.5),
                "zoom_deadband": getattr(cfg.ptz, "zoom_deadband", 0.06),
                "zoom_max_speed": getattr(cfg.ptz, "zoom_max_speed", 5),
            },
            "fusion": {
                "lock_threshold": cfg.fusion.lock_threshold,
                "unlock_threshold": cfg.fusion.unlock_threshold,
                "require_person": cfg.fusion.require_person,
                "match_dist": cfg.fusion.match_dist,
                "person_aim_x": getattr(cfg.fusion, "person_aim_x", 0.5),
                "person_aim_y": getattr(cfg.fusion, "person_aim_y", 0.5),
            },
            "color": {
                "enabled": cfg.color.enabled,
                "preset": getattr(cfg.color, "preset", "orange_red"),
                "min_area": cfg.color.min_area,
                "max_area": getattr(cfg.color, "max_area", 200000),
                "morph_kernel": getattr(cfg.color, "morph_kernel", 5),
                "hsv_ranges": getattr(cfg.color, "hsv_ranges", {}),
            },
            "detector": {
                "enabled": cfg.detector.enabled,
                "model": getattr(cfg.detector, "model", None),
                "conf": cfg.detector.conf,
                "imgsz": cfg.detector.imgsz,
                "person_class": cfg.detector.person_class,
                "every_n": cfg.detector.every_n,
                "box_ttl_sec": cfg.detector.box_ttl_sec,
            },
            "web": {
                "show_mask": bool(getattr(pipeline.state, "show_mask", False)),
                "jpeg_quality": cfg.web.jpeg_quality,
            },
            "calibration": calibration or empty_calibration_state(),
        },
        "supported": {
            "calibration": True,
            "color_presets": sorted(COLOR_PRESETS),
            "ptz_home": callable(getattr(getattr(pipeline, "ptz", None), "home", None)),
            "yolo_classes": list(YOLO_CLASSES),
            "person_aim_y": {
                "0.20": "head/upper face",
                "0.35": "upper chest",
                "0.50": "box center",
            },
        },
        "hot_keys": list(HOT_CONFIG_KEYS),
        "restart_required_keys": list(RESTART_REQUIRED_KEYS),
    }


def build_status_snapshot(pipeline, revision: int, media: dict | None = None) -> dict:
    legacy = merged_status(pipeline)
    return {
        "revision": revision,
        "time_unix_ms": int(time.time() * 1000),
        "session": build_session(legacy, pipeline),
        "safety": build_safety(legacy),
        "ptz": build_ptz(legacy, pipeline),
        "tracking": build_tracking(legacy),
        "gps": build_gps(pipeline, legacy),
        "media": media if media is not None else unknown_media(),
        "services": snapshot_services(read_health()),
        "network": build_network(legacy),
    }


def merged_status(pipeline) -> dict:
    status = pipeline.state.get_status()
    status.update(pipeline.owner.state())
    return status


def build_session(legacy: dict, pipeline=None) -> dict:
    return {
        "state": str(legacy.get("state", "UNKNOWN")),
        "mode": session_mode(legacy, pipeline),
        "started_at_unix_ms": None,
    }


def session_mode(legacy: dict, pipeline=None) -> str:
    mode = legacy.get("mode") or getattr(pipeline, "mode", None)
    if mode:
        return str(mode)
    return "vision"


def build_safety(legacy: dict) -> dict:
    return {
        "killed": bool(legacy.get("killed", False)),
        "kill_reason": None,
        "last_kill_at_unix_ms": None,
    }


def build_ptz(legacy: dict, pipeline) -> dict:
    cfg_enabled = getattr(pipeline.cfg.ptz, "enabled", False)
    return {
        "owner": str(legacy.get("owner", IDLE)),
        "enabled": bool(legacy.get("ptz_enabled", cfg_enabled)),
        "pan_tilt_cmd": legacy.get("cmd"),
        "zoom_state": str(legacy.get("zoom_cmd", "hold")),
    }


def build_tracking(legacy: dict) -> dict:
    return {
        "locked": bool(legacy.get("locked", False)),
        "state": str(legacy.get("state", "UNKNOWN")),
        "confidence": float(legacy.get("conf", 0.0) or 0.0),
        "fps": float(legacy.get("fps", 0.0) or 0.0),
        "has_color": bool(legacy.get("has_color", False)),
        "has_person": bool(legacy.get("has_person", False)),
        "matched": bool(legacy.get("matched", False)),
    }


def build_gps(pipeline, legacy: dict) -> dict:
    status = unknown_gps()
    source = gps_snapshot_source(pipeline, legacy)
    if source is None:
        return status
    status.update(normalize_gps(source))
    return status


def gps_snapshot_source(pipeline, legacy: dict):
    legacy_gps = legacy.get("gps")
    if isinstance(legacy_gps, dict):
        return legacy_gps

    gps_status = getattr(pipeline, "gps_status", None)
    if callable(gps_status):
        return gps_status()

    gps = getattr(pipeline, "gps", None)
    if gps is None:
        return None
    for method_name in ("status", "get_status"):
        method = getattr(gps, method_name, None)
        if callable(method):
            return method()
    get_fix = getattr(gps, "get_fix", None)
    if callable(get_fix):
        return gps_fix_snapshot(get_fix())
    return None


def gps_fix_snapshot(fix) -> dict | None:
    if fix is None:
        return None
    return {
        "source": getattr(fix, "src", None),
        "target_age_sec": getattr(fix, "age_sec", None),
        "base_age_sec": None,
        "distance_m": None,
        "bearing_deg": getattr(fix, "course", None),
        "stale": False,
    }


def normalize_gps(status: dict) -> dict:
    return {
        "source": status.get("source"),
        "target_age_sec": status.get("target_age_sec", status.get("target_age_s")),
        "base_age_sec": status.get("base_age_sec", status.get("base_age_s")),
        "distance_m": status.get("distance_m"),
        "bearing_deg": status.get("bearing_deg"),
        "stale": bool(status.get("stale", False)),
    }


def unknown_gps() -> dict:
    return {
        "source": None,
        "target_age_sec": None,
        "base_age_sec": None,
        "distance_m": None,
        "bearing_deg": None,
        "stale": True,
    }


def unknown_media() -> dict:
    return {"recording": False, "segment_name": None, "free_gb": None}


def normalize_media(status: dict) -> dict:
    media = unknown_media()
    media.update(status)
    return media


def build_network(legacy: dict) -> dict:
    return {
        "camera_lan": bool(legacy.get("connected", False)),
        "uplink": None,
        "cloudflare": None,
    }


def map_axis(value: float, cfg, axis: str) -> tuple[int, int]:
    if axis == "pan":
        value = -value if getattr(cfg, "invert_pan", False) else value
        dirs = (PAN_LEFT, PAN_RIGHT, PAN_STOP)
        max_speed = int(getattr(cfg, "max_pan_speed", 10))
    else:
        value = -value if getattr(cfg, "invert_tilt", False) else value
        # Manual control values use joystick semantics: positive tilt means
        # operator requested camera-up. Visual servo image-error semantics are
        # handled separately in controller.py.
        dirs = (TILT_DOWN, TILT_UP, TILT_STOP)
        max_speed = int(getattr(cfg, "max_tilt_speed", 8))

    if value > 0:
        return dirs[1], scaled_speed(value, max_speed, cfg)
    if value < 0:
        return dirs[0], scaled_speed(-value, max_speed, cfg)
    return dirs[2], int(getattr(cfg, "min_speed", 1))


def scaled_speed(value: float, max_speed: int, cfg) -> int:
    min_speed = int(getattr(cfg, "min_speed", 1))
    return max(min_speed, min(max_speed, int(round(value * max_speed))))


def zoom_speed(value: float) -> int:
    return max(1, min(7, int(round(value * 7))))


def set_float(
    target: Any,
    attr: str,
    value: Any,
    lo: float,
    hi: float,
    dry_run: bool = False,
) -> str | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return f"{attr} must be a number."
    if parsed < lo or parsed > hi:
        return f"{attr} must be between {lo} and {hi}."
    if not dry_run:
        setattr(target, attr, parsed)
    return None


def set_int(
    target: Any,
    attr: str,
    value: Any,
    lo: int,
    hi: int,
    dry_run: bool = False,
) -> str | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return f"{attr} must be an integer."
    if parsed < lo or parsed > hi:
        return f"{attr} must be between {lo} and {hi}."
    if not dry_run:
        setattr(target, attr, parsed)
    return None


def set_bool(target: Any, attr: str, value: Any, dry_run: bool = False) -> str | None:
    if not isinstance(value, bool):
        return f"{attr} must be a boolean."
    if not dry_run:
        setattr(target, attr, value)
    return None
