"""Calibration state management for the WaveCam control API.

Moved from control_api.py.  CalibrationManager owns the CalibrationStore
and exposes calibration_ok / calibration_state / validate_capture /
capture_calibration.  It receives the adapter as its api parameter so
it can call refusal(), claim_manual(), revision, and status_snapshot().
"""
from __future__ import annotations

import math
import threading
import time
from typing import Any

from fastapi.responses import JSONResponse

from .camera_pose import PRISUAL_PAN_ENC_PER_DEG
from .control_utils import copy_optional_dict, make_request_id
from .gps_geo import bearing_deg, haversine_m, normalize_180
from .ptz_owner import AUTONOMOUS, CALIBRATE, IDLE


LEVEL_MAX_DEG = 0.5
LOCATION_MIN_RADIUS_M = 2.5
LOCATION_DEFAULT_RADIUS_M = 15.0
LIVE_BASE_MAX_AGE_SEC = 3.0   # GPS-2: reject the live-base fallback if the cached fix is older than this
LOCATION_DEFAULT_UERE_M = 5.0
LOCATION_WARMUP_SEC = 60.0
HEADING_DEFAULT_BUDGET_DEG = 2.0
VALIDATION_DEFAULT_BUDGET_DEG = 2.0


def _now_ms() -> int:
    return int(time.time() * 1000)


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _confidence(uncertainty_deg: float | None, budget_deg: float) -> float:
    if uncertainty_deg is None or budget_deg <= 0:
        return 0.0
    return round(max(0.0, min(1.0, 1.0 - (uncertainty_deg / (budget_deg * 1.5)))), 3)


def _quadrature(values: list[float]) -> float:
    return math.sqrt(sum(max(0.0, v) ** 2 for v in values))


