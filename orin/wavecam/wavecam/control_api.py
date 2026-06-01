"""Release-shaped Control API adapter for WaveCam.

The existing web console is still the hardware bring-up surface. This module
adds the production-facing /api/v1 contract beside it, using the same pipeline,
PTZ owner gate, and PTZ backend.
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from typing import Any, Callable, Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .ptz_owner import IDLE
from .ptz_visca import PAN_LEFT, PAN_RIGHT, PAN_STOP, TILT_DOWN, TILT_STOP, TILT_UP


FrameSource = Callable[[], Any]


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
    deadman_ms: int = Field(default=800, ge=100, le=5000)
    source: str | None = None


class PtzStopRequest(BaseModel):
    source: str | None = None


class ZoomRequest(BaseModel):
    requested_owner: str = "manual"
    mode: str = "velocity"
    value: float = Field(default=0.0, ge=-1.0, le=1.0)
    deadman_ms: int = Field(default=800, ge=100, le=5000)
    source: str | None = None


class HotConfigRequest(BaseModel):
    revision: int | None = None
    patch: Dict[str, Any]
    persist: bool = False


def register_control_api(app: FastAPI, pipeline, frames: FrameSource) -> None:
    adapter = ControlApiAdapter(pipeline, frames)
    app.state.control_api = adapter
    register_status_routes(app, adapter)
    register_safety_routes(app, adapter)
    register_ptz_routes(app, adapter)
    register_config_routes(app, adapter)


def register_status_routes(app: FastAPI, api: "ControlApiAdapter") -> None:
    @app.get("/api/v1/status")
    def status():
        return api.status_snapshot()

    @app.get("/api/v1/preview.mjpeg")
    def preview():
        return StreamingResponse(
            api.frames(),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    @app.websocket("/api/v1/telemetry")
    async def telemetry(websocket: WebSocket):
        await websocket.accept()
        try:
            while True:
                await websocket.send_json(
                    {"type": "status", "revision": api.revision, "status": api.status_snapshot()}
                )
                await asyncio.sleep(0.5)
        except WebSocketDisconnect:
            return


def register_safety_routes(app: FastAPI, api: "ControlApiAdapter") -> None:
    @app.post("/api/v1/safety/kill")
    def safety_kill(_: SafetyKillRequest | None = None):
        api.pipeline.kill(True)
        api.cancel_manual_deadman()
        api.bump_revision()
        return api.ok()

    @app.post("/api/v1/safety/resume")
    def safety_resume(_: SafetyResumeRequest | None = None):
        api.resume_without_autostart()
        api.bump_revision()
        return api.ok()


def register_ptz_routes(app: FastAPI, api: "ControlApiAdapter") -> None:
    @app.post("/api/v1/ptz/stop")
    def ptz_stop(_: PtzStopRequest | None = None):
        api.stop_ptz()
        api.bump_revision()
        return api.ok()

    @app.post("/api/v1/ptz/velocity")
    def ptz_velocity(req: VelocityRequest):
        if api.pipeline.owner.killed:
            return api.refusal("killed", "KILL is latched; resume before movement commands.")
        if req.requested_owner != "manual":
            return api.refusal("invalid_request", "Only requested_owner=manual is accepted in v1.", 422)
        if not api.pipeline.owner.request("manual"):
            return api.refusal("owner_busy", "Another PTZ owner holds the camera.")

        api.send_manual_velocity(req)
        api.schedule_manual_deadman(req.deadman_ms)
        api.bump_revision()
        return api.ok()

    @app.post("/api/v1/ptz/zoom")
    def ptz_zoom(req: ZoomRequest):
        if api.pipeline.owner.killed:
            return api.refusal("killed", "KILL is latched; resume before movement commands.")
        if req.requested_owner != "manual":
            return api.refusal("invalid_request", "Only requested_owner=manual is accepted in v1.", 422)
        if req.mode != "velocity":
            return api.refusal("invalid_request", "Only mode=velocity is accepted in v1.", 422)
        if not api.pipeline.owner.request("manual"):
            return api.refusal("owner_busy", "Another PTZ owner holds the camera.")

        api.send_manual_zoom_velocity(req.value)
        if req.value == 0:
            api.cancel_manual_deadman()
            api.pipeline.owner.release("manual")
        else:
            api.schedule_manual_deadman(req.deadman_ms)
        api.bump_revision()
        return api.ok()


def register_config_routes(app: FastAPI, api: "ControlApiAdapter") -> None:
    @app.post("/api/v1/config/hot")
    def config_hot(req: HotConfigRequest):
        refusal = api.apply_hot_config(req.patch)
        if refusal is not None:
            return refusal
        api.bump_revision()
        return api.ok()


class ControlApiAdapter:
    """Small state holder for /api/v1 command behavior."""

    def __init__(self, pipeline, frames: FrameSource) -> None:
        self.pipeline = pipeline
        self.frames = frames
        self._lock = threading.Lock()
        self._revision = 0
        self._manual_deadman: threading.Timer | None = None

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def bump_revision(self) -> None:
        with self._lock:
            self._revision += 1

    def status_snapshot(self) -> dict:
        return build_status_snapshot(self.pipeline, self.revision)

    def ok(self) -> JSONResponse:
        return JSONResponse(
            {"ok": True, "request_id": make_request_id(), "status": self.status_snapshot()}
        )

    def refusal(self, code: str, message: str, status_code: int = 409) -> JSONResponse:
        return JSONResponse(
            {"ok": False, "code": code, "message": message, "status": self.status_snapshot()},
            status_code=status_code,
        )

    def resume_without_autostart(self) -> None:
        self.pipeline.state.killed = False
        self.pipeline.owner.resume()
        if self.pipeline.owner.owner != IDLE:
            self.pipeline.owner.release(self.pipeline.owner.owner)
        self.pipeline.state.set_status(killed=False, state="SEARCHING")

    def stop_ptz(self) -> None:
        self.cancel_manual_deadman()
        self.pipeline.ptz.stop()
        self.pipeline.ptz.zoom("stop")
        if self.pipeline.owner.owner != IDLE:
            self.pipeline.owner.release(self.pipeline.owner.owner)

    def send_manual_velocity(self, req: VelocityRequest) -> None:
        cfg = self.pipeline.cfg.ptz
        pan_dir, pan_speed = map_axis(req.pan, cfg, "pan")
        tilt_dir, tilt_speed = map_axis(req.tilt, cfg, "tilt")

        if pan_dir == PAN_STOP and tilt_dir == TILT_STOP and req.zoom == 0:
            self.pipeline.ptz.stop()
            self.pipeline.ptz.zoom("stop")
            self.pipeline.owner.release("manual")
            return

        if pan_dir == PAN_STOP and tilt_dir == TILT_STOP:
            self.pipeline.ptz.stop()
        else:
            self.pipeline.ptz.pan_tilt(pan_speed, tilt_speed, pan_dir, tilt_dir)
        self.send_manual_zoom(req.zoom)

    def send_manual_zoom_velocity(self, zoom: float) -> None:
        if zoom == 0:
            self.pipeline.ptz.zoom("stop")
            return
        self.send_manual_zoom(zoom)

    def send_manual_zoom(self, zoom: float) -> None:
        if zoom > 0:
            self.pipeline.ptz.zoom("tele", zoom_speed(zoom))
        elif zoom < 0:
            self.pipeline.ptz.zoom("wide", zoom_speed(-zoom))

    def schedule_manual_deadman(self, deadman_ms: int) -> None:
        self.cancel_manual_deadman()
        timer = threading.Timer(deadman_ms / 1000.0, self.manual_deadman_expired)
        timer.daemon = True
        self._manual_deadman = timer
        timer.start()

    def cancel_manual_deadman(self) -> None:
        if self._manual_deadman is not None:
            self._manual_deadman.cancel()
            self._manual_deadman = None

    def manual_deadman_expired(self) -> None:
        if self.pipeline.owner.owner == "manual":
            self.pipeline.ptz.stop()
            self.pipeline.ptz.zoom("stop")
            self.pipeline.owner.release("manual")
            self.bump_revision()

    def apply_hot_config(self, patch: Dict[str, Any]) -> JSONResponse | None:
        for key, value in patch.items():
            refusal = self.apply_hot_key(key, value)
            if refusal is not None:
                return refusal
        return None

    def apply_hot_key(self, key: str, value: Any) -> JSONResponse | None:
        cfg = self.pipeline.cfg
        setters = {
            "ptz.deadzone": lambda: set_float(cfg.ptz, "deadzone", value, 0.02, 0.30),
            "ptz.max_pan_speed": lambda: set_int(cfg.ptz, "max_pan_speed", value, 1, 24),
            "ptz.max_tilt_speed": lambda: set_int(cfg.ptz, "max_tilt_speed", value, 1, 20),
            "ptz.invert_pan": lambda: set_bool(cfg.ptz, "invert_pan", value),
            "ptz.invert_tilt": lambda: set_bool(cfg.ptz, "invert_tilt", value),
            "fusion.lock_threshold": lambda: set_float(cfg.fusion, "lock_threshold", value, 0.05, 0.95),
            "fusion.unlock_threshold": lambda: set_float(cfg.fusion, "unlock_threshold", value, 0.05, 0.95),
            "color.min_area": lambda: set_int(cfg.color, "min_area", value, 20, 4000),
            "web.show_mask": lambda: set_bool(self.pipeline.state, "show_mask", value),
        }
        setter = setters.get(key)
        if setter is None:
            return self.refusal("invalid_request", f"{key} is not a hot-config key.", 422)
        error = setter()
        if error is not None:
            return self.refusal("invalid_request", error, 422)
        return None


def make_request_id() -> str:
    ms = int(time.time() * 1000) % 1000
    return f"{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}.{ms:03d}Z-{uuid.uuid4().hex[:8]}"


def build_status_snapshot(pipeline, revision: int) -> dict:
    legacy = merged_status(pipeline)
    return {
        "revision": revision,
        "time_unix_ms": int(time.time() * 1000),
        "session": build_session(legacy),
        "safety": build_safety(legacy),
        "ptz": build_ptz(legacy, pipeline),
        "tracking": build_tracking(legacy),
        "gps": unknown_gps(),
        "media": unknown_media(),
        "services": unknown_services(),
        "network": build_network(legacy),
    }


def merged_status(pipeline) -> dict:
    status = pipeline.state.get_status()
    status.update(pipeline.owner.state())
    return status


def build_session(legacy: dict) -> dict:
    return {
        "state": str(legacy.get("state", "UNKNOWN")),
        "mode": "testbed",
        "started_at_unix_ms": None,
    }


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
        "zoom_state": "hold",
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


def unknown_services() -> dict:
    return {
        "wavecam": "unknown",
        "gps_server": "unknown",
        "dashboard": "unknown",
        "cloudflared": "unknown",
        "supervisor": "unknown",
    }


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
        dirs = (TILT_UP, TILT_DOWN, TILT_STOP)
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


def set_float(target: Any, attr: str, value: Any, lo: float, hi: float) -> str | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return f"{attr} must be a number."
    if parsed < lo or parsed > hi:
        return f"{attr} must be between {lo} and {hi}."
    setattr(target, attr, parsed)
    return None


def set_int(target: Any, attr: str, value: Any, lo: int, hi: int) -> str | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return f"{attr} must be an integer."
    if parsed < lo or parsed > hi:
        return f"{attr} must be between {lo} and {hi}."
    setattr(target, attr, parsed)
    return None


def set_bool(target: Any, attr: str, value: Any) -> str | None:
    if not isinstance(value, bool):
        return f"{attr} must be a boolean."
    setattr(target, attr, value)
    return None
