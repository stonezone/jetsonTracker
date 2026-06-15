"""Config loading and typed access. Single responsibility: parse YAML -> dataclasses."""
from __future__ import annotations
import os
import threading
from dataclasses import dataclass, field
from typing import Any
import yaml

_persist_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Overlay helpers
# ---------------------------------------------------------------------------

def _overlay_path(main_yaml_path: str) -> str:
    """Return the path to config.local.yaml in the same directory as the main YAML."""
    return os.path.join(os.path.dirname(os.path.abspath(main_yaml_path)), "config.local.yaml")


def persist_hot_values(yaml_path: str, values: dict) -> None:
    """Write hot-applied config keys to config.local.yaml (overlay), never the main YAML.

    The overlay lives alongside the main config and is excluded from rsync so it
    survives deploys.  ``values`` maps dotted keys ("gps.stale_threshold_sec") to
    post-coercion scalars (float/int/bool/str — never raw request strings).
    """
    overlay = _overlay_path(yaml_path)
    with _persist_lock:
        try:
            with open(overlay, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except FileNotFoundError:
            data = {}
        for dotted, v in values.items():
            section, key = dotted.split(".", 1)
            data.setdefault(section, {})[key] = v
        tmp = overlay + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp, overlay)


def _d(src: dict, key: str, default: Any) -> Any:
    v = src.get(key, default)
    return default if v is None else v


@dataclass
class CameraCfg:
    source: Any = 0
    use_gstreamer: bool = False
    codec: str = "h264"
    reconnect_sec: float = 2.0


@dataclass
class PtzCfg:
    enabled: bool = False
    ip: str = "192.168.1.50"
    port: int = 1259
    address: int = 1
    reset_sequence: bool = True
    invert_pan: bool = False
    invert_tilt: bool = False
    deadzone: float = 0.08
    max_pan_speed: int = 10
    max_tilt_speed: int = 8
    min_speed: int = 1
    command_min_interval: float = 0.05
    ff_gain: float = 0.0
    ff_deadzone_mult: float = 1.5
    cinematic_zoom_enabled: bool = False
    zoom_target_frac: float = 0.5
    zoom_deadband: float = 0.06
    zoom_max_speed: int = 5


@dataclass
class CameraAiCfg:
    disable_on_start: bool = True
    http_base: str = ""
    off_path: str = ""
    verify_path: str = ""
    # Camera factory defaults (documented in the Prisual manual); override in
    # YAML if the camera's web login was ever changed.
    http_user: str = "admin"
    http_pass: str = "admin"


@dataclass
class ColorCfg:
    enabled: bool = True
    preset: str = "orange_red"
    min_area: int = 60
    max_area: int = 200000
    hsv_ranges: dict = field(default_factory=dict)
    morph_kernel: int = 5


@dataclass
class DetectorCfg:
    enabled: bool = True
    model: str = "yolo26n.pt"
    conf: float = 0.35
    imgsz: int = 640
    person_class: int = 0
    every_n: int = 3
    box_ttl_sec: float = 0.6


@dataclass
class FusionCfg:
    require_person: bool = False
    match_dist: float = 120.0
    lock_threshold: float = 0.60
    unlock_threshold: float = 0.35
    ema_alpha: float = 0.5
    lost_grace_sec: float = 0.8
    person_aim_x: float = 0.5
    person_aim_y: float = 0.5
    # Scale match_dist by subject bbox height so a far (small) person has a
    # tighter association radius than a near (large) one (flag-off; review 2026-06-12).
    # 240 px ≈ near subject height at 720p — empirical, to be field-tuned.
    match_dist_scale: bool = False
    gps_boost: float = 0.2
    gps_boost_radius_frac: float = 0.25
    # GPS-cued detector ROI (flag-off). When true + arbiter owns gps_tracker:
    # YOLO runs on a cropped ROI centered at the GPS-pointed frame center.
    # Flag OFF preserves byte-identical behavior. (review 2026-06-12)
    gps_roi_enabled: bool = False


@dataclass
class WebCfg:
    host: str = "0.0.0.0"
    port: int = 8088
    jpeg_quality: int = 70
    show_hud: bool = True


@dataclass
class LoopCfg:
    target_fps: float = 35.0
    log_every_sec: float = 5.0


