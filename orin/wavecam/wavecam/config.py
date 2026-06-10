"""Config loading and typed access. Single responsibility: parse YAML -> dataclasses."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
import yaml


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
    dev_path: str = "/dev/ttyACM0"
    remote_id: str = ""  # "" => auto-detect the non-local mesh node
    # P1: GPS-mode PTZ speeds (conservative — GPS has latency + bearing uncertainty)
    max_pan_speed: int = 4      # 1..24, vision uses up to 10
    max_tilt_speed: int = 3     # 1..20, vision uses up to 12
    stale_threshold_sec: float = 10.0  # remote fix age > this → stale
    # P2: GPS-driven zoom (off by default — untuned; enable when ready)
    drive_zoom: bool = False
    # P1: handoff hysteresis
    lock_frames: int = 5        # K consecutive vision-locked frames → hand to vision
    grace_sec: float = 1.0      # unlock grace before falling back to GPS


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


def load_config(path: str) -> Config:
    with open(path, "r") as f:
        raw = yaml.safe_load(f) or {}

    cam = _d(raw, "camera", {})
    # allow webcam index given as int-like string
    src = cam.get("source", 0)
    if isinstance(src, str) and src.isdigit():
        src = int(src)

    return Config(
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
    )
