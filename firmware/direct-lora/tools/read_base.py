#!/usr/bin/env python3
"""Read + summarize the direct-LoRa base serial — the Phase-3 instrument.

The base emits two JSON line types over USB serial (115200): tracker packets
(a "seq" field) and the base's own position (a "base" field). This reads them
and prints rolling link + GPS stats so the outdoor test is quantitative:
delivered packet rate, loss, RSSI/SNR spread, the remote's fix + measured GPS
cadence, and the base's settle/stable state.

Read-only and standalone — NOT the production ingest (that's the Orin's
DirectRadioGps). Runs on Linux/the Orin (the base enumerates as a CDC ACM
there; macOS won't enumerate it — known build limitation). Stdlib only.

Usage:
  python3 read_base.py [--port /dev/ttyACM0] [--seconds 0] [--every 2.0]
"""
from __future__ import annotations

import argparse
import json
import sys
import time


def _open_port(port: str):
    # CDC ACM ignores baud; a plain line-reading open is enough on Linux. Best
    # effort raw mode so the terminal layer doesn't mangle/echo the stream.
    try:
        import termios
        import tty
        fd = open(port, "rb", buffering=0)
        try:
            tty.setraw(fd.fileno())
        except (termios.error, OSError):
            pass
        return fd
    except ImportError:
        return open(port, "rb", buffering=0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--seconds", type=float, default=0.0, help="0 = until Ctrl-C")
    ap.add_argument("--every", type=float, default=2.0, help="summary interval s")
    a = ap.parse_args()

    try:
        fd = _open_port(a.port)
    except OSError as e:
        print(f"cannot open {a.port}: {e}", file=sys.stderr)
        return 1

    start = win = _mono()
    rx0 = rx_win = None          # received counters (rolling-window rate)
    last_lost = 0
    rssis: list[int] = []        # x10
    snrs: list[int] = []
    remote = {"fix": 0, "gps_age_ms": None, "seq": None}
    base = {"fix": 0, "stable": 0, "hold_s": 0, "hdop_x10": None,
            "lat_e7": None, "lon_e7": None}
    bad = 0
    buf = b""

    def summary(tag: str) -> None:
        now = _mono()
        dt = max(1e-3, now - win)
        rate = ((rx_win is not None and rx0 is not None)
                and (rx_win - rx0) / dt) or 0.0
        rmin = min(rssis) / 10 if rssis else float("nan")
        rmax = max(rssis) / 10 if rssis else float("nan")
        rmean = (sum(rssis) / len(rssis) / 10) if rssis else float("nan")
        smean = (sum(snrs) / len(snrs) / 10) if snrs else float("nan")
        print(f"[{tag} +{now-start:5.0f}s] "
              f"rx={remote['seq']} rate={rate:.1f}/s lost={last_lost} bad={bad} | "
              f"rssi {rmin:.0f}/{rmean:.0f}/{rmax:.0f}dBm snr~{smean:.0f}dB | "
              f"remote fix={remote['fix']} age={remote['gps_age_ms']}ms | "
              f"base fix={base['fix']} stable={base['stable']} hold={base['hold_s']}s "
              f"hdop={(base['hdop_x10'] or 0)/10:.1f}")

    try:
        while True:
            now = _mono()
            if a.seconds and now - start >= a.seconds:
                break
            chunk = fd.read(256)
            if chunk:
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    s = line.strip().decode("utf-8", "replace")
                    if not s.startswith("{"):
                        continue
                    try:
                        d = json.loads(s)
                    except ValueError:
                        bad += 1
                        continue
                    if "seq" in d:
                        if rx0 is None:
                            rx0 = rx_win = d.get("rx", 0)
                        rx_win = d.get("rx", rx_win)
                        last_lost = d.get("lost", last_lost)
                        remote.update(seq=d.get("seq"), fix=d.get("fix", 0),
                                      gps_age_ms=d.get("gps_age_ms"))
                        if "rssi_x10" in d:
                            rssis.append(d["rssi_x10"])
                        if "snr_x10" in d:
                            snrs.append(d["snr_x10"])
                    elif d.get("base") == 1:
                        base.update(fix=d.get("fix", 0), stable=d.get("stable", 0),
                                    hold_s=d.get("hold_s", 0),
                                    hdop_x10=d.get("hdop_x10"),
                                    lat_e7=d.get("lat_e7"), lon_e7=d.get("lon_e7"))
                    elif "err" in d or "info" in d:
                        print(f"  [board] {s}")
            if now - win >= a.every:
                summary("live")
                win, rx0 = now, rx_win
                rssis.clear(), snrs.clear()
            if not chunk:
                time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    summary("END")
    if base["lat_e7"] is not None and base["stable"]:
        print(f"  base position (stable): "
              f"{base['lat_e7']/1e7:.7f}, {base['lon_e7']/1e7:.7f}")
    return 0


def _mono() -> float:
    return time.monotonic()


if __name__ == "__main__":
    raise SystemExit(main())
