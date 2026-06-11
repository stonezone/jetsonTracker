#!/usr/bin/env python3
"""Measure horizontal FOV at the current zoom by pan-sweeping the orange subject.

Run ON the rig from the deploy dir:
  PYTHONPATH=/data/projects/gimbal/wavecam python3 tools/calibrate_fov.py --label wide [--zoom-secs N] [--dry]

Method: the subject (orange rashguard) stands still ~15-25 m out. The script
claims manual PTZ (deadman-protected velocity pulses — a dead script means a
stopped camera), pans until the orange blob sits near one frame edge, reads the
pan encoder, pans to the other edge, reads again. HFOV = enc_span / enc_per_deg
normalized by the fraction of frame width actually traversed. The subject's size
and distance cancel out — only the encoders and the blob centroid matter.

Prints one JSON line: {"label", "zoom_enc", "fov_deg", ...}. POST the result to
/api/v1/calibration/fov yourself (kept manual so a bad sweep is never persisted
by accident).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

import cv2
import numpy as np

from wavecam.color_presets import preset_hsv_ranges
from wavecam.ptz_visca import ViscaIP

API = "http://localhost:8088/api/v1"
RTSP = "rtsp://192.168.100.88:554/2"
EDGE_LO, EDGE_HI = 0.12, 0.88     # target blob-centre fractions for the two edges
MIN_BLOB_PX = 150                  # sweep-time floor; stricter than the tracker's min_area=60 so edge reads are unambiguous
PHASE_TIMEOUT_S = 90.0


def api(path: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        API + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def velocity(pan: float) -> None:
    # takeover: the live tracker can re-acquire ownership mid-sweep when a deadman
    # expires; 1500ms rides out a slow grab+decode iteration while a dead script
    # still stops the camera within 1.5s.
    api("/ptz/velocity", {"pan": pan, "tilt": 0.0, "zoom": 0.0,
                          "deadman_ms": 1500, "takeover": True})


def stop() -> None:
    api("/ptz/stop", {})


class Eye:
    """Fresh-frame reader + orange-blob centroid."""

    def __init__(self) -> None:
        self.cap = cv2.VideoCapture(RTSP, cv2.CAP_FFMPEG)
        if not self.cap.isOpened():
            sys.exit(f"FATAL: cannot open {RTSP}")
        import os
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
        self.w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640

    def blob_cx(self) -> float | None:
        for _ in range(4):                       # flush stale buffered frames
            self.cap.grab()
        ok, frame = self.cap.retrieve()
        if not ok:
            return None
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = None
        for lo, hi in self.ranges:
            m = cv2.inRange(hsv, np.array(lo), np.array(hi))
            mask = m if mask is None else cv2.bitwise_or(mask, m)
        mask = cv2.dilate(mask, np.ones((5, 5), np.uint8))
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            return None
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
                  fov_hint_deg: float, anchor_enc: int = 0) -> tuple[float, int]:
    """Pulse-pan until blob centre crosses target_frac; return (frac, pan_enc)."""
    # shorter pulses when zoomed in (frame crosses faster)
    pulse_s = max(0.15, min(0.45, fov_hint_deg / 80.0))
    speed = 0.08 if fov_hint_deg < 12 else 0.14
    t0 = time.time()
    lost = 0
    while True:
        if time.time() - t0 > PHASE_TIMEOUT_S:
            stop()
            sys.exit("FATAL: edge-drive timeout — is the subject visible and orange?")
        enc_now = visca.inquire_pan_tilt()
        if enc_now is not None and abs(enc_now[0] - anchor_enc) > 600:
            stop()
            sys.exit("FATAL: pan excursion leash hit — aborting to protect pointing")
        velocity(pan_sign * speed)
        time.sleep(pulse_s)
        cx = eye.blob_cx()
        if cx is None:
            lost += 1
            if lost >= 3:                        # overshot — back off until refound
                velocity(-pan_sign * 0.06)
                time.sleep(0.25)
            continue
        lost = 0
        frac = cx / eye.w
        if (target_frac > 0.5 and frac >= target_frac) or \
           (target_frac < 0.5 and frac <= target_frac):
            cx2, enc = settle_read(eye, visca)
            if cx2 is None or enc is None:
                continue                          # settle read failed; keep nudging
            return cx2 / eye.w, enc[0]


def center(eye: Eye, visca: ViscaIP, right_sign: float) -> None:
    """Drive the blob back to mid-frame (needed before zooming between runs)."""
    t0 = time.time()
    while time.time() - t0 < PHASE_TIMEOUT_S:
        cx = eye.blob_cx()
        if cx is None:
            time.sleep(0.3)
            continue
        frac = cx / eye.w
        if 0.44 <= frac <= 0.56:
            stop()
            return
        velocity((right_sign if frac < 0.5 else -right_sign) * 0.08)
        time.sleep(0.25)
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

    home_enc = None
    try:
        stop()                                    # takes manual ownership, halts motion
        home_enc = visca.inquire_pan_tilt()       # safety anchor: always return here
        if eye.blob_cx() is None:
            sys.exit("FATAL: no orange blob in frame — point the camera at the subject first")

        # learn pan-direction -> cx mapping with one small pulse (valid across zoom levels)
        cx_a, _ = settle_read(eye, visca)
        velocity(0.08)
        time.sleep(0.4)
        cx_b, _ = settle_read(eye, visca)
        if cx_a is None or cx_b is None or abs(cx_b - cx_a) < 1.0:
            sys.exit("FATAL: direction probe saw no blob movement — owner not granted or subject lost")
        right_sign = 1.0 if cx_b > cx_a else -1.0   # pan sign that moves blob toward +x

        center(eye, visca, right_sign)
        if args.zoom_secs > 0:
            t_end = time.time() + args.zoom_secs
            while time.time() < t_end:
                api("/ptz/zoom", {"value": 0.6, "deadman_ms": 1500, "takeover": True})
                time.sleep(0.4)
            stop()   # zoom value=0.0 RELEASES manual ownership — stop() halts and keeps it
            time.sleep(1.2)
            if eye.blob_cx() is None:
                sys.exit("FATAL: subject lost after zoom — re-aim and rerun this level")
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
