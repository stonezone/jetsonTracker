"""Pure utility helpers for the WaveCam control API.

All functions here are side-effect-free: no I/O, no FastAPI imports, no
dependency on ControlApiAdapter or pipeline.  Moved from control_api.py so
that the snapshot builders, adapter classes, and route handlers can all import
from a single low-level module.
"""
from __future__ import annotations

import re
import time
import uuid
from typing import Any


# ---------------------------------------------------------------------------
# Constants (moved from control_api.py)
# ---------------------------------------------------------------------------

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
    "fusion.match_dist_scale",
    "fusion.person_aim_x",
    "fusion.person_aim_y",
    "fusion.gps_boost",
    "fusion.gps_boost_radius_frac",
    "fusion.gps_roi_enabled",
    "fusion.gps_bearing_cue_enabled",
    "gps.stale_threshold_sec",
    "gps.drive_stale_sec",
    "gps.grace_sec",
    "gps.lock_frames",
    "gps.drive_zoom",
    "gps.drive_zoom_near_m",
    "gps.drive_zoom_far_m",
    "gps.drive_zoom_max_enc",
    "gps.drive_zoom_max_frac",
    "gps.base_drift_enabled",
    "gps.max_pan_speed",
    "gps.max_tilt_speed",
    "tracking.mode",
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
    "web.show_hud",
    "web.jpeg_quality",
    "estimator.shadow",
    "estimator.enabled",
    "estimator.q_accel",
    "estimator.p0_pos",
    "estimator.p0_vel",
    "estimator.r_gps_fresh",
    "estimator.r_gps_age_scale",
    "estimator.r_vis_deg",
    "estimator.zoom_cov_wide_deg",
    "estimator.zoom_cov_narrow_deg",
    "estimator.log_every_n",
    "sensors.enabled",
    "sensors.drift_alert_deg",
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
    "detector.tracker",
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

BUILTIN_PRESET_VALUES: dict[str, dict[str, Any]] = {
    "Tow Foil": {
        "fusion.require_person": False,
        "ptz.max_pan_speed": 18,
        "ptz.max_tilt_speed": 12,
        "ptz.deadzone": 0.10,
        "ptz.ff_gain": 0.30,
        "ptz.zoom_target_frac": 0.35,
        "fusion.person_aim_y": 0.45,
    },
    "Wing Foil": {
        "fusion.require_person": False,
        "ptz.max_pan_speed": 12,
        "ptz.max_tilt_speed": 9,
        "ptz.deadzone": 0.08,
        "ptz.ff_gain": 0.15,
        "ptz.zoom_target_frac": 0.45,
    },
    "Land Chase": {
        "fusion.require_person": True,
        "ptz.max_pan_speed": 16,
        "ptz.max_tilt_speed": 12,
        "ptz.deadzone": 0.06,
        "ptz.ff_gain": 0.25,
        "ptz.zoom_target_frac": 0.55,
        "fusion.person_aim_y": 0.50,
    },
    "Sunny": {
        "detector.conf": 0.40,
        "web.jpeg_quality": 80,
    },
    "Cloudy": {
        "detector.conf": 0.30,
    },
}

DEFAULT_PRESET_STORE_PATH = "/data/wavecam/presets.json"
PRESET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")
LOG_LEVELS = frozenset({"debug", "info", "warning", "error"})
LOG_UNITS = ("wavecam.service", "wavecam-supervisor.service")
SYSLOG_PRIORITY_LEVELS = {
    "0": "error",
    "1": "error",
    "2": "error",
    "3": "error",
    "4": "warning",
    "5": "info",
    "6": "info",
    "7": "debug",
}
SENSITIVE_LOG_PATTERNS = (
    (
        re.compile(r"(?i)\b(authorization\s*:\s*bearer)\s+[A-Za-z0-9._~+/\-=]+"),
        r"\1 <redacted>",
    ),
    (
        re.compile(r"(?i)\b(token|api[_-]?key|secret|password|key)\s*[:=]\s*[^ \t,;]+"),
        r"\1=<redacted>",
    ),
    (re.compile(r"/Users/[^ \t,;:]+"), "<home>"),
    (re.compile(r"/home/[^ \t,;:]+"), "<home>"),
    (re.compile(r"(?i)[^ \t,;:]*\.env[^ \t,;:]*"), "<redacted-path>"),
)