@dataclass
class GpsCfg:
    enabled: bool = False
    source: str = "meshtastic"  # "meshtastic" | "direct_lora"
    dev_path: str = "/dev/ttyACM0"
    remote_id: str = ""  # "" => auto-detect the non-local mesh node
    direct_dev_path: str = "/dev/ttyACM0"
    direct_baud: int = 115200
    direct_reconnect_sec: float = 3.0
    # P1: GPS-mode PTZ speeds (conservative — GPS has latency + bearing uncertainty)
    max_pan_speed: int = 4      # 1..24, vision uses up to 10
    max_tilt_speed: int = 3     # 1..20, vision uses up to 12
    stale_threshold_sec: float = 10.0  # remote fix age > this → stale (display/status)
    # drive_stale_sec is tighter: a 44s-old fix on an 8m/s foiler points ~350m behind
    # (review 2026-06-12); this gate keeps steering honest without affecting the HUD display
    drive_stale_sec: float = 8.0    # remote fix age > this → too old to STEER
    # P2: GPS-driven zoom (off by default — untuned; enable when ready)
    drive_zoom: bool = False
    # P1: handoff hysteresis
    lock_frames: int = 5        # K consecutive vision-locked frames → hand to vision
    grace_sec: float = 1.0      # unlock grace before falling back to GPS
    # Phase-1 (v3): base-drift / staleness revalidation. The monitor withholds GPS
    # authority on CONFIRMED tripod drift; unknown/suspect keep the lock so noisy or
    # stale base GPS never false-denies pointing. base_drift_enabled is hot; the
    # thresholds are read at startup (restart to change).
    base_drift_enabled: bool = True
    base_drift_threshold_m: float = 4.0
    base_drift_min_trend_m: float = 2.0
    base_drift_window: int = 10
    base_drift_min_consecutive: int = 5
    base_drift_interval_sec: float = 2.0
    base_drift_max_fix_age_sec: float = 10.0
    base_drift_min_sats: int = 0   # 0 = sats gate off (base sats not yet ingested)


@dataclass
class TrackingCfg:
    mode: str = "auto"  # "auto" | "gps_only" | "vision_only"


@dataclass
class EstimatorCfg:
    # Plan-3 target estimator (shadow mode). enabled=False keeps the estimator
    # out of the loop entirely until a rig opts in; the G2 FOV-curve gate
    # still applies after that.
    enabled: bool = False
    shadow: bool = True
    q_accel: float = 2.0
    p0_pos: float = 25.0
    p0_vel: float = 9.0
    r_gps_fresh: float = 4.0
    r_gps_age_scale: float = 0.5
    r_vis_deg: float = 1.0
    zoom_cov_wide_deg: float = 4.0
    zoom_cov_narrow_deg: float = 1.5
    log_every_n: int = 3
    # Phase-2 vision range observation (T2.1). Default OFF; enable via
    # config.local.yaml overlay after shadow validation. Enabling is gated on
    # the zoom curve being multi-point (the 1-point curve is exact at wide but
    # a multi-point curve is required before trusting zoom-variant range math).
    use_vision_range: bool = False
    subject_height_m: float = 1.0  # standing surfer torso-on-board height
    r_range_frac: float = 0.3      # range std = r_range_frac * range_m (30 %)


@dataclass
class SensorsCfg:
    # Phase-3 T3.2: phone-on-tripod ingest.  enabled=False means the POST route
    # still accepts (200) but SensorHub records nothing — cheap field kill-switch.
    enabled: bool = False
    # Degrees of deviation from the session heading baseline that, sustained for
    # >10s, fires an anchor_suspect event (heading-drift monitor).
    drift_alert_deg: float = 12.0


@dataclass
class Config:
    camera: CameraCfg
    ptz: PtzCfg
    camera_ai: CameraAiCfg
    color: ColorCfg
    detector: DetectorCfg
    fusion: FusionCfg
    web: WebCfg
    loop: LoopCfg
    gps: GpsCfg = field(default_factory=GpsCfg)
    tracking: TrackingCfg = field(default_factory=TrackingCfg)
    estimator: EstimatorCfg = field(default_factory=EstimatorCfg)
    sensors: SensorsCfg = field(default_factory=SensorsCfg)
    source_path: str = ""   # set by load_config; the rig yaml; empty in unit tests


