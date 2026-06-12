"""Calibration state management for the WaveCam control API.

Moved from control_api.py.  CalibrationManager owns the CalibrationStore
and exposes calibration_ok / calibration_state / validate_capture /
capture_calibration.  It receives the adapter as its api parameter so
it can call refusal(), claim_manual(), revision, and status_snapshot().
"""
from __future__ import annotations

import threading
from typing import Any

from fastapi.responses import JSONResponse

from .control_utils import copy_optional_dict, make_request_id



class CalibrationManager:
    """Owns the CalibrationStore and exposes calibration routes."""

    def __init__(self, store, pipeline, lock: threading.RLock, api) -> None:
        self._store = store
        self.pipeline = pipeline
        self._lock = lock
        self._api = api

    def calibration_ok(self) -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "request_id": make_request_id(),
                "revision": self._api.revision,
                "calibration": self.calibration_state(),
                "status": self._api.status_snapshot(),
            }
        )

    def calibration_state(self) -> dict:
        with self._lock:
            steps = self._store.steps
            state = {
                "reference_heading": self._store.reference_heading,
                "heading": copy_optional_dict(steps.get("heading")),
                "tilt": copy_optional_dict(steps.get("tilt")),
                "zoom": copy_optional_dict(steps.get("zoom")),
                "updated_at_unix_ms": self._store.updated_at_unix_ms,
                # P1: GPS calibration status
                "gps_calibrated": self.pipeline.pose.calibrated,
                "base_locked": (
                    self.pipeline.pose.lat != 0.0 or self.pipeline.pose.lon != 0.0
                ),
                # P3: FOV curve for estimator vision bearing
                "fov_entries": [list(e) for e in self._store.fov_curve],
            }
            if state["gps_calibrated"]:
                state["gps_pose"] = {
                    "lat": self.pipeline.pose.lat,
                    "lon": self.pipeline.pose.lon,
                    "alt_m": self.pipeline.pose.alt_m,
                    "pan_enc_per_deg": self.pipeline.pose.pan_enc_per_deg,
                }
            return state

    def get_fov_curve(self) -> dict:
        """Return the current FOV curve as a JSON-serialisable dict."""
        with self._lock:
            return {"fov_entries": [list(e) for e in self._store.fov_curve]}

    def post_fov_entry(self, zoom_enc: Any, fov_deg: Any) -> JSONResponse:
        """Upsert a zoom-level FOV measurement. Returns ok or 422 on bad input."""
        try:
            z = int(zoom_enc)
            f = float(fov_deg)
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": "zoom_enc and fov_deg must be numbers"}, 422)
        if f <= 0:
            return JSONResponse({"ok": False, "error": "fov_deg must be > 0"}, 422)
        with self._lock:
            curve = [(ze, fe) for ze, fe in self._store.fov_curve if ze != z]
            curve.append((z, f))
            curve.sort(key=lambda x: x[0])
            self._store.fov_curve = curve
            try:
                self._store.save()
            except Exception as e:
                print(f"[control_calibration] fov_curve save failed: {e}")
        return JSONResponse({"ok": True, "fov_entries": [list(e) for e in curve]})

    def validate_calibration_capture(self, req) -> JSONResponse | None:
        if self.pipeline.owner.killed:
            return self._api.refusal("killed", "KILL is latched; resume before calibration capture.")
        if req.requested_owner != "manual":
            return self._api.refusal("invalid_request", "Only requested_owner=manual is accepted in v1.", 422)
        if not self._api.claim_manual(takeover=req.takeover):
            return self._api.refusal("owner_busy", "Another PTZ owner holds the camera.")
        return None

    def capture_calibration(self, step: str, values: dict) -> None:
        # Encoder source: the PtzState poller cache (fresh to ~0.1s at 10Hz and
        # plausibility-gated), NOT a direct inquiry — a request-thread inquiry
        # races the poller on the shared UDP socket (reply theft), and the
        # camera is stationary during a capture anyway. Falls back to a direct
        # inquiry only when the poller has no data (ptz disabled / just booted).
        # Cache read is non-blocking, so the old hold-the-lock-across-recvfrom
        # hazard (2026-06-08 class) only applies on the fallback path, which we
        # still run before acquiring the adapter lock.
        enc = None
        if step in ("heading", "tilt") and self.pipeline.ptz is not None:
            ptz_state = getattr(self.pipeline, "ptz_state", None)
            if ptz_state is not None:
                cached, age = ptz_state.latest()
                if cached is not None and age is not None and age < 1.0:
                    enc = cached
            if enc is None:
                enc = self.pipeline.ptz.inquire_pan_tilt()

        cam_pos = None
        if step == "base_lock" and self.pipeline.gps is not None:
            cam_pos = self.pipeline.gps.get_camera_position()

        with self._lock:
            if step == "heading":
                # P1: wire to CameraPose — read pan encoder, calibrate pan aim
                heading_deg = values.get("heading_deg")
                if heading_deg is not None and enc is not None:
                    from .camera_pose import PRISUAL_PAN_ENC_PER_DEG
                    self.pipeline.pose.calibrate_pan_aim(
                        enc=float(enc[0]),
                        bearing_deg=float(heading_deg),
                        enc_per_deg=PRISUAL_PAN_ENC_PER_DEG,
                    )
            elif step == "base_lock":
                # P1: lock base GPS position for camera reference
                from .camera_pose import lock_base_position
                if cam_pos is not None:
                    # Single-fix lock (averaging done by GPS chip)
                    fixes = [(cam_pos[0], cam_pos[1], cam_pos[2], None)]
                    base = lock_base_position(fixes)
                    if base is not None:
                        self.pipeline.pose.lat = base[0]
                        self.pipeline.pose.lon = base[1]
                        self.pipeline.pose.alt_m = base[2]
            elif step == "tilt":
                # P1: tilt calibration — single-point anchor (two-point deferred)
                tilt_deg = values.get("tilt_deg")
                if tilt_deg is not None and enc is not None:
                    self.pipeline.pose.tilt_anchor_enc = float(enc[1])
                    self.pipeline.pose.tilt_anchor_elev = float(tilt_deg)
            # Always persist after set_step so reference_heading survives restart even
            # when enc=None (VISCA timeout or DummyPtz in tests) prevented pose update.
            # Test isolation is handled by the WAVECAM_POSE_PATH env var (conftest.py).
            self._store.set_step(step, values)
            try:
                self._store.save()
            except Exception as e:
                print(f"[control_api] calibration save failed: {e}")
