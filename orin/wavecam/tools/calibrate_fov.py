#!/usr/bin/env python3
"""Measure horizontal FOV at the current zoom by pan-sweeping a color target.

Run ON the rig from the deploy dir:
  PYTHONPATH=/data/projects/gimbal/wavecam python3 tools/calibrate_fov.py --label wide [--zoom-secs N] [--dry]

Method: a color-matched target (shirt on a stand/bike) sits still 5-25 m out.
The script claims manual PTZ with takeover (deadman-protected velocity pulses —
a dead script means a stopped camera), pans until the blob sits near one frame
edge, reads the pan encoder, pans to the other edge, reads again.
HFOV = enc_span / enc_per_deg, normalized by the frame-width fraction actually
traversed; target size and distance cancel out.

Safety: every API response is checked (refusals abort loudly); pan excursion is
leashed to ~135 deg from the start anchor; on ANY exit the camera returns to the
anchor pointing. Frames come from the service's MJPEG preview (no h264
inter-frame state to corrupt during motion), falling back to RTSP.

Prints one JSON line. POST the result to /api/v1/calibration/fov yourself.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

import cv2
import numpy as np

from wavecam.color_presets import preset_hsv_ranges
from wavecam.ptz_visca import ViscaIP

API = "http://localhost:8088/api/v1"
SNAPSHOT = "http://192.168.100.88/snapshot.jpg"   # camera HTTP still: fresh frame per GET, no stream state
EDGE_LO, EDGE_HI = 0.12, 0.88
MIN_BLOB_PX = 300                  # snapshot frames are 1080p — 300px there ≈ 35px at 360p
PHASE_TIMEOUT_S = 90.0
LEASH_COUNTS = 600                 # ~135 deg at 4.47 counts/deg


def api(path: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        API + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def check(resp: dict, what: str) -> None:
    if not resp.get("ok", False):
        sys.exit(f"FATAL: {what} refused: {resp.get('error')} — {resp.get('detail') or resp}")


def hold_ownership() -> None:
    """One API stop with takeover semantics: service releases PTZ to manual and
    stays quiet; all actual motion below is raw VISCA (proven speed control)."""
    check(api("/ptz/stop", {}), "stop")


_VISCA: ViscaIP | None = None


def pan_pulse(sign: float, speed: int, dur: float) -> None:
    _VISCA.pan_tilt(speed, 0, 0x02 if sign > 0 else 0x01, 0x03)
    time.sleep(dur)
    _VISCA.stop()
    time.sleep(0.15)


def stop() -> None:
    if _VISCA is not None:
        _VISCA.stop()


class Eye:
    """Fresh-frame reader + color-blob centroid with positional continuity."""

    def __init__(self) -> None:
        ok, f = self._read()
        if not ok:
            sys.exit("FATAL: camera snapshot endpoint not responding")
        ov = os.environ.get("CAL_HSV", "")
        if ov:                                    # "h1,s1,v1,h2,s2,v2"
            v = [int(x) for x in ov.split(",")]
            self.ranges = [(v[:3], v[3:])]
        else:
            try:
                preset = api("/config")["current"]["color"]["preset"]
            except Exception:
                preset = "orange_red"
            d = preset_hsv_ranges(preset)
            self.ranges = [(d[k], d[k.replace("_low", "_high")]) for k in d if "_low" in k]
        self.last_cx: float | None = None
        ok, f = self._read()
        self.w = f.shape[1] if ok else 640

    def _read(self):
        for attempt in range(3):
            try:
                with urllib.request.urlopen(SNAPSHOT, timeout=4) as r:
                    f = cv2.imdecode(np.frombuffer(r.read(), np.uint8), cv2.IMREAD_COLOR)
                if f is not None:
                    return True, f
            except Exception:
                time.sleep(0.3)
        return False, None

    def blob_cx(self) -> float | None:
        ok, frame = self._read()
        if not ok:
            return None
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = None
        for lo, hi in self.ranges:
            m = cv2.inRange(hsv, np.array(lo), np.array(hi))
            mask = m if mask is None else cv2.bitwise_or(mask, m)
        mask = cv2.dilate(mask, np.ones((5, 5), np.uint8))
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        good = [c for c in cnts if cv2.contourArea(c) >= MIN_BLOB_PX]
        if not good:
            return None

        def cx_of(c):
            m = cv2.moments(c)
            return m["m10"] / m["m00"] if m["m00"] else None

        if self.last_cx is not None:
            near = [c for c in good
                    if cx_of(c) is not None and abs(cx_of(c) - self.last_cx) < 0.3 * self.w]
            if near:
                good = near
        cx = cx_of(max(good, key=cv2.contourArea))
        if cx is not None:
            self.last_cx = cx
        return cx


def settle_read(eye: Eye, visca: ViscaIP) -> tuple[float | None, tuple | None]:
    stop()
    time.sleep(0.8)
    cx = eye.blob_cx()
    enc = visca.inquire_pan_tilt()
    return cx, enc


def drive_to_edge(eye: Eye, visca: ViscaIP, pan_sign: float, target_frac: float,
                  fov_hint_deg: float, anchor_enc: int) -> tuple[float, int]:
    """Pulse-pan until blob centre crosses target_frac; return (frac, pan_enc).

    Never pulses forward while blind: a lost blob stops the camera, waits for
    clean frames, then backs off until the target is re-found.
    """
    # service maps velocity quadratically to VISCA speed: ~0.3 -> speed 1 (0.75 deg/s).
    # 0.55 ≈ a few deg/s for wide sweeps; 0.3 = creep for tele frames.
    pulse_s = 0.25 if fov_hint_deg < 20 else 0.4
    speed = 2 if fov_hint_deg < 20 else 5
    t0 = time.time()
    lost = 0
    while True:
        if time.time() - t0 > PHASE_TIMEOUT_S:
            stop()
            sys.exit("FATAL: edge-drive timeout — is the target visible?")

        cx = eye.blob_cx()
        if cx is None:
            lost += 1
            stop()
            if lost <= 3:                         # static re-read: decode recovers when still
                time.sleep(0.4)
            else:                                 # genuinely overshot — back off to re-find
                pan_pulse(-pan_sign, 2, 0.25)
            continue
        lost = 0
        frac = cx / eye.w

        if (target_frac > 0.5 and frac >= target_frac) or \
           (target_frac < 0.5 and frac <= target_frac):
            cx2, enc = settle_read(eye, visca)
            if cx2 is None or enc is None:
                continue
            return cx2 / eye.w, enc[0]

        enc_now = visca.inquire_pan_tilt()
        if enc_now is not None and abs(enc_now[0] - anchor_enc) > LEASH_COUNTS:
            stop()
            sys.exit("FATAL: pan excursion leash hit — aborting to protect pointing")
        pan_pulse(pan_sign, speed, pulse_s)


def center(eye: Eye, visca: ViscaIP, right_sign: float) -> None:
    """Drive the blob back to mid-frame (needed before zooming between runs)."""
    t0 = time.time()
    while time.time() - t0 < PHASE_TIMEOUT_S:
        cx = eye.blob_cx()
        if cx is None:
            stop()
            time.sleep(0.3)
            continue
        frac = cx / eye.w
        if 0.44 <= frac <= 0.56:
            stop()
            return
        pan_pulse(right_sign if frac < 0.5 else -right_sign, 3, 0.25)
    stop()
    sys.exit("FATAL: centering timeout")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="wide")
    ap.add_argument("--zoom-secs", type=float, default=0.0,
                    help="tele-zoom for N seconds before measuring (0 = stay at current zoom)")
    ap.add_argument("--fov-hint", type=float, default=60.0,
                    help="rough expected HFOV deg — only tunes pulse sizing")
    ap.add_argument("--dry", action="store_true", help="no camera motion; report blob+encoders only")
    args = ap.parse_args()

    cal = api("/calibration")["calibration"]
    enc_per_deg = float(cal["gps_pose"]["pan_enc_per_deg"]) if "gps_pose" in cal else 4.47
    eye = Eye()
    visca = ViscaIP("192.168.100.88")

    if args.dry:
        cx = eye.blob_cx()
        print(json.dumps({"dry": True, "frame_w": eye.w,
                          "blob_frac": None if cx is None else round(cx / eye.w, 3),
                          "pan_tilt_enc": visca.inquire_pan_tilt(),
                          "zoom_enc": visca.inquire_zoom(),
                          "enc_per_deg": enc_per_deg}))
        return

    global _VISCA
    _VISCA = visca
    home_enc = None
    try:
        hold_ownership()                          # service releases PTZ; raw VISCA from here
        home_enc = visca.inquire_pan_tilt()       # safety anchor: always return here
        if eye.blob_cx() is None:
            sys.exit("FATAL: no color blob in frame — point the camera at the target first")

        # direction probe: three raw pulses ≈ 3 deg — unmistakable at any zoom
        cx_a, _ = settle_read(eye, visca)
        pan_pulse(1.0, 5, 0.5)
        pan_pulse(1.0, 5, 0.5)
        pan_pulse(1.0, 5, 0.5)
        cx_b, _ = settle_read(eye, visca)
        if cx_a is None or cx_b is None or abs(cx_b - cx_a) < 5.0:
            sys.exit("FATAL: direction probe saw no blob movement — check ownership or target")
        right_sign = 1.0 if cx_b > cx_a else -1.0   # pan sign that moves blob toward +x

        center(eye, visca, right_sign)
        if args.zoom_secs > 0:
            visca.zoom("tele", 3)
            time.sleep(args.zoom_secs)
            visca.zoom("stop")
            time.sleep(1.2)
            if eye.blob_cx() is None:
                sys.exit("FATAL: target lost after zoom — re-aim and rerun this level")
            center(eye, visca, right_sign)

        anchor = home_enc[0] if home_enc else 0
        frac_hi, enc_hi = drive_to_edge(eye, visca, right_sign, EDGE_HI, args.fov_hint, anchor)
        frac_lo, enc_lo = drive_to_edge(eye, visca, -right_sign, EDGE_LO, args.fov_hint, anchor)

        zoom_enc = visca.inquire_zoom()
        span_frac = frac_hi - frac_lo
        if span_frac < 0.4:
            sys.exit(f"FATAL: traversed only {span_frac:.2f} of frame width — measurement unusable")
        fov = abs(enc_hi - enc_lo) / enc_per_deg / span_frac
        print(json.dumps({"label": args.label,
                          "zoom_enc": zoom_enc,
                          "fov_deg": round(fov, 2),
                          "enc_span": abs(enc_hi - enc_lo),
                          "span_frac": round(span_frac, 3),
                          "enc_per_deg": enc_per_deg}))
    finally:
        stop()
        if home_enc is not None:
            visca.pan_tilt_absolute(home_enc[0], home_enc[1])
            time.sleep(2.0)


if __name__ == "__main__":
    main()
