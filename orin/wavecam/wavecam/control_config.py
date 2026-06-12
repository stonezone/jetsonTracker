"""Configuration management for the WaveCam control API.

Moved from control_api.py.  ConfigManager owns hot-config application,
GPS config helpers, and preset-value reading.  It delegates response
construction (ok/refusal) to the api object it receives.

Note: config_snapshot and current_preset_values remain on ControlApiAdapter
because they also need pending_restart_config (owned by the adapter's lock)
and calibration_state (owned by CalibrationManager).  The config _mutation_
methods (apply_hot_config, validate_hot_config_request, apply_hot_key, the GPS
helpers) live here, along with stage_restart_config.
"""
from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from .color_presets import COLOR_PRESETS, preset_hsv_ranges
from .control_utils import set_bool, set_float, set_int


class ConfigManager:
    """Owns hot-config validation and application, GPS config helpers."""

    def __init__(self, pipeline, api) -> None:
        self.pipeline = pipeline
        self._api = api

    # ------------------------------------------------------------------
    # Hot-config entry points
    # ------------------------------------------------------------------

    def apply_hot_config(self, patch: dict[str, Any]) -> JSONResponse | None:
        for key, value in patch.items():
            refusal = self.apply_hot_key(key, value, dry_run=True)
            if refusal is not None:
                return refusal
        refusal = self._check_fusion_hysteresis(patch)
        if refusal is not None:
            return refusal
        for key, value in patch.items():
            refusal = self.apply_hot_key(key, value, dry_run=False)
            if refusal is not None:
                return refusal
        return None

    def _check_fusion_hysteresis(self, patch: dict[str, Any]) -> JSONResponse | None:
        """Refuse unlock >= lock: with inverted hysteresis the unlock branch is
        unreachable, so any sustained color blob locks permanently (the
        2026-06-11 field failure)."""
        if "fusion.lock_threshold" not in patch and "fusion.unlock_threshold" not in patch:
            return None
        fusion = self.pipeline.cfg.fusion
        lock = float(patch.get("fusion.lock_threshold", fusion.lock_threshold))
        unlock = float(patch.get("fusion.unlock_threshold", fusion.unlock_threshold))
        if unlock >= lock:
            return self._api.refusal(
                "invalid_request",
                f"fusion.unlock_threshold ({unlock:g}) must be below "
                f"fusion.lock_threshold ({lock:g}).",
                422,
            )
        return None

    def validate_hot_config_request(self, req) -> JSONResponse | None:
        if req.persist:
            return self._api.refusal(
                "invalid_request",
                "persist=true is not supported by hot config in v1.",
                422,
            )
        if req.revision is not None and req.revision != self._api.revision:
            return self._api.refusal(
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
            "estimator.shadow": lambda: self.apply_estimator_bool("shadow", value, dry_run=dry_run),
            "estimator.enabled": lambda: self.apply_estimator_bool("enabled", value, dry_run=dry_run),
            "estimator.q_accel": lambda: self.apply_estimator_float("q_accel", value, 0.1, 20.0, dry_run=dry_run),
            "estimator.p0_pos": lambda: self.apply_estimator_float("p0_pos", value, 0.01, 1000.0, dry_run=dry_run),
            "estimator.p0_vel": lambda: self.apply_estimator_float("p0_vel", value, 0.01, 100.0, dry_run=dry_run),
            "estimator.r_gps_fresh": lambda: self.apply_estimator_float("r_gps_fresh", value, 0.01, 1000.0, dry_run=dry_run),
            "estimator.r_gps_age_scale": lambda: self.apply_estimator_float("r_gps_age_scale", value, 0.0, 100.0, dry_run=dry_run),
            "estimator.r_vis_deg": lambda: self.apply_estimator_float("r_vis_deg", value, 0.1, 45.0, dry_run=dry_run),
            "estimator.zoom_cov_wide_deg": lambda: self.apply_estimator_float("zoom_cov_wide_deg", value, 0.1, 90.0, dry_run=dry_run),
            "estimator.zoom_cov_narrow_deg": lambda: self.apply_estimator_float("zoom_cov_narrow_deg", value, 0.1, 45.0, dry_run=dry_run),
            "estimator.log_every_n": lambda: self.apply_estimator_int("log_every_n", value, 1, 100, dry_run=dry_run),
        }
        setter = setters.get(key)
        if setter is None:
            return self._api.refusal("invalid_request", f"{key} is not a hot-config key.", 422)
        error = setter()
        if error is not None:
            return self._api.refusal("invalid_request", error, 422)
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

    # ------------------------------------------------------------------
    # GPS config helpers
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Estimator config helpers
    # ------------------------------------------------------------------

    def _est_cfg(self):
        """Return cfg.estimator, or None if estimator section is absent."""
        return getattr(self.pipeline.cfg, "estimator", None)

    def apply_estimator_float(self, attr: str, value: Any, lo: float, hi: float,
                              dry_run: bool = False) -> str | None:
        est_cfg = self._est_cfg()
        if est_cfg is None:
            return f"estimator.{attr}: estimator section not present in config."
        return set_float(est_cfg, attr, value, lo, hi, dry_run=dry_run)

    def apply_estimator_int(self, attr: str, value: Any, lo: int, hi: int,
                            dry_run: bool = False) -> str | None:
        est_cfg = self._est_cfg()
        if est_cfg is None:
            return f"estimator.{attr}: estimator section not present in config."
        return set_int(est_cfg, attr, value, lo, hi, dry_run=dry_run)

    def apply_estimator_bool(self, attr: str, value: Any,
                             dry_run: bool = False) -> str | None:
        est_cfg = self._est_cfg()
        if est_cfg is None:
            return f"estimator.{attr}: estimator section not present in config."
        return set_bool(est_cfg, attr, value, dry_run=dry_run)
