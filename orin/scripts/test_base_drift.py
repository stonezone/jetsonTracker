#!/usr/bin/env python3
"""Offline test for CalibrationManager.drift_status — synthetic base + fake GPS.

Watch/iPhone are quiet overnight, so this drives the drift logic with a fake
GPSClient returning known base fixes. Run anywhere:
    python3 orin/scripts/test_base_drift.py
"""

import os
import sys

os.environ["POSE_PATH"] = "/tmp/_nonexistent_pose_test.json"  # don't load real pose
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from calibration import CalibrationManager  # noqa: E402


class FakeFix:
    def __init__(self, lat, lon, alt=0.0, acc=2.0):
        self.lat, self.lon, self.alt, self.accuracy = lat, lon, alt, acc


class FakeState:
    def __init__(self, gimbal):
        self.gimbal = gimbal


class FakeGPS:
    def __init__(self):
        self._g = None

    def set(self, g):
        self._g = g

    def get_state(self):
        return FakeState(self._g)


_n = 0


def check(c, m):
    global _n
    _n += 1
    if not c:
        print("FAIL:", m)
        sys.exit(1)
    print("  ok:", m)


gps = FakeGPS()
cm = CalibrationManager(gps, lambda: None)

check(cm.drift_status()["locked"] is False, "no base set -> locked False")

cm.set_base_manual(21.28000, -157.83000, 0.0)
check(cm.state()["base"]["set"] is True, "base set manually")

d = cm.drift_status()
check(d["locked"] and d["live"] is False, "base set, no live fix -> live False")

# Live base == locked base -> ~0 m, no warn.
gps.set(FakeFix(21.28000, -157.83000))
d = cm.drift_status()
check(d["live"] and d["drift_m"] < 0.5 and d["warn"] is False,
      "live==base -> ~0m, no warn (got %.2fm)" % d["drift_m"])

# ~10 m away (0.00009 deg lat) -> warn.
gps.set(FakeFix(21.28000 + 0.00009, -157.83000))
d = cm.drift_status()
check(8 < d["drift_m"] < 12 and d["warn"] is True,
      "live ~10m away -> warn (got %.1fm)" % d["drift_m"])

# ~2 m away (0.000018 deg lat) -> under 3 m threshold, no warn.
gps.set(FakeFix(21.28000 + 0.000018, -157.83000))
d = cm.drift_status()
check(d["warn"] is False, "live ~2m away -> no warn (got %.1fm)" % d["drift_m"])

# state() carries the drift block through.
gps.set(FakeFix(21.28000 + 0.00009, -157.83000))
check(cm.state()["drift"]["warn"] is True, "state().drift surfaces the warn flag")

print("\nALL %d CHECKS PASSED" % _n)
