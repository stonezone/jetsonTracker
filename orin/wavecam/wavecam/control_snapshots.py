"""Status and config snapshot builders for the WaveCam control API.

Pure data-assembly functions: they read pipeline/config attributes but
perform no I/O and have no FastAPI or auth dependencies.  Moved from
control_api.py so adapter classes and route handlers can import them
without pulling in the full monolith.
"""
from __future__ import annotations

import time
from typing import Any

from .control_utils import (
    HOT_CONFIG_KEYS,
    RESTART_REQUIRED_KEYS,
    YOLO_CLASSES,
    empty_calibration_state,
)
from .ptz_owner import IDLE
from .ptz_visca import PAN_LEFT, PAN_RIGHT, PAN_STOP, TILT_DOWN, TILT_STOP, TILT_UP
from .supervisor import read_health, snapshot_services


# ---------------------------------------------------------------------------
# Config snapshot
# ---------------------------------------------------------------------------

def build_config_snapshot(pipeline, revision: int, calibration: dict | None = None) -> dict:
    from .color_presets import COLOR_PRESETS
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
                "gps_boost": getattr(cfg.fusion, "gps_boost", 0.2),
                "gps_boost_radius_frac": getattr(cfg.fusion, "gps_boost_radius_frac", 0.25),
            },
            "gps": {
                "enabled": getattr(getattr(cfg, "gps", None), "enabled", False),
                "stale_threshold_sec": getattr(getattr(cfg, "gps", None), "stale_threshold_sec", 10.0),
                "grace_sec": getattr(getattr(cfg, "gps", None), "grace_sec", 1.0),
                "lock_frames": getattr(getattr(cfg, "gps", None), "lock_frames", 5),
                "drive_zoom": getattr(getattr(cfg, "gps", None), "drive_zoom", False),
                "max_pan_speed": getattr(getattr(cfg, "gps", None), "max_pan_speed", 4),
                "max_tilt_speed": getattr(getattr(cfg, "gps", None), "max_tilt_speed", 3),
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
                "show_hud": bool(getattr(pipeline.state, "show_hud", True)),
                "jpeg_quality": cfg.web.jpeg_quality,
            },
            "calibration": calibration or empty_calibration_state(),
            "estimator": {
                "shadow": getattr(getattr(cfg, "estimator", None), "shadow", True),
                "enabled": getattr(getattr(cfg, "estimator", None), "enabled", True),
                "q_accel": getattr(getattr(cfg, "estimator", None), "q_accel", 2.0),
                "p0_pos": getattr(getattr(cfg, "estimator", None), "p0_pos", 25.0),
                "p0_vel": getattr(getattr(cfg, "estimator", None), "p0_vel", 9.0),
                "r_gps_fresh": getattr(getattr(cfg, "estimator", None), "r_gps_fresh", 4.0),
                "r_gps_age_scale": getattr(getattr(cfg, "estimator", None), "r_gps_age_scale", 0.5),
                "r_vis_deg": getattr(getattr(cfg, "estimator", None), "r_vis_deg", 1.0),
                "zoom_cov_wide_deg": getattr(getattr(cfg, "estimator", None), "zoom_cov_wide_deg", 4.0),
                "zoom_cov_narrow_deg": getattr(getattr(cfg, "estimator", None), "zoom_cov_narrow_deg", 1.5),
                "log_every_n": getattr(getattr(cfg, "estimator", None), "log_every_n", 3),
            },
        },
        "supported": {
            "calibration": True,
            "cinematic_zoom": True,
            "color_presets": sorted(COLOR_PRESETS),
            "media": getattr(pipeline, "recorder", None) is not None,
            "media_delete": getattr(pipeline, "recorder", None) is not None,
            "presets": True,
            "logs": True,
            "ptz_home": callable(getattr(getattr(pipeline, "ptz", None), "home", None)),
            "show_hud": True,
            "gps": getattr(pipeline, "gps", None) is not None,
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


# ---------------------------------------------------------------------------
# Status snapshot
# ---------------------------------------------------------------------------

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
    threshold = getattr(getattr(pipeline, "cfg", None), "gps", None)
    threshold = getattr(threshold, "stale_threshold_sec", 10.0)
    status = unknown_gps()
    gps = getattr(pipeline, "gps", None)
    # Collect reader health from the live gps object before snapshot lookup so
    # these keys survive the status.update() below (normalize_gps passes them
    # through as None when they aren't in the source dict).
    reader_alive_val = None
    last_poll_age_val = None
    if gps is not None:
        ra = getattr(gps, "reader_alive", None)
        lp = getattr(gps, "last_poll_age_sec", None)
        reader_alive_val = ra() if callable(ra) else None
        last_poll_age_val = lp() if callable(lp) else None
    source = gps_snapshot_source(pipeline, legacy, threshold=threshold)
    if source is None:
        status["reader_alive"] = reader_alive_val
        status["last_poll_age_sec"] = last_poll_age_val
        return status
    status.update(normalize_gps(source))
    # Overlay the live reader health (takes precedence over any stale source dict value)
    if gps is not None:
        status["reader_alive"] = reader_alive_val
        status["last_poll_age_sec"] = last_poll_age_val
    return status


def gps_snapshot_source(pipeline, legacy: dict, threshold: float = 10.0):
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
        fix = get_fix()
        if fix is not None:
            return gps_fix_snapshot(fix, gps, threshold=threshold)
    return None


def gps_fix_snapshot(fix, gps=None, threshold: float = 10.0) -> dict | None:
    if fix is None:
        return None
    from .gps_meshtastic import bearing_deg, haversine_m

    target_age = getattr(fix, "age_sec", None)
    snapshot = {
        "source": getattr(fix, "src", None),
        "target_age_sec": target_age,
    }

    # Compute camera→target distance and bearing when both positions are available
    if gps is not None:
        cam = getattr(gps, "get_camera_position", None)
        cam_age = getattr(gps, "get_camera_age", None)
        if callable(cam) and callable(cam_age):
            cam_pos = cam()
            base_age = cam_age()
            if cam_pos is not None:
                dist = haversine_m(cam_pos[0], cam_pos[1], fix.lat, fix.lon)
                bearing = bearing_deg(cam_pos[0], cam_pos[1], fix.lat, fix.lon)
                snapshot["distance_m"] = round(dist, 1)
                snapshot["bearing_deg"] = round(bearing, 1)
                snapshot["base_age_sec"] = round(base_age, 1) if base_age is not None else None
                snapshot["stale"] = (
                    target_age is not None and target_age > threshold
                )
                return snapshot

    # Fallback: no camera position yet — bearing is null (fix.course is the
    # remote's heading-of-travel, not a camera→target bearing)
    snapshot.update({
        "distance_m": None,
        "bearing_deg": None,
        "base_age_sec": None,
        "stale": target_age is not None and target_age > threshold,
    })
    return snapshot


def normalize_gps(status: dict) -> dict:
    return {
        "source": status.get("source"),
        "target_age_sec": status.get("target_age_sec", status.get("target_age_s")),
        "base_age_sec": status.get("base_age_sec", status.get("base_age_s")),
        "distance_m": status.get("distance_m"),
        "bearing_deg": status.get("bearing_deg"),
        "stale": bool(status.get("stale", False)),
        "reader_alive": status.get("reader_alive"),
        "last_poll_age_sec": status.get("last_poll_age_sec"),
    }


def unknown_gps() -> dict:
    return {
        "source": None,
        "target_age_sec": None,
        "base_age_sec": None,
        "distance_m": None,
        "bearing_deg": None,
        "stale": True,
        "reader_alive": None,
        "last_poll_age_sec": None,
    }


def unknown_media() -> dict:
    return {
        "recording": False,
        "segment_name": None,
        "current_segment_name": None,
        "segment_pattern": None,
        "segment_prefix": None,
        "free_gb": None,
    }


def normalize_media(status: dict) -> dict:
    media = unknown_media()
    media.update(status)
    return media


def build_network(legacy: dict) -> dict:
    return {
        "camera_lan": bool(legacy.get("connected", False)),
        "uplink": None,
    }


# ---------------------------------------------------------------------------
# PTZ axis helpers
# ---------------------------------------------------------------------------

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