def _offset_lat_lon(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    lat_rad = math.radians(lat)
    dlat = north_m / 111_320.0
    denom = max(1e-6, 111_320.0 * math.cos(lat_rad))
    dlon = east_m / denom
    return lat + dlat, lon + dlon


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


class CalibrationManager:
    """Owns the CalibrationStore and exposes calibration routes."""

    def __init__(self, store, pipeline, lock: threading.RLock, api) -> None:
        self._store = store
        self.pipeline = pipeline
        self._lock = lock
        self._api = api
        self._session = self._new_session()

    def _new_session(self) -> dict:
        return {
            "active": False,
            "started_at_unix_ms": None,
            "ended_at_unix_ms": None,
            "valid": False,
            "confirmed": False,
            "previous_owner": None,
            "level": None,
            "location": None,
            "heading_lock": None,
            "validation": None,
            "last_refusal": None,
            "banner": "INVALID",
        }

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
            session = dict(self._session)
            started = session.get("started_at_unix_ms")
            ended = session.get("ended_at_unix_ms")
            age_sec = None
            if started is not None:
                age_ms = (_now_ms() if session.get("active") else (ended or _now_ms())) - started
                age_sec = round(max(0.0, age_ms / 1000.0), 3)
            session["age_sec"] = age_sec
            session["owner_active"] = self.pipeline.owner.owner == CALIBRATE
            session["session_scoped"] = True
            state = {
                "reference_heading": self._store.reference_heading,
                "heading": copy_optional_dict(steps.get("heading")),
                "tilt": copy_optional_dict(steps.get("tilt")),
                "zoom": copy_optional_dict(steps.get("zoom")),
                "updated_at_unix_ms": self._store.updated_at_unix_ms,
                # P1: GPS calibration status
                "gps_calibrated": self.pipeline.pose.calibrated,
                # M10 (audit 2026-07-01): "has a base position" and "the base-drift
                # lock permits GPS authority" are DIFFERENT facts. base_locked used
                # to mean only the former, so a confirmed tripod-drift unlock still
                # showed "locked" while GPS pointing was silently withheld. has_base
                # is the old (renamed) meaning; base_locked now reports the honest
                # BaseDriftMonitor verdict pipeline.py actually gates on.
                "has_base": (
                    self.pipeline.pose.lat != 0.0 or self.pipeline.pose.lon != 0.0
                ),
                "base_locked": bool(getattr(self.pipeline.pose, "base_locked", True)),
                # P3: FOV curve for estimator vision bearing
                "fov_entries": [list(e) for e in self._store.fov_curve],
                "mode": "calibrate" if session.get("active") else "idle",
                "active": bool(session.get("active")),
                "valid": bool(session.get("valid")),
                "confirmed": bool(session.get("confirmed")),
                "age_sec": age_sec,
                "banner": self._calibration_banner(session),
                "session": session,
            }
            if state["gps_calibrated"]:
                state["gps_pose"] = {
                    "lat": self.pipeline.pose.lat,
                    "lon": self.pipeline.pose.lon,
                    "alt_m": self.pipeline.pose.alt_m,
                    "pan_enc_per_deg": self.pipeline.pose.pan_enc_per_deg,
                }
            return state

    def _calibration_banner(self, session: dict | None = None) -> str:
        session = session or self._session
        if session.get("active"):
            return "CALIBRATE ACTIVE"
        if session.get("valid"):
            return "VALID"
        return "INVALID"

    def _calibration_refusal(
        self,
        code: str,
        message: str,
        status_code: int = 409,
        **extra: Any,
    ) -> JSONResponse:
        with self._lock:
            self._session["last_refusal"] = {
                "code": code,
                "message": message,
                "at_unix_ms": _now_ms(),
                **extra,
            }
        return JSONResponse(
            {
                "ok": False,
                "code": code,
                "message": message,
                **extra,
                "calibration": self.calibration_state(),
                "status": self._api.status_snapshot(),
            },
            status_code=status_code,
        )

    def _require_active(self) -> JSONResponse | None:
        if not self._session.get("active"):
            return self._calibration_refusal(
                "calibrate_inactive",
                "Start CALIBRATE mode before this calibration step.",
            )
        if self.pipeline.owner.killed:
            return self._calibration_refusal(
                "killed",
                "KILL is latched; resume before calibration.",
            )
        if self.pipeline.owner.owner != CALIBRATE:
            return self._calibration_refusal(
                "calibrate_owner_lost",
                "CALIBRATE no longer owns PTZ; restart calibration mode.",
            )
        return None

    def start_session(self, req) -> JSONResponse:
        if self.pipeline.owner.killed:
            return self._calibration_refusal(
                "killed",
                "KILL is latched; resume before entering CALIBRATE.",
            )
        if _field(req, "requested_owner", "manual") != "manual":
            return self._calibration_refusal(
                "invalid_request",
                "Only requested_owner=manual is accepted for CALIBRATE.",
                422,
            )
        with self._lock:
            current_owner = self.pipeline.owner.owner
            if current_owner == CALIBRATE and self._session.get("active"):
                return self.calibration_ok()
            if current_owner != IDLE:
                takeover = bool(_field(req, "takeover", False))
                if not takeover:
                    return self._calibration_refusal(
                        "owner_busy",
                        "Another PTZ owner holds the camera; retry with takeover=true.",
                    )
                self._api.cancel_manual_deadman()
                self._api.cancel_zoom_deadman()
                self._api.reset_restore_owner()
                self.pipeline.ptz.stop()
                self.pipeline.ptz.zoom("stop")
                if not self.pipeline.owner.release(current_owner):
                    return self._calibration_refusal(
                        "owner_busy",
                        "Could not release current PTZ owner for CALIBRATE.",
                    )
            if not self.pipeline.owner.request(CALIBRATE):
                return self._calibration_refusal(
                    "owner_busy",
                    "Could not claim PTZ for CALIBRATE.",
                )
            self._session = self._new_session()
            self._session.update(
                {
                    "active": True,
                    "started_at_unix_ms": _now_ms(),
                    "previous_owner": current_owner if current_owner in AUTONOMOUS else None,
                    "banner": "CALIBRATE ACTIVE",
                }
            )
            self.pipeline.state.set_status(state="CALIBRATE", cmd="stop")
        return self.calibration_ok()

    def exit_session(self, req) -> JSONResponse:
        confirm = bool(_field(req, "confirm", False))
        restore_prior = bool(_field(req, "restore_prior", True))
        if confirm and not self._session.get("valid"):
            return self._calibration_refusal(
                "validation_required",
                "Validation must pass and be confirmed before confirm=true exit.",
            )
        with self._lock:
            previous_owner = self._session.get("previous_owner")
            if self.pipeline.owner.owner == CALIBRATE:
                self.pipeline.ptz.stop()
                self.pipeline.ptz.zoom("stop")
                self.pipeline.owner.release(CALIBRATE)
            self._session["active"] = False
            self._session["ended_at_unix_ms"] = _now_ms()
            self._session["banner"] = self._calibration_banner(self._session)
            self.pipeline.state.set_status(state="SEARCHING", cmd="stop")
            if restore_prior and previous_owner in AUTONOMOUS and not self.pipeline.owner.killed:
                self.pipeline.owner.request(previous_owner)
        return self.calibration_ok()

    def cancel_session(self, reason: str = "cancelled") -> None:
        with self._lock:
            if self.pipeline.owner.owner == CALIBRATE:
                self.pipeline.owner.release(CALIBRATE)
            if self._session.get("active"):
                self._session["active"] = False
                self._session["ended_at_unix_ms"] = _now_ms()
                self._session["valid"] = False
                self._session["confirmed"] = False
                self._session["last_refusal"] = {
                    "code": reason,
                    "message": "CALIBRATE session cancelled.",
                    "at_unix_ms": _now_ms(),
                }

    def lock_location(self, req) -> JSONResponse:
        refusal = self._require_active()
        if refusal is not None:
            return refusal
        method = str(_field(req, "method", "base_wio_average") or "base_wio_average")
        manual_lat = _optional_float(_field(req, "lat"))
        manual_lon = _optional_float(_field(req, "lon"))
        manual_alt = _optional_float(_field(req, "alt_m")) or 0.0
        if manual_lat is not None and manual_lon is not None:
            error_radius = max(
                LOCATION_MIN_RADIUS_M,
                _optional_float(_field(req, "manual_error_radius_m")) or LOCATION_DEFAULT_RADIUS_M,
            )
            lat, lon = self._apply_location_offset(req, manual_lat, manual_lon)
            entry = {
                "method": method,
                "lat": lat,
                "lon": lon,
                "alt_m": manual_alt + (_optional_float(_field(req, "offset_up_m")) or 0.0),
                "error_radius_m": round(error_radius, 3),
                "sample_count": 0,
                "model": "manual_radius",
                "source": _field(req, "source", None),
                "captured_at_unix_ms": _now_ms(),
            }
            return self._commit_location(entry)

        samples, rejected = self._accepted_location_samples(req)
        if not samples and bool(_field(req, "use_live_base", True)):
            gps = getattr(self.pipeline, "gps", None)
            cam = gps.get_camera_position() if gps is not None else None
            if cam is not None:
                # Freshness gate (GPS-2): get_camera_position() returns the last
                # cached base fix unconditionally. A rebooted base Wio leaves a
                # STALE _cam cached (the documented "base reboot staleifies serial"
                # gotcha) — locking it would silently corrupt every bearing. Require
                # a live reader and a recent fix before accepting the live base.
                cam_age = gps.get_camera_age() if hasattr(gps, "get_camera_age") else None
                reader_ok = gps.reader_alive() if hasattr(gps, "reader_alive") else True
                if not reader_ok or cam_age is None or cam_age > LIVE_BASE_MAX_AGE_SEC:
                    return self._calibration_refusal(
                        "gps_stale",
                        "Live base position is stale or unavailable; reacquire the base "
                        "fix (or send explicit samples) before locking location.",
                        503,
                        base_age_sec=None if cam_age is None else round(cam_age, 1),
                    )
                samples = [
                    {
                        "lat": float(cam[0]),
                        "lon": float(cam[1]),
                        "alt_m": float(cam[2]),
                        "radius_m": LOCATION_DEFAULT_RADIUS_M,
                    }
                ]
        if not samples:
            return self._calibration_refusal(
                "location_unavailable",
                "No accepted base-position samples for CALIBRATE location lock.",
                503,
                rejected_count=rejected,
            )

        samples = self._reject_location_outliers(samples)
        lat = sum(s["lat"] for s in samples) / len(samples)
        lon = sum(s["lon"] for s in samples) / len(samples)
        # M11 (audit 2026-07-01): average altitude only over samples that
        # actually reported one; a camera 6m above the water must not be
        # dragged toward 0 by samples that simply omitted alt_m. Fall back to
        # 0.0 only when NONE of the samples reported an altitude.
        alt_values = [s["alt_m"] for s in samples if s.get("alt_m") is not None]
        alt = sum(alt_values) / len(alt_values) if alt_values else 0.0
        lat, lon = self._apply_location_offset(req, lat, lon)
        radius = max(LOCATION_MIN_RADIUS_M, max(s["radius_m"] for s in samples))
        entry = {
            "method": method,
            "lat": lat,
            "lon": lon,
            "alt_m": alt + (_optional_float(_field(req, "offset_up_m")) or 0.0),
            "error_radius_m": round(radius, 3),
            "sample_count": len(samples),
            "rejected_count": rejected,
            "model": "max(hdop*UERE,h_acc,min_radius), not sample_stddev",
            "source": _field(req, "source", None),
            "captured_at_unix_ms": _now_ms(),
        }
        return self._commit_location(entry)

    def _apply_location_offset(self, req, lat: float, lon: float) -> tuple[float, float]:
        north = _optional_float(_field(req, "offset_north_m")) or 0.0
        east = _optional_float(_field(req, "offset_east_m")) or 0.0
        if north == 0.0 and east == 0.0:
            return lat, lon
        return _offset_lat_lon(lat, lon, north, east)

    def _accepted_location_samples(self, req) -> tuple[list[dict], int]:
        accepted: list[dict] = []
        rejected = 0
        max_fix_age = _optional_float(_field(req, "max_fix_age_sec")) or 5.0
        max_hdop = _optional_float(_field(req, "max_hdop")) or 3.0
        max_h_acc = _optional_float(_field(req, "max_h_acc_m")) or 20.0
        min_sats = _optional_int(_field(req, "min_sats")) or 4
        uere = _optional_float(_field(req, "uere_m")) or LOCATION_DEFAULT_UERE_M
        skip_warmup = bool(_field(req, "skip_warmup", True))
        for sample in (_field(req, "samples", []) or []):
            lat = _optional_float(_field(sample, "lat"))
            lon = _optional_float(_field(sample, "lon"))
            if lat is None or lon is None:
                rejected += 1
                continue
            uptime = _optional_float(_field(sample, "uptime_sec"))
            if skip_warmup and uptime is not None and uptime < LOCATION_WARMUP_SEC:
                rejected += 1
                continue
            fix_age = _optional_float(_field(sample, "fix_age_sec"))
            if fix_age is not None and fix_age > max_fix_age:
                rejected += 1
                continue
            sats = _optional_int(_field(sample, "sats"))
            if sats is not None and sats < min_sats:
                rejected += 1
                continue
            hdop = _optional_float(_field(sample, "hdop"))
            if hdop is not None and hdop > max_hdop:
                rejected += 1
                continue
            h_acc = _optional_float(_field(sample, "h_acc_m"))
            if h_acc is not None and h_acc > max_h_acc:
                rejected += 1
                continue
            radius_candidates = [LOCATION_MIN_RADIUS_M]
            if hdop is not None:
                radius_candidates.append(hdop * uere)
            if h_acc is not None:
                radius_candidates.append(h_acc)
            accepted.append(
                {
                    "lat": lat,
                    "lon": lon,
                    # M11 (audit 2026-07-01): keep None distinct from a real 0.0 —
                    # `or 0.0` here silently coerced "no altitude reported" into
                    # "sea level", which then got averaged into the locked base
                    # altitude as if it were a measurement (~2 deg tilt error at
                    # 150m). lock_location() now averages only non-None samples.
                    "alt_m": _optional_float(_field(sample, "alt_m")),
                    "radius_m": max(radius_candidates),
                }
            )
        return accepted, rejected

    def _reject_location_outliers(self, samples: list[dict]) -> list[dict]:
        if len(samples) < 3:
            return samples
        lat0 = _median([s["lat"] for s in samples])
        lon0 = _median([s["lon"] for s in samples])
        radius = max(s["radius_m"] for s in samples)
        gate_m = max(20.0, radius * 3.0)
        kept = [
            s for s in samples
            if haversine_m(lat0, lon0, s["lat"], s["lon"]) <= gate_m
        ]
        return kept or samples

    def _persist_step(self, step: str, entry: dict) -> bool:
        """Write a wizard step to the CalibrationStore and flush to disk.

        CAL-1: the session wizard mutated pose/_session in memory only, so a
        confirmed calibration was lost on the next restart (re-introducing the
        "gps_calibrated true but reference_heading null" class CalibrationStore was
        built to prevent). Mirror capture_calibration: set_step + save under the
        adapter lock. Returns False on a save failure so the route can surface 503.
        Caller MUST hold self._lock.
        """
        self._store.set_step(step, entry)
        try:
            self._store.save()
            return True
        except Exception as e:
            print(f"[control_api] calibration wizard save failed ({step}): {e}")
            return False

    def _commit_location(self, entry: dict) -> JSONResponse:
        with self._lock:
            self.pipeline.pose.lat = float(entry["lat"])
            self.pipeline.pose.lon = float(entry["lon"])
            self.pipeline.pose.alt_m = float(entry["alt_m"])
            self._session["location"] = entry
            self._session["valid"] = False
            self._session["confirmed"] = False
            persisted = self._persist_step("location", entry)
        if not persisted:
            return self._calibration_refusal(
                "calibration_persist_failed",
                "Location locked in memory but failed to write to disk.",
                503,
            )
        return self.calibration_ok()

    def check_level(self, req) -> JSONResponse:
        refusal = self._require_active()
        if refusal is not None:
            return refusal
        roll = _optional_float(_field(req, "roll_deg"))
        pitch = _optional_float(_field(req, "pitch_deg"))
        if roll is None or pitch is None:
            return self._calibration_refusal(
                "level_missing",
                "roll_deg and pitch_deg are required for the pan-axis level gate.",
                422,
            )
        max_tilt = _optional_float(_field(req, "max_tilt_deg")) or LEVEL_MAX_DEG
        tilt_mag = max(abs(roll), abs(pitch))
        entry = {
            "roll_deg": roll,
            "pitch_deg": pitch,
            "max_tilt_deg": max_tilt,
            "tilt_mag_deg": tilt_mag,
            "passed": tilt_mag <= max_tilt,
            "captured_at_unix_ms": _now_ms(),
        }
        with self._lock:
            self._session["level"] = entry
            self._session["valid"] = False
            self._session["confirmed"] = False
            persisted = self._persist_step("level", entry)
        if not entry["passed"]:
            return self._calibration_refusal(
                "pan_axis_not_level",
                "Pan axis is outside the level gate; level the tripod before heading capture.",
                uncertainty_deg=round(tilt_mag, 3),
                max_tilt_deg=max_tilt,
            )
        if not persisted:
            return self._calibration_refusal(
                "calibration_persist_failed",
                "Level captured in memory but failed to write to disk.",
                503,
            )
        return self.calibration_ok()

    def heading_lock(self, req) -> JSONResponse:
        refusal = self._require_active()
        if refusal is not None:
            return refusal
        if not bool(_field(req, "operator_accepted", False)):
            return self._calibration_refusal(
                "operator_accept_required",
                "Heading capture requires explicit operator acceptance of the preview.",
            )
        # Level gate removed 2026-06-17: on this rig the only attitude sensor is the
        # phone, which mounts OFF the camera (magnetic isolation) and on its side, so its
        # roll/pitch can never represent the pan-axis tilt. The operator levels the tripod
        # by hand; tilt_error falls back to 0 in _estimate_heading_uncertainty when no level
        # entry exists. A level check remains available but is no longer required.
        location = self._session.get("location")
        if not location:
            return self._calibration_refusal(
                "location_required",
                "Lock camera location before heading capture.",
            )
        bearing, distance_m = self._resolve_bearing(req, location)
        if bearing is None:
            return self._calibration_refusal(
                "bearing_required",
                "Provide bearing_deg or target_lat/target_lon for heading capture.",
                422,
            )
        pan_enc = _optional_float(_field(req, "pan_enc"))
        if pan_enc is None:
            enc = self._current_encoder()
            pan_enc = float(enc[0]) if enc is not None else None
        if pan_enc is None:
            return self._calibration_refusal(
                "encoder_unavailable",
                "No fresh pan encoder is available for heading capture.",
                503,
            )
        budget = _optional_float(_field(req, "max_uncertainty_deg")) or HEADING_DEFAULT_BUDGET_DEG
        uncertainty = self._estimate_heading_uncertainty(req, location, distance_m)
        confidence = _confidence(uncertainty, budget)
        if uncertainty > budget:
            return self._calibration_refusal(
                "uncertainty_too_high",
                "Estimated heading uncertainty exceeds the configured budget.",
                uncertainty_deg=round(uncertainty, 3),
                max_uncertainty_deg=budget,
                confidence=confidence,
            )
        with self._lock:
            self.pipeline.pose.calibrate_pan_aim(
                enc=pan_enc,
                bearing_deg=bearing,
                enc_per_deg=PRISUAL_PAN_ENC_PER_DEG,
            )
            entry = {
                "bearing_deg": round(bearing % 360.0, 6),
                # set_step("heading", ...) maps heading_deg -> reference_heading; the
                # camera "heading" IS the bearing it's aimed at, so persist both so a
                # restart restores reference_heading (CAL-1).
                "heading_deg": round(bearing % 360.0, 6),
                "pan_enc": pan_enc,
                "pan_enc_per_deg": PRISUAL_PAN_ENC_PER_DEG,
                "distance_m": None if distance_m is None else round(distance_m, 3),
                "uncertainty_deg": round(uncertainty, 3),
                "max_uncertainty_deg": budget,
                "confidence": confidence,
                "method": str(_field(req, "method", _field(req, "source", "unknown"))),
                "source": _field(req, "source", None),
                "captured_at_unix_ms": _now_ms(),
            }
            self._session["heading_lock"] = entry
            self._session["validation"] = None
            self._session["valid"] = False
            self._session["confirmed"] = False
            persisted = self._persist_step("heading", entry)
        if not persisted:
            return self._calibration_refusal(
                "calibration_persist_failed",
                "Heading captured in memory but failed to write to disk.",
                503,
            )
        return self.calibration_ok()

    def validate_heading(self, req) -> JSONResponse:
        refusal = self._require_active()
        if refusal is not None:
            return refusal
        if not self.pipeline.pose.calibrated or not self._session.get("heading_lock"):
            return self._calibration_refusal(
                "heading_required",
                "Capture heading before validation.",
            )
        location = self._session.get("location")
        bearing, distance_m = self._resolve_bearing(req, location)
        if bearing is None:
            return self._calibration_refusal(
                "bearing_required",
                "Provide bearing_deg or target_lat/target_lon for validation.",
                422,
            )
        pan_enc = _optional_float(_field(req, "pan_enc"))
        if pan_enc is None:
            enc = self._current_encoder()
            pan_enc = float(enc[0]) if enc is not None else None
        if pan_enc is None:
            return self._calibration_refusal(
                "encoder_unavailable",
                "No fresh pan encoder is available for validation.",
                503,
            )
        predicted = self.pipeline.pose.pan_encoder_to_bearing(pan_enc)
        miss = abs(normalize_180((predicted or 0.0) - bearing))
        budget = _optional_float(_field(req, "max_miss_deg")) or VALIDATION_DEFAULT_BUDGET_DEG
        accepted = miss <= budget
        entry = {
            "bearing_deg": round(bearing % 360.0, 6),
            "predicted_bearing_deg": None if predicted is None else round(predicted % 360.0, 6),
            "pan_enc": pan_enc,
            "miss_deg": round(miss, 3),
            "max_miss_deg": budget,
            "distance_m": None if distance_m is None else round(distance_m, 3),
            "accepted": accepted,
            "source": _field(req, "source", None),
            "captured_at_unix_ms": _now_ms(),
        }
        with self._lock:
            self._session["validation"] = entry
            self._session["valid"] = False
            self._session["confirmed"] = False
            persisted = self._persist_step("validation", entry)
        if not accepted:
            return self._calibration_refusal(
                "validation_miss_too_large",
                "Independent validation miss exceeds the configured budget.",
                miss_deg=entry["miss_deg"],
                max_miss_deg=budget,
            )
        if not persisted:
            return self._calibration_refusal(
                "calibration_persist_failed",
                "Validation captured in memory but failed to write to disk.",
                503,
            )
        return self.calibration_ok()

    def confirm_validation(self, req) -> JSONResponse:
        refusal = self._require_active()
        if refusal is not None:
            return refusal
        validation = self._session.get("validation")
        if not validation or not validation.get("accepted"):
            return self._calibration_refusal(
                "validation_required",
                "A passing validation capture is required before confirmation.",
            )
        if not bool(_field(req, "accepted", True)):
            return self._calibration_refusal(
                "operator_rejected_validation",
                "Operator rejected the validation capture.",
            )
        with self._lock:
            validation["confirmed_at_unix_ms"] = _now_ms()
            self._session["validation"] = validation
            self._session["valid"] = True
            self._session["confirmed"] = True
            self._session["banner"] = "VALID"
            # Persist the confirmed validation so the VALID state (and the validation
            # record) survives a restart — the final, most important commit (CAL-1).
            persisted = self._persist_step("validation", validation)
        if not persisted:
            return self._calibration_refusal(
                "calibration_persist_failed",
                "Validation confirmed in memory but failed to write to disk.",
                503,
            )
        return self.calibration_ok()

    def _resolve_bearing(self, req, location: dict | None) -> tuple[float | None, float | None]:
        direct = _optional_float(_field(req, "bearing_deg"))
        target_lat = _optional_float(_field(req, "target_lat"))
        target_lon = _optional_float(_field(req, "target_lon"))
        if target_lat is not None and target_lon is not None and location is not None:
            lat = float(location["lat"])
            lon = float(location["lon"])
            return (
                bearing_deg(lat, lon, target_lat, target_lon),
                haversine_m(lat, lon, target_lat, target_lon),
            )
        if direct is not None:
            return direct % 360.0, _optional_float(_field(req, "distance_m"))
        return None, None

    def _estimate_heading_uncertainty(self, req, location: dict, distance_m: float | None) -> float:
        method = str(_field(req, "method", _field(req, "source", "")) or "").lower()
        # Phone-compass source: the bearing comes from the phone's magnetometer, so the
        # dominant uncertainty is its reported heading accuracy (CLHeading.headingAccuracy),
        # NOT GPS position geometry. Use it directly. A phone heading is a COARSE acquisition
        # cue (±15-25° in practice), not a 2° lock — the caller must pass a lenient
        # max_uncertainty_deg to accept it, and the default budget will reject it.
        phone_acc = _optional_float(_field(req, "heading_acc_deg"))
        # M12 (audit 2026-07-01): select the model by `method` ONLY. Branching on
        # "or phone_acc is not None" let any request that incidentally carried
        # heading_acc_deg skip the distance-geometry error model entirely — a
        # 10m GPS capture (~27 deg hazard) with a plausible compass acc could
        # lock a badly wrong heading despite method != phone.
        if "phone" in method:
            acc = phone_acc if (phone_acc is not None and phone_acc >= 0) else HEADING_DEFAULT_BUDGET_DEG
            vision_error = _optional_float(_field(req, "vision_error_deg")) or 0.5
            latency_error = _optional_float(_field(req, "latency_error_deg")) or 0.2
            return _quadrature([acc, vision_error, latency_error])
        base_error = _optional_float(_field(req, "base_error_radius_m"))
        if base_error is None:
            base_error = float(location.get("error_radius_m", LOCATION_DEFAULT_RADIUS_M))
        remote_error = _optional_float(_field(req, "remote_error_radius_m"))
        if remote_error is None:
            remote_error = 0.0 if "landmark" in method else 3.0
        lever_error = _optional_float(_field(req, "lever_arm_error_m"))
        if lever_error is None:
            lever_error = 0.5
        position_error_deg = 0.0
        if distance_m is not None and distance_m > 0.1:
            position_error_deg = math.degrees(math.atan((base_error + remote_error + lever_error) / distance_m))
        else:
            position_error_deg = _optional_float(_field(req, "position_error_deg")) or HEADING_DEFAULT_BUDGET_DEG
        vision_error = _optional_float(_field(req, "vision_error_deg")) or 0.5
        latency_error = _optional_float(_field(req, "latency_error_deg")) or 0.2
        level = self._session.get("level") or {}
        tilt_error = _optional_float(_field(req, "tilt_error_deg"))
        if tilt_error is None:
            tilt_error = float(level.get("tilt_mag_deg", 0.0))
        return _quadrature([position_error_deg, vision_error, latency_error, tilt_error])

    def _current_encoder(self) -> tuple[float, float] | None:
        ptz_state = getattr(self.pipeline, "ptz_state", None)
        if ptz_state is not None:
            cached, age = ptz_state.latest()
            if cached is not None and age is not None and age < 1.0:
                return float(cached[0]), float(cached[1])
        if self.pipeline.ptz is None:
            return None
        enc = self.pipeline.ptz.inquire_pan_tilt()
        if enc is None:
            return None
        return float(enc[0]), float(enc[1])

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
                # Don't report success when the entry didn't persist — it would be lost on
                # restart while the operator saw ok:true (CAL-1, the M2 pattern on the FOV
                # path). Mirror calibration_persisted_response: 503.
                print(f"[control_calibration] fov_curve save failed: {e}")
                return JSONResponse(
                    {"ok": False, "error": f"FOV entry not persisted: {e}"}, 503)
        return JSONResponse({"ok": True, "fov_entries": [list(e) for e in curve]})

    def validate_calibration_capture(self, req) -> JSONResponse | None:
        if self.pipeline.owner.killed:
            return self._api.refusal("killed", "KILL is latched; resume before calibration capture.")
        if req.requested_owner != "manual":
            return self._api.refusal("invalid_request", "Only requested_owner=manual is accepted in v1.", 422)
        current = self.pipeline.owner.owner
        # Allow takeover from calibrate owner (standalone captures must work during
        # an active calibration session). Save it so release_manual_owner can restore
        # the session when the capture is done.
        if current == "calibrate" and req.takeover:
            # Lock-guarded takeover (stops PTZ first, stages calibrate restore under
            # the dispatcher lock) — no external poke of _restore_owner_after_manual.
            if not self._api.claim_manual_from_calibrate():
                return self._api.refusal("owner_busy", "Cannot claim manual for capture.")
            return None
        if not self._api.claim_manual(takeover=req.takeover):
            return self._api.refusal("owner_busy", "Another PTZ owner holds the camera.")
        return None

    def capture_calibration(self, step: str, values: dict) -> bool:
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
                    from .camera_pose import PRISUAL_TILT_ENC_PER_DEG
                    self.pipeline.pose.tilt_anchor_enc = float(enc[1])
                    self.pipeline.pose.tilt_anchor_elev = float(tilt_deg)
                    self.pipeline.pose.tilt_enc_per_deg = PRISUAL_TILT_ENC_PER_DEG
            # Always persist after set_step so reference_heading survives restart even
            # when enc=None (VISCA timeout or DummyPtz in tests) prevented pose update.
            # Test isolation is handled by the WAVECAM_POSE_PATH env var (conftest.py).
            self._store.set_step(step, values)
            try:
                self._store.save()
                return True
            except Exception as e:
                print(f"[control_api] calibration save failed: {e}")
                return False