# ---------------------------------------------------------------------------
# String helpers
# ---------------------------------------------------------------------------

def normalized_text(value: str | None, fallback: str, max_len: int) -> str:
    text = (value or "").strip()
    if not text:
        return fallback
    return text[:max_len]


def normalized_optional_text(value: str | None, max_len: int) -> str | None:
    text = (value or "").strip()
    return text[:max_len] if text else None


# ---------------------------------------------------------------------------
# Calibration state helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Preset helpers
# ---------------------------------------------------------------------------

def preset_store_path(pipeline) -> "Path":  # noqa: F821
    import os
    from pathlib import Path
    raw_path = (
        getattr(pipeline, "preset_store_path", None)
        or os.environ.get("WAVECAM_PRESETS_FILE")
        or DEFAULT_PRESET_STORE_PATH
    )
    return Path(raw_path)


def normalized_preset_name(value: Any) -> str | None:
    name = str(value or "").strip()
    if not PRESET_NAME_RE.match(name):
        return None
    return name


def canonical_preset_values(values: dict[str, Any]) -> dict[str, Any]:
    return {str(key): values[key] for key in sorted(values)}


def split_preset_values(values: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    hot_patch: dict[str, Any] = {}
    restart_patch: dict[str, Any] = {}
    for key, value in values.items():
        if key in HOT_CONFIG_KEYS:
            hot_patch[key] = value
        elif key in RESTART_REQUIRED_KEYS:
            restart_patch[key] = value
    return hot_patch, restart_patch


def preset_payload(name: str, values: dict[str, Any], builtin: bool) -> dict:
    restart_keys = [key for key in values if key in RESTART_REQUIRED_KEYS]
    return {
        "name": name,
        "builtin": builtin,
        "values": dict(values),
        "restart_required": bool(restart_keys),
        "restart_keys": restart_keys,
    }


def nested_current_value(current: dict, key: str) -> Any:
    node: Any = current
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


# ---------------------------------------------------------------------------
# Log helpers
# ---------------------------------------------------------------------------

def bounded_log_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = 200
    return max(1, min(value, 500))


def normalized_log_level(level: str | None) -> str | None:
    if level is None:
        return None
    normalized = str(level).strip().lower()
    return normalized if normalized in LOG_LEVELS else None


def normalized_log_source(source: Any) -> str | None:
    text = str(source or "").strip()
    if text == "wavecam.service":
        return "wavecam.service"
    if text in {"supervisor", "wavecam-supervisor.service"}:
        return "supervisor"
    return None


def redact_log_message(message: Any) -> str:
    text = str(message or "")
    for pattern, replacement in SENSITIVE_LOG_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def normalize_log_line(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    source = normalized_log_source(
        raw.get("source") or raw.get("_SYSTEMD_UNIT") or raw.get("unit")
    )
    if source is None:
        return None
    level = normalized_log_level(raw.get("level"))
    if level is None:
        level = SYSLOG_PRIORITY_LEVELS.get(str(raw.get("PRIORITY")), "info")
    ts_unix_ms = log_timestamp_ms(raw)
    if ts_unix_ms is None:
        ts_unix_ms = int(time.time() * 1000)
    return {
        "ts_unix_ms": ts_unix_ms,
        "level": level,
        "source": source,
        "message": redact_log_message(raw.get("message") or raw.get("MESSAGE")),
    }


def log_timestamp_ms(raw: dict) -> int | None:
    for key in ("ts_unix_ms", "timestamp_ms"):
        value = raw.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    realtime_us = raw.get("__REALTIME_TIMESTAMP")
    if realtime_us is not None:
        try:
            return int(int(realtime_us) / 1000)
        except (TypeError, ValueError):
            return None
    return None


def make_request_id() -> str:
    ms = int(time.time() * 1000) % 1000
    return f"{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}.{ms:03d}Z-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Typed config setters
# ---------------------------------------------------------------------------

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
