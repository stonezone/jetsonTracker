"""TargetEstimator — constant-velocity Kalman filter in a local East-North frame.

Fuses GPS position observations and vision bearing observations into a single
world-frame state: [e, n, ve, vn] (metres from base, m/s). Outputs per-tick
predicted bearing/distance/uncertainty for shadow logging and (after the flip)
direct pointing.

SHADOW MODE (estimator.shadow = true): this module NEVER commands the camera.
The pipeline reads the output and logs it; all VISCA commands continue from the
existing arbiter/servo path.

Implementation notes:
  - Uses plain Python lists for 4×4 matrix ops (no numpy required in tests).
    If numpy is available (it is on the Orin via ultralytics), the ops fall
    through to it for performance. The _Matrix shim below handles both.
  - The flat-earth approximation (treating the local EN frame as Cartesian) is
    valid within ±300 m with < 0.1 m error — acceptable for surf filming.
  - Measurement noise R for GPS scales linearly with fix age so a stale fix
    contributes little information without being fully ignored until the cutoff.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional, Tuple


# ── matrix shim (works without numpy; numpy used automatically if present) ───

try:
    import numpy as _np

    def _mat(rows):
        return _np.array(rows, dtype=float)

    def _matmul(a, b):
        return _np.dot(a, b)

    def _matadd(a, b):
        return _np.add(a, b)

    def _matsub(a, b):
        return _np.subtract(a, b)

    def _mattranspose(a):
        return _np.transpose(a)

    def _matinv(a):
        return _np.linalg.inv(a)

    def _scalar_mul(s, m):
        return s * _np.array(m, dtype=float)

    def _mat_to_list(m):
        return m.tolist()

except ImportError:
    # Pure-Python fallback — correct, not fast.
    def _mat(rows):
        return [list(r) for r in rows]

    def _matmul(a, b):
        n, m, p = len(a), len(b), len(b[0])
        return [[sum(a[i][k] * b[k][j] for k in range(m)) for j in range(p)] for i in range(n)]

    def _matadd(a, b):
        return [[a[i][j] + b[i][j] for j in range(len(a[0]))] for i in range(len(a))]

    def _matsub(a, b):
        return [[a[i][j] - b[i][j] for j in range(len(a[0]))] for i in range(len(a))]

    def _mattranspose(a):
        return [[a[j][i] for j in range(len(a))] for i in range(len(a[0]))]

    def _matinv(a):
        # 2×2 only (used for GPS update; vision update is 1×1 handled inline)
        [[a00, a01], [a10, a11]] = a
        det = a00 * a11 - a01 * a10
        if abs(det) < 1e-12:
            return [[1e9, 0], [0, 1e9]]
        d = 1.0 / det
        return [[d * a11, -d * a01], [-d * a10, d * a00]]

    def _scalar_mul(s, m):
        return [[s * m[i][j] for j in range(len(m[0]))] for i in range(len(m))]

    def _mat_to_list(m):
        return m


# ── geo helpers ──────────────────────────────────────────────────────────────

def _enu_from_gps(base_lat: float, base_lon: float,
                  fix_lat: float, fix_lon: float) -> Tuple[float, float]:
    """Flat-earth east/north metres from base to fix."""
    from .gps_geo import haversine_m, bearing_deg
    dist = haversine_m(base_lat, base_lon, fix_lat, fix_lon)
    brg = math.radians(bearing_deg(base_lat, base_lon, fix_lat, fix_lon))
    return dist * math.sin(brg), dist * math.cos(brg)


def _bearing_from_enu(e: float, n: float) -> float:
    """True bearing (degrees) from origin to (e, n)."""
    return (math.degrees(math.atan2(e, n)) + 360.0) % 360.0

if TYPE_CHECKING:
    from .protocols import GpsFixLike


def range_from_bbox_height(fov_curve: list, zoom_enc: int, bbox_h_px: float,
                           frame_h: float, subject_height_m: float):
    """Known-size-subject range from apparent bbox height. Pure; shared by the
    live observation path and the sim/replay harness so the logged observation
    can never diverge from what was fused. Returns metres, or None when the
    geometry degenerates (zero bbox)."""
    import math
    hfov_rad = math.radians(_fov_at_zoom(fov_curve, zoom_enc))
    vfov_rad = 2.0 * math.atan(math.tan(hfov_rad / 2.0) * 9.0 / 16.0)
    half_angle = vfov_rad * (bbox_h_px / frame_h) / 2.0
    if half_angle <= 0:
        return None
    return subject_height_m / (2.0 * math.tan(half_angle))


def _fov_at_zoom(fov_curve: list, zoom_enc: int) -> float:
    """Linear interpolation of FOV (degrees) from the calibration curve."""
    if not fov_curve:
        return 60.0   # unreachable: __init__ guards this
    if zoom_enc <= fov_curve[0][0]:
        return fov_curve[0][1]
    for i in range(1, len(fov_curve)):
        z0, f0 = fov_curve[i - 1]
        z1, f1 = fov_curve[i]
        if zoom_enc <= z1:
            t = (zoom_enc - z0) / max(1, z1 - z0)
            return f0 + t * (f1 - f0)
    return fov_curve[-1][1]


# ── output dataclass ─────────────────────────────────────────────────────────

@dataclass
class EstimatorOutput:
    e: float
    n: float
    ve: float
    vn: float
    cov: list               # 4×4 covariance (list of lists)
    bearing_deg: float
    dist_m: float
    pan_enc_would: int
    tilt_enc_would: int
    bearing_std_deg: float
    owner_actual: str = ""
    cmd_actual: str = ""
    gps_updated: bool = False
    vision_updated: bool = False


# ── estimator ────────────────────────────────────────────────────────────────

class TargetEstimator:
    """Constant-velocity Kalman filter in a local East-North frame.

    Call update_gps() and/or update_vision() each pipeline tick (whichever
    measurements are available), then predict_output() to get the would-command
    output for shadow logging.

    Thread-safety: not thread-safe internally — intended to be called from the
    pipeline thread only.
    """

    def __init__(self, cfg, gps_cfg, pose, fov_curve: list):
        """
        Args:
            cfg: estimator config namespace (keys from the config table above).
            gps_cfg: the gps config namespace (for stale_threshold_sec).
            pose: CameraPose instance (must be calibrated before vision updates).
            fov_curve: list of (zoom_enc, fov_deg) tuples — MUST be non-empty
                       when shadow=True (the G2 gate).
        """
        if not cfg.enabled:
            self._enabled = False
            return
        self._enabled = True

        if cfg.shadow and not fov_curve:
            raise RuntimeError(
                "FOV curve is empty — shadow mode requires the zoom calibration "
                "(G2 gate). Run the zoom/FOV calibration session first and populate "
                "CalibrationStore.fov_curve before enabling the estimator."
            )

        self._cfg = cfg
        self._gps_stale_sec = float(getattr(gps_cfg, "stale_threshold_sec", 10.0))
        self._pose = pose
        self._fov_curve = fov_curve

        self._initialised = False
        self._t_last: Optional[float] = None

        # State [e, n, ve, vn] and covariance P (4×4)
        self._x: List[float] = [0.0, 0.0, 0.0, 0.0]
        p0p = float(cfg.p0_pos)
        p0v = float(cfg.p0_vel)
        self._P = _mat([
            [p0p, 0, 0, 0],
            [0, p0p, 0, 0],
            [0, 0, p0v, 0],
            [0, 0, 0, p0v],
        ])

    @property
    def initialised(self) -> bool:
        return getattr(self, "_initialised", False)

    # ── predict step ─────────────────────────────────────────────────────────

    def _predict(self, now: float) -> None:
        """Advance the state forward to time `now`. Called before each update."""
        if self._t_last is None:
            self._t_last = now
            return
        dt = max(0.0, now - self._t_last)
        self._t_last = now
        if dt <= 0.0:
            return

        # State transition
        F = _mat([
            [1, 0, dt, 0],
            [0, 1, 0, dt],
            [0, 0, 1,  0],
            [0, 0, 0,  1],
        ])
        self._x = [
            self._x[0] + dt * self._x[2],
            self._x[1] + dt * self._x[3],
            self._x[2],
            self._x[3],
        ]

        # Process noise Q (Singer/NCA model for constant-velocity with accel noise)
        q = float(self._cfg.q_accel)
        dt2 = dt * dt
        dt3 = dt2 * dt
        Q = _mat([
            [q*q*dt3/3, 0, q*q*dt2/2, 0],
            [0, q*q*dt3/3, 0, q*q*dt2/2],
            [q*q*dt2/2, 0, q*q*dt, 0],
            [0, q*q*dt2/2, 0, q*q*dt],
        ])
        FP = _matmul(F, self._P)
        Ft = _mattranspose(F)
        self._P = _matadd(_matmul(FP, Ft), Q)

    # ── GPS update ───────────────────────────────────────────────────────────

    def update_gps(self, fix: "GpsFixLike", now: float) -> None:
        """Fuse a GPS position observation. `fix` must have .lat, .lon, .age_sec."""
        if not self._enabled:
            return
        if fix.age_sec > self._gps_stale_sec:
            # Stale fix — still predict forward so the clock advances
            if self._initialised:
                self._predict(now)
            return

        base_lat = self._pose.lat
        base_lon = self._pose.lon
        e_obs, n_obs = _enu_from_gps(base_lat, base_lon, fix.lat, fix.lon)

        if not self._initialised:
            # Cold start: initialise from the first GPS fix
            self._x = [e_obs, n_obs, 0.0, 0.0]
            self._t_last = now
            self._initialised = True
            return

        self._predict(now)

        # Observation model: H_gps = [[1,0,0,0],[0,1,0,0]]
        r = float(self._cfg.r_gps_fresh) + float(self._cfg.r_gps_age_scale) * fix.age_sec
        H = _mat([[1, 0, 0, 0], [0, 1, 0, 0]])
        Ht = _mattranspose(H)
        # S = H P Ht + R
        HP = _matmul(H, self._P)
        HPHt = _matmul(HP, Ht)
        R = _mat([[r, 0], [0, r]])
        S = _matadd(HPHt, R)
        # K = P Ht S^-1
        PHt = _matmul(self._P, Ht)
        K = _matmul(PHt, _matinv(S))
        # innovation
        inn = [e_obs - self._x[0], n_obs - self._x[1]]
        # state update: x = x + K inn
        Kinn = _matmul(K, _mat([[inn[0]], [inn[1]]]))
        for i in range(4):
            self._x[i] += _mat_to_list(Kinn)[i][0]
        # covariance update: P = (I - K H) P
        I4 = _mat([[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]])
        KH = _matmul(K, H)
        IKH = _matsub(I4, KH)
        self._P = _matmul(IKH, self._P)

    # ── vision update ────────────────────────────────────────────────────────

    def update_vision(self, pan_enc: int, pixel_cx: float, frame_w: float,
                      zoom_enc: int, now: float) -> None:
        """Fuse a vision bearing observation.

        Args:
            pan_enc: current pan encoder from PtzState.latest().
            pixel_cx: blob centre x in pixels.
            frame_w: frame width in pixels.
            zoom_enc: current zoom encoder (for FOV interpolation).
            now: current time.
        """
        if not self._enabled or not self._initialised:
            return

        self._predict(now)

        # Bearing from encoder + pixel offset
        bearing_enc = self._pose.pan_encoder_to_bearing(pan_enc)
        if bearing_enc is None:
            return   # pose not heading-calibrated yet — no bearing frame exists
        fov = _fov_at_zoom(self._fov_curve, zoom_enc)
        pixel_offset_deg = (pixel_cx - frame_w / 2.0) / frame_w * fov
        obs_bearing = (bearing_enc + pixel_offset_deg + 360.0) % 360.0

        e, n = self._x[0], self._x[1]
        r2 = e * e + n * n
        if r2 < 1.0:
            return   # too close to base — linearisation is unreliable

        # H_vis: linearised Jacobian of bearing w.r.t. (e, n)
        # bearing = atan2(e, n); d(bearing)/de = n/r², d(bearing)/dn = -e/r²
        # (in degrees: multiply by 180/π)
        scale = math.degrees(1.0) / r2
        h = [n * scale, -e * scale, 0.0, 0.0]   # 1×4 row

        # predicted bearing from state
        pred_bearing = _bearing_from_enu(e, n)
        innovation = (obs_bearing - pred_bearing + 180.0) % 360.0 - 180.0   # wrap

        self._scalar_update(h, innovation, float(self._cfg.r_vis_deg) ** 2)

    # ── vision range update ──────────────────────────────────────────────────

    def update_vision_range(self, bbox_h_px: float, frame_h: float,
                            zoom_enc: int, now: float) -> None:
        """Fuse a range observation derived from the person bbox height.

        Model: the subject is a known-height object; the angular subtense of
        its height in the frame gives distance.

            vfov = 2 * atan(tan(hfov/2) * 9/16)           (16:9 aspect)
            angle_sub = vfov * (bbox_h / frame_h)          (subtended fraction)
            range_m = subject_height_m / (2 * tan(angle_sub / 2))

        H linearised: range r = sqrt(e² + n²); H = [e/r, n/r, 0, 0].
        R = (r_range_frac * range_m)²

        Args:
            bbox_h_px: person bbox height in pixels.
            frame_h: frame height in pixels.
            zoom_enc: current zoom encoder (for FOV interpolation).
            now: current timestamp (seconds).
        """
        if not self._enabled or not self._initialised:
            return

        if frame_h <= 0 or bbox_h_px <= 0:
            return

        hfov_deg = _fov_at_zoom(self._fov_curve, zoom_enc)
        hfov_rad = math.radians(hfov_deg)
        # vfov from hfov via 16:9 aspect ratio
        vfov_rad = 2.0 * math.atan(math.tan(hfov_rad / 2.0) * 9.0 / 16.0)

        bbox_frac = bbox_h_px / frame_h
        # angular subtense of the subject in the vertical
        angle_sub_rad = vfov_rad * bbox_frac

        half_angle = angle_sub_rad / 2.0
        if half_angle <= 0 or math.tan(half_angle) == 0:
            return

        subject_h = float(getattr(self._cfg, "subject_height_m", 1.0))
        range_m = subject_h / (2.0 * math.tan(half_angle))

        if range_m < 1.0:
            return  # geometry degenerate — too close or bbox fills frame

        self._predict(now)

        e, n = self._x[0], self._x[1]
        r = math.hypot(e, n)
        if r < 1.0:
            return  # state not yet far from origin — linearisation unreliable

        # H = [e/r, n/r, 0, 0]  (Jacobian of r = sqrt(e²+n²) w.r.t. state)
        h = [e / r, n / r, 0.0, 0.0]

        # Predicted range from state
        pred_range = r
        innovation = range_m - pred_range

        r_frac = float(self._cfg.r_range_frac)
        self._scalar_update(h, innovation, (r_frac * range_m) ** 2)

    def _scalar_update(self, h: list, innovation: float, r_var: float) -> None:
        """Fuse one scalar observation with Jacobian row ``h``.

        Covariance form note: the update keeps only the diagonal term of
        (K h) P — approximate but stable for the small innovations this
        filter sees. If the form ever changes, it changes HERE for every
        observation type (bearing, range) at once.
        """
        Pht = [sum(self._P[i][j] * h[j] for j in range(4)) for i in range(4)]
        S = sum(h[j] * Pht[j] for j in range(4)) + r_var
        if abs(S) < 1e-9:
            return
        K = [Pht[i] / S for i in range(4)]
        for i in range(4):
            self._x[i] += K[i] * innovation
        KhP = [[K[i] * h[j] for j in range(4)] for i in range(4)]
        for i in range(4):
            for j in range(4):
                self._P[i][j] -= KhP[i][j] * self._P[j][j]  # approximate but stable

    # ── output ───────────────────────────────────────────────────────────────

    def predict_output(self, now: float) -> Optional[EstimatorOutput]:
        """Return the current estimate as a shadow-log-ready output, or None
        if the estimator is not yet initialised."""
        if not self._enabled or not self._initialised:
            return None

        e, n = self._x[0], self._x[1]
        dist_m = math.hypot(e, n)
        bearing = _bearing_from_enu(e, n)

        # pan/tilt encoders the estimator WOULD command
        from .gps_geo import elevation_deg, GeoPoint
        elev = elevation_deg(
            GeoPoint(lat=self._pose.lat, lon=self._pose.lon, alt_m=self._pose.alt_m),
            GeoPoint(lat=self._pose.lat, lon=self._pose.lon, alt_m=0.0),
            dist_m,
        )
        pan_enc_would = int(self._pose.bearing_to_pan_encoder(bearing))
        tilt_enc_would = int(self._pose.elevation_to_tilt_encoder(elev))

        # Bearing uncertainty from covariance
        # Var(bearing) ≈ (dn/r²)² * P_ee + (de/r²)² * P_nn  (linearised)
        r2 = max(e*e + n*n, 1.0)
        P_ee = _mat_to_list(self._P)[0][0]
        P_nn = _mat_to_list(self._P)[1][1]
        var_brg_rad = (n / r2) ** 2 * P_ee + (e / r2) ** 2 * P_nn
        bearing_std_deg = math.degrees(math.sqrt(max(0.0, var_brg_rad)))

        cov_list = _mat_to_list(self._P)

        return EstimatorOutput(
            e=e, n=n, ve=self._x[2], vn=self._x[3],
            cov=cov_list,
            bearing_deg=bearing,
            dist_m=dist_m,
            pan_enc_would=pan_enc_would,
            tilt_enc_would=tilt_enc_would,
            bearing_std_deg=bearing_std_deg,
        )
