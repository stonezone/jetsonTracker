"""Camera calibration manager for the dashboard wizard.

Builds + persists a gps_fusion.CameraPose: camera position (manual entry, or
averaged iPhone base GPS once that path is fresh) + pan heading/scale from two
landmark aims (aim at a known-GPS point, read VISCA pan -> calibrate_pan_two_point).
VISCA reports raw encoder counts, so TWO reference aims give both the scale and
the anchor with no spec sheet.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gps_fusion.camera_pose import CameraPose, lock_base_position  # noqa: E402
from gps_fusion.geo_calc import GeoPoint, calculate_bearing, haversine_distance  # noqa: E402

POSE_PATH = os.environ.get("POSE_PATH", "/data/projects/gimbal/config/camera_pose.json")


class CalibrationManager:
    def __init__(self, gps, cam_getter):
        self.gps = gps                # GPSClient
        self.cam_getter = cam_getter  # callable -> ViscaBackend or None
        self.pose = CameraPose()
        if os.path.exists(POSE_PATH):
            try:
                self.pose = CameraPose.load(POSE_PATH)
            except Exception:
                pass
        self.heading_points = []      # list of (pan_enc, bearing_deg)

    def state(self):
        return {
            "base": {
                "lat": self.pose.lat, "lon": self.pose.lon, "alt": self.pose.alt_m,
                "set": (self.pose.lat != 0 or self.pose.lon != 0),
            },
            "pan_calibrated": self.pose.calibrated,
            "pan_enc_per_deg": round(self.pose.pan_enc_per_deg, 3),
            "heading_points": len(self.heading_points),
            "pose_path": POSE_PATH,
            "saved": os.path.exists(POSE_PATH),
            "drift": self.drift_status(),
        }

    def drift_status(self, warn_m=3.0):
        """Compare the live base GPS fix against the locked/calibrated base
        position. The whole pointing solution is anchored to the base, so if the
        tripod is bumped the camera silently mis-aims; this surfaces it."""
        if self.pose.lat == 0 and self.pose.lon == 0:
            return {"locked": False}
        g = self.gps.get_state().gimbal
        if g is None:
            return {"locked": True, "live": False}
        locked = GeoPoint(lat=self.pose.lat, lon=self.pose.lon, alt=self.pose.alt_m)
        live = GeoPoint(lat=g.lat, lon=g.lon, alt=g.alt or 0.0)
        drift = haversine_distance(locked, live)
        return {"locked": True, "live": True, "drift_m": round(drift, 1),
                "warn": drift > warn_m, "threshold_m": warn_m}

    def set_base_manual(self, lat, lon, alt=0.0):
        self.pose.lat, self.pose.lon, self.pose.alt_m = float(lat), float(lon), float(alt)
        return {"ok": True, "base": [self.pose.lat, self.pose.lon, self.pose.alt_m]}

    def base_lock(self, seconds=8.0, max_acc=5.0):
        """Average iPhone/base GPS fixes from the live GPSClient (stationary tripod)."""
        samples, t0 = [], time.time()
        while time.time() - t0 < seconds:
            g = self.gps.get_state().gimbal
            if g is not None:
                samples.append((g.lat, g.lon, g.alt or 0.0, g.accuracy or 99.0))
            time.sleep(0.3)
        if not samples:
            return {"ok": False, "error": "no base/iPhone fixes (base may be stale-dropped upstream)"}
        res = lock_base_position(samples, max_h_acc_m=max_acc)
        if res is None:
            return {"ok": False, "error": "no acceptable base fixes"}
        self.pose.lat, self.pose.lon, self.pose.alt_m = res
        return {"ok": True, "base": list(res), "samples": len(samples)}

    def heading_point(self, lat, lon):
        """Record a heading reference: aim camera at a known-GPS point, read pan."""
        cam = self.cam_getter()
        if cam is None:
            return {"ok": False, "error": "no camera"}
        p = cam.get_position()
        if p is None:
            return {"ok": False, "error": "no PTZ readback"}
        if self.pose.lat == 0 and self.pose.lon == 0:
            return {"ok": False, "error": "set base position first"}
        base = GeoPoint(lat=self.pose.lat, lon=self.pose.lon, alt=self.pose.alt_m)
        bearing = calculate_bearing(base, GeoPoint(lat=float(lat), lon=float(lon), alt=0.0))
        self.heading_points.append((p.pan, bearing))
        return {"ok": True, "pan_enc": p.pan, "bearing": round(bearing, 1),
                "points": len(self.heading_points)}

    def heading_commit(self):
        if len(self.heading_points) < 2:
            return {"ok": False, "error": "need 2 heading points (aim at 2 known-GPS landmarks)"}
        (e1, b1), (e2, b2) = self.heading_points[-2], self.heading_points[-1]
        try:
            self.pose.calibrate_pan_two_point(e1, b1, e2, b2)
        except Exception as ex:
            return {"ok": False, "error": str(ex)}
        return {"ok": True, "pan_enc_per_deg": round(self.pose.pan_enc_per_deg, 3)}

    def reset_heading(self):
        self.heading_points = []
        return {"ok": True}

    def save(self):
        os.makedirs(os.path.dirname(POSE_PATH), exist_ok=True)
        self.pose.save(POSE_PATH)
        return {"ok": True, "path": POSE_PATH}
