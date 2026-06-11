"""Release-shaped Control API adapter for WaveCam.

The existing web console is still the hardware bring-up surface. This module
adds the production-facing /api/v1 contract beside it, using the same pipeline,
PTZ owner gate, and PTZ backend.
"""
from __future__ import annotations

import asyncio
import json
import os
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
from .config import persist_hot_values
from .control_calibration import CalibrationManager
from .control_logs import LogAdapter
from .control_presets import PresetStore
from .control_ptz import PtzDispatcher
from .control_media import (
    MediaAdapter,
    MediaNotFound,
    MediaUnavailable,
    media_ok,
)
from .control_snapshots import (
    build_config_snapshot,
    build_gps,
    build_status_snapshot,
    gps_fix_snapshot,
    map_axis,
    zoom_speed,
)
from .control_utils import (
    HOT_CONFIG_KEYS,
    RESTART_REQUIRED_KEYS,
    make_request_id,
    nested_current_value,
    normalized_optional_text,
    normalized_text,
    set_bool,
    set_float,
    set_int,
)
from .ptz_owner import AUTONOMOUS, IDLE
from .supervisor import restart_systemd_unit


FrameSource = Callable[[], Any]

GUIDE_FILENAME = "WaveCam_Guide.html"
GUIDE_ASSET_DIR = "guide_assets"
GUIDE_ROOT_ENV = "WAVECAM_GUIDE_ROOT"


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


class PresetSaveRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    values: Dict[str, Any] | None = None
    capture_current: bool = False


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
    register_guide_routes(app)
    register_version_routes(app)
    register_status_routes(app, adapter)
    register_safety_routes(app, adapter)
    register_ptz_routes(app, adapter)
    register_calibration_routes(app, adapter)
    register_media_routes(app, adapter)
    register_preset_routes(app, adapter)
    register_log_routes(app, adapter)
    register_config_routes(app, adapter)
    register_system_routes(app, adapter)
    register_agent_routes(app, adapter)
    register_health_routes(app, adapter)
    register_events_routes(app, adapter)


def register_version_routes(app: FastAPI) -> None:
    @app.get("/api/v1/version", dependencies=[Depends(require(READ))])
    def version():
        path = os.environ.get(
            "WAVECAM_VERSION_PATH",
            os.path.join(os.path.dirname(__file__), "..", "version.json"),
        )
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        return {
            "git_sha": data.get("git_sha"),
            "branch": data.get("branch"),
            "deployed_at": data.get("deployed_at"),
        }


def register_guide_routes(app: FastAPI) -> None:
    @app.get("/guide", dependencies=[Depends(require(READ))])
    def guide():
        path = find_guide_file()
        if path is None:
            return JSONResponse({"ok": False, "code": "guide_not_found"}, status_code=404)
        return FileResponse(path, media_type="text/html")

    @app.get("/guide_assets/{asset_path:path}", dependencies=[Depends(require(READ))])
    def guide_asset(asset_path: str):
        path = find_guide_asset(asset_path)
        if path is None:
            return JSONResponse({"ok": False, "code": "guide_asset_not_found"}, status_code=404)
        return FileResponse(path)


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

    @app.post("/api/v1/calibration/base-lock", dependencies=[Depends(require(PTZ))])
    def calibration_base_lock(req: CalibrationBaseRequest):
        refusal = api.validate_calibration_capture(req)
        if refusal is not None:
            return refusal
        if api.pipeline.gps is None or api.pipeline.gps.get_camera_position() is None:
            return api.refusal("gps_unavailable", "Base GPS has no fix yet.", 503)
        api.capture_calibration("base_lock", {
            "source": normalized_text(req.source, "unknown", 64),
            "note": normalized_optional_text(req.note, 256),
        })
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

    @app.delete("/api/v1/media/{name}", dependencies=[Depends(require(CONFIG))])
    def media_delete(name: str):
        try:
            result = api.media.delete_file(name)
        except MediaUnavailable as exc:
            return api.refusal("media_unavailable", exc.message, 503)
        except MediaNotFound as exc:
            return api.refusal("media_not_found", exc.message, 404)
        api.bump_revision()
        return JSONResponse(
            {
                "ok": True,
                "request_id": make_request_id(),
                "name": result["name"],
                "freed_bytes": result["freed_bytes"],
                "status": api.status_snapshot(),
            }
        )

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