def _apply_overlay(cfg: "Config", overlay_path: str) -> None:
    """Deep-merge config.local.yaml sections over an already-built Config in-place.

    Uses the same key coercion approach as the main load (dataclass field updates via
    dict merge).  Unknown section names are ignored with a printed warning; unknown keys
    within a known section are also silently ignored (matching main load behaviour for
    extra yaml keys).
    """
    try:
        with open(overlay_path, encoding="utf-8") as f:
            ov = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return  # no overlay — normal case on a fresh deploy

    _KNOWN_SECTIONS = {
        "camera": "camera",
        "ptz": "ptz",
        "camera_ai": "camera_ai",
        "color": "color",
        "detector": "detector",
        "fusion": "fusion",
        "web": "web",
        "loop": "loop",
        "gps": "gps",
        "tracking": "tracking",
        "estimator": "estimator",
        "sensors": "sensors",
    }
    for section, kv in ov.items():
        if section not in _KNOWN_SECTIONS:
            print(f"[config] overlay: unknown section '{section}' — ignored")
            continue
        if not isinstance(kv, dict):
            print(f"[config] overlay: section '{section}' is not a dict — ignored")
            continue
        target = getattr(cfg, _KNOWN_SECTIONS[section], None)
        if target is None:
            continue
        for k, v in kv.items():
            if hasattr(target, k):
                setattr(target, k, v)
            # unknown keys within a known section are silently ignored


def load_config(path: str) -> Config:
    with open(path, "r") as f:
        raw = yaml.safe_load(f) or {}

    cam = _d(raw, "camera", {})
    # allow webcam index given as int-like string
    src = cam.get("source", 0)
    if isinstance(src, str) and src.isdigit():
        src = int(src)

    cfg = Config(
        camera=CameraCfg(
            source=src,
            use_gstreamer=bool(_d(cam, "use_gstreamer", False)),
            codec=str(_d(cam, "codec", "h264")),
            reconnect_sec=float(_d(cam, "reconnect_sec", 2.0)),
        ),
        ptz=PtzCfg(**{**PtzCfg().__dict__, **_d(raw, "ptz", {})}),
        camera_ai=CameraAiCfg(**{**CameraAiCfg().__dict__, **_d(raw, "camera_ai", {})}),
        color=ColorCfg(**{**ColorCfg().__dict__, **_d(raw, "color", {})}),
        detector=DetectorCfg(**{**DetectorCfg().__dict__, **_d(raw, "detector", {})}),
        fusion=FusionCfg(**{**FusionCfg().__dict__, **_d(raw, "fusion", {})}),
        web=WebCfg(**{**WebCfg().__dict__, **_d(raw, "web", {})}),
        loop=LoopCfg(**{**LoopCfg().__dict__, **_d(raw, "loop", {})}),
        gps=GpsCfg(**{**GpsCfg().__dict__, **_d(raw, "gps", {})}),
        tracking=TrackingCfg(**{**TrackingCfg().__dict__, **_d(raw, "tracking", {})}),
        estimator=EstimatorCfg(**{**EstimatorCfg().__dict__, **_d(raw, "estimator", {})}),
        sensors=SensorsCfg(**{**SensorsCfg().__dict__, **_d(raw, "sensors", {})}),
    )

    # Apply overlay (config.local.yaml) over the base config — rig-owned, deploy-safe.
    _apply_overlay(cfg, _overlay_path(path))

    # Inverted hysteresis (unlock >= lock) makes any color blob acquire a full
    # lock instantly — the 2026-06-11 field failure. The hot-config path rejects
    # it; the YAML path must too, but a refusal here would brick the service at
    # the beach, so reset to the designed defaults and say so loudly.
    # This guard runs AFTER the overlay merge so an inverted pair arriving via
    # overlay is also caught and reset.
    if cfg.fusion.unlock_threshold >= cfg.fusion.lock_threshold:
        d = FusionCfg()
        print(f"[config] INVALID fusion hysteresis in {path}: unlock "
              f"{cfg.fusion.unlock_threshold:g} >= lock {cfg.fusion.lock_threshold:g} "
              f"— resetting to defaults lock={d.lock_threshold:g}/unlock={d.unlock_threshold:g}")
        cfg.fusion.lock_threshold = d.lock_threshold
        cfg.fusion.unlock_threshold = d.unlock_threshold
    if cfg.tracking.mode not in ("auto", "gps_only", "vision_only"):
        print(f"[config] INVALID tracking.mode in {path}: {cfg.tracking.mode!r} "
              "— resetting to 'auto'")
        cfg.tracking.mode = "auto"
    cfg.source_path = path
    return cfg