def register_preset_routes(app: FastAPI, api: "ControlApiAdapter") -> None:
    @app.get("/api/v1/presets", dependencies=[Depends(require(READ))])
    def presets_get():
        return api.presets.list_response()

    @app.post("/api/v1/presets", dependencies=[Depends(require(CONFIG))])
    def preset_save(req: PresetSaveRequest):
        return api.presets.save_response(req)

    @app.post("/api/v1/presets/{name}/apply", dependencies=[Depends(require(CONFIG))])
    def preset_apply(name: str):
        return api.presets.apply_response(name)

    @app.delete("/api/v1/presets/{name}", dependencies=[Depends(require(CONFIG))])
    def preset_delete(name: str):
        return api.presets.delete_response(name)


def register_log_routes(app: FastAPI, api: "ControlApiAdapter") -> None:
    @app.get("/api/v1/logs", dependencies=[Depends(require(READ))])
    def logs_get(level: str | None = None, limit: int = 200, since: int | None = None):
        return api.logs.response(level=level, limit=limit, since=since)


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
        # Persist the successfully applied keys back to the live yaml so the rig file
        # is always the single source of truth. Failure must not fail the request —
        # the in-memory apply already succeeded.
        # Read post-coercion values from the live cfg rather than persisting req.patch
        # directly — set_float/set_int coerce e.g. "0.3" → 0.3, so persisting the raw
        # request string would write a yaml string and corrupt the dataclass type on restart.
        src = getattr(getattr(api.pipeline, "cfg", None), "source_path", "")
        if src and req.patch:
            try:
                coerced = {}
                for dotted in req.patch:
                    section, attr = dotted.split(".", 1)
                    coerced[dotted] = getattr(getattr(api.pipeline.cfg, section), attr)
                persist_hot_values(src, coerced)
            except Exception as e:
                print(f"[control_api] hot-config persist failed (live value still applied): {e}")
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


def register_events_routes(app: FastAPI, api: "ControlApiAdapter") -> None:
    @app.get("/api/v1/events", dependencies=[Depends(require(READ))])
    def events(since: float = 0.0):
        ring = getattr(api.pipeline, "events", None)
        items = ring.since(since) if ring is not None else []
        return {"events": items}


def register_health_routes(app: FastAPI, api: "ControlApiAdapter") -> None:
    @app.get("/api/v1/health", dependencies=[Depends(require(READ))])
    def health():
        reg = getattr(api.pipeline, "health", None)
        snap = reg.snapshot() if reg else {"ok": False, "components": {}}
        gps = getattr(api.pipeline, "gps", None)
        if gps is not None:
            alive = gps.reader_alive() if callable(getattr(gps, "reader_alive", None)) else None
            age = gps.last_poll_age_sec() if callable(getattr(gps, "last_poll_age_sec", None)) else None
            snap["components"]["gps_reader"] = {"ok": bool(alive), "age_sec": age, "detail": {}}
            snap["ok"] = snap["ok"] and bool(alive)
        try:
            import shutil
            free_gb = shutil.disk_usage(str(api.pipeline.recorder.config.rec_dir)).free / 1e9
            snap["components"]["disk"] = {"ok": free_gb > 5.0, "age_sec": 0,
                                          "detail": {"free_gb": round(free_gb, 1)}}
        except Exception:
            pass
        return snap


class ControlApiAdapter:
    """Small state holder for /api/v1 command behavior."""

    def __init__(self, pipeline, frames: FrameSource) -> None:
        self.pipeline = pipeline
        self.frames = frames
        self.media = MediaAdapter(getattr(pipeline, "recorder", None))
        self._lock = threading.RLock()
        self._revision = 0
        self._restart_timer: threading.Timer | None = None
        self._restart_pending = False
        self._restart_unit = "wavecam.service"
        # Unified calibration store — replaces split _calibration dict + camera_pose.json.
        # One file holds pose, reference_heading, and step log so a restart can no longer
        # give "gps_calibrated true but reference_heading null".
        _pose_path = os.environ.get(
            "WAVECAM_POSE_PATH",
            os.path.join(os.path.dirname(__file__), "..", "..", "camera_pose.json"),
        )
        from .calibration_store import CalibrationStore
        self._store = CalibrationStore.load(_pose_path)
        # The pipeline must point at the SAME CameraPose object the store owns so that
        # GPS/pointing code always reads the live calibration and we never have two copies.
        pipeline.pose = self._store.pose
        if self._store.pose.calibrated:
            print(f"[control_api] loaded calibrated pose from {_pose_path}")
        self._pending_restart_config: dict[str, Any] = {}
        self._ptz = PtzDispatcher(pipeline, self.bump_revision)
        self._calibration = CalibrationManager(self._store, pipeline, self._lock, self)
        self.presets = PresetStore(self)
        self.logs = LogAdapter(self)

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
        snapshot = build_config_snapshot(self.pipeline, self.revision, self.calibration_state())
        with self._lock:
            pending_restart = dict(self._pending_restart_config)
        snapshot["pending_restart"] = pending_restart
        snapshot["restart_required"] = bool(pending_restart)
        return snapshot

    def ok(self) -> JSONResponse:
        return JSONResponse(
            {"ok": True, "request_id": make_request_id(), "status": self.status_snapshot()}
        )

    def refusal(self, code: str, message: str, status_code: int = 409) -> JSONResponse:
        return JSONResponse(
            {"ok": False, "code": code, "message": message, "status": self.status_snapshot()},
            status_code=status_code,
        )

    def current_preset_values(self) -> dict[str, Any]:
        current = build_config_snapshot(
            self.pipeline,
            self.revision,
            self.calibration_state(),
        )["current"]
        values: dict[str, Any] = {}
        for key in HOT_CONFIG_KEYS:
            value = nested_current_value(current, key)
            if value is not None:
                values[key] = value
        return values

    def stage_restart_config(self, patch: dict[str, Any]) -> None:
        with self._lock:
            self._pending_restart_config.update(patch)

    # --- Calibration delegation stubs ---

    def calibration_ok(self) -> JSONResponse:
        return self._calibration.calibration_ok()

    def calibration_state(self) -> dict:
        return self._calibration.calibration_state()

    def validate_calibration_capture(self, req: CalibrationBaseRequest) -> JSONResponse | None:
        return self._calibration.validate_calibration_capture(req)

    def capture_calibration(self, step: str, values: dict) -> None:
        self._calibration.capture_calibration(step, values)

    def resume_without_autostart(self) -> None:
        self._ptz.cancel_manual_deadman()
        self._ptz.cancel_zoom_deadman()
        self._ptz.reset_restore_owner()
        with self._lock:
            self.pipeline.state.killed = False
            self.pipeline.owner.resume()
            if self.pipeline.owner.owner != IDLE:
                self.pipeline.owner.release(self.pipeline.owner.owner)
            self.pipeline.state.set_status(killed=False, state="SEARCHING")

    # --- PTZ delegation stubs (behavior lives in PtzDispatcher) ---

    def claim_manual(self, takeover: bool = False) -> bool:
        return self._ptz.claim_manual(takeover)

    def release_manual_owner(self, restore_autonomous: bool = True) -> None:
        self._ptz.release_manual_owner(restore_autonomous)

    def start_autonomous(self, owner: str) -> bool:
        return self._ptz.start_autonomous(owner)

    def stop_ptz(self, hold: bool = True) -> None:
        self._ptz.stop_ptz(hold)

    def home_ptz(self) -> None:
        self._ptz.home_ptz()

    def hold_manual_owner(self) -> None:
        self._ptz.hold_manual_owner()

    def send_manual_velocity(self, req: VelocityRequest) -> None:
        self._ptz.send_manual_velocity(req)

    def send_manual_zoom_velocity(self, zoom: float, deadman_ms: int = 800) -> None:
        self._ptz.send_manual_zoom_velocity(zoom, deadman_ms)

    def send_manual_zoom(self, zoom: float, deadman_ms: int = 800) -> None:
        self._ptz.send_manual_zoom(zoom, deadman_ms)

    @property
    def manual_pan_tilt_active(self) -> bool:
        return self._ptz.manual_pan_tilt_active

    def schedule_manual_deadman(self, deadman_ms: int) -> int:
        return self._ptz.schedule_manual_deadman(deadman_ms)

    def cancel_manual_deadman(self) -> None:
        self._ptz.cancel_manual_deadman()

    def schedule_zoom_deadman(self, deadman_ms: int) -> int:
        return self._ptz.schedule_zoom_deadman(deadman_ms)

    def cancel_zoom_deadman(self) -> None:
        self._ptz.cancel_zoom_deadman()

    def zoom_deadman_expired(self, generation: int | None = None) -> None:
        self._ptz.zoom_deadman_expired(generation)

    def manual_deadman_expired(self, generation: int | None = None) -> None:
        self._ptz.manual_deadman_expired(generation)

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
            "fusion.gps_boost": lambda: set_float(cfg.fusion, "gps_boost", value, 0.0, 0.5, dry_run=dry_run),
            "fusion.gps_boost_radius_frac": lambda: set_float(cfg.fusion, "gps_boost_radius_frac", value, 0.05, 0.75, dry_run=dry_run),
            "gps.stale_threshold_sec": lambda: self.apply_gps_float("stale_threshold_sec", value, 1.0, 120.0, dry_run=dry_run),
            "gps.grace_sec": lambda: self.apply_gps_float("grace_sec", value, 0.1, 10.0, dry_run=dry_run),
            "gps.lock_frames": lambda: self.apply_gps_int("lock_frames", value, 1, 30, dry_run=dry_run),
            "gps.drive_zoom": lambda: self.apply_gps_bool("drive_zoom", value, dry_run=dry_run),
            "gps.max_pan_speed": lambda: self.apply_gps_int("max_pan_speed", value, 1, 24, dry_run=dry_run),
            "gps.max_tilt_speed": lambda: self.apply_gps_int("max_tilt_speed", value, 1, 20, dry_run=dry_run),
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
            "web.show_hud": lambda: set_bool(self.pipeline.state, "show_hud", value, dry_run=dry_run),
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

    def _gps_cfg(self):
        """Return cfg.gps, or None if GPS is disabled/absent."""
        return getattr(self.pipeline.cfg, "gps", None)

    def apply_gps_float(self, attr: str, value: Any, lo: float, hi: float,
                        dry_run: bool = False) -> str | None:
        gps_cfg = self._gps_cfg()
        if gps_cfg is None:
            return f"gps.{attr}: GPS section not present in config."
        error = set_float(gps_cfg, attr, value, lo, hi, dry_run=dry_run)
        if error is not None:
            return error
        if not dry_run:
            self._sync_arbiter_from_gps()
        return None

    def apply_gps_int(self, attr: str, value: Any, lo: int, hi: int,
                      dry_run: bool = False) -> str | None:
        gps_cfg = self._gps_cfg()
        if gps_cfg is None:
            return f"gps.{attr}: GPS section not present in config."
        error = set_int(gps_cfg, attr, value, lo, hi, dry_run=dry_run)
        if error is not None:
            return error
        if not dry_run:
            self._sync_arbiter_from_gps()
        return None

    def apply_gps_bool(self, attr: str, value: Any, dry_run: bool = False) -> str | None:
        gps_cfg = self._gps_cfg()
        if gps_cfg is None:
            return f"gps.{attr}: GPS section not present in config."
        error = set_bool(gps_cfg, attr, value, dry_run=dry_run)
        if error is not None:
            return error
        return None

    def _sync_arbiter_from_gps(self) -> None:
        """Push hot-updated gps.lock_frames / gps.grace_sec into the running arbiter."""
        arbiter = getattr(self.pipeline, "arbiter", None)
        gps_cfg = self._gps_cfg()
        if arbiter is None or gps_cfg is None:
            return
        arbiter.lock_frames = int(getattr(gps_cfg, "lock_frames", arbiter.lock_frames))
        arbiter.grace_sec = float(getattr(gps_cfg, "grace_sec", arbiter.grace_sec))

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
        self._ptz.cancel_manual_deadman()
        self._ptz.cancel_zoom_deadman()
        self._ptz.reset_restore_owner()
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


def guide_root_candidates() -> list[Path]:
    env_root = os.environ.get(GUIDE_ROOT_ENV)
    if env_root:
        return [Path(env_root).resolve()]

    candidates: list[Path] = []
    cwd = Path.cwd()
    candidates.extend([cwd / "docs", cwd.parent / "docs"])

    module = Path(__file__).resolve()
    parents = list(module.parents)
    for idx in (1, 2, 3):
        if idx < len(parents):
            candidates.append(parents[idx] / "docs")

    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def find_guide_file() -> Path | None:
    for root in guide_root_candidates():
        path = root / GUIDE_FILENAME
        if path.is_file():
            return path
    return None


def find_guide_asset(asset_path: str) -> Path | None:
    requested = Path(asset_path)
    if requested.is_absolute() or ".." in requested.parts:
        return None
    for root in guide_root_candidates():
        asset_root = (root / GUIDE_ASSET_DIR).resolve()
        path = (asset_root / requested).resolve()
        if path.is_file() and path.is_relative_to(asset_root):
            return path
    return None



