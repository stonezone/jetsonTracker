#!/usr/bin/env python3
"""Replay a synthetic foil-surfing GPS track into a running gps_server.

Connects to gps_server (default ws://localhost:8765) and SENDS RelayUpdate
envelopes the way the Watch/iPhone do, so the whole receive -> filter ->
broadcast path is exercised without any hardware. Run gps_monitor.py in another
terminal to watch fixes arrive.

With --faults it also injects one stale fix (old timestamp) and one
out-of-order fix (regressed seq); gps_server should DROP both (visible as
dropped_stale / dropped_out_of_order in its stats and logs).

Usage:
    python3 scripts/gps_replay.py [--uri ws://localhost:8765] [--hz 1.0]
                                  [--duration 60] [--faults]
"""

import argparse
import asyncio
import json
import math
import os
import sys
import time

try:
    import websockets
except ImportError:
    print("websockets not installed: pip3 install websockets")
    sys.exit(1)

EARTH_R = 6_371_000.0

# Beach / base station (iPhone next to the camera). Placeholder coordinates.
BASE_LAT, BASE_LON = 21.2760, -157.8270


def move(lat, lon, bearing_deg, dist_m):
    """Return lat/lon moved dist_m along bearing_deg from (lat, lon)."""
    br = math.radians(bearing_deg)
    lat1, lon1 = math.radians(lat), math.radians(lon)
    dr = dist_m / EARTH_R
    lat2 = math.asin(math.sin(lat1) * math.cos(dr) +
                     math.cos(lat1) * math.sin(dr) * math.cos(br))
    lon2 = lon1 + math.atan2(math.sin(br) * math.sin(dr) * math.cos(lat1),
                             math.cos(dr) - math.sin(lat1) * math.sin(lat2))
    return math.degrees(lat2), math.degrees(lon2)


def fix(source, lat, lon, seq, speed=0.0, course=0.0, ts_ms=None,
        acc=3.0, alt=0.0, heading=None, battery=0.8):
    f = {
        "ts_unix_ms": ts_ms if ts_ms is not None else int(time.time() * 1000),
        "source": source,
        "lat": lat,
        "lon": lon,
        "alt_m": alt,
        "h_accuracy_m": acc,
        "v_accuracy_m": acc * 1.5,
        "speed_mps": speed,
        "course_deg": course,
        "battery_pct": battery,
        "seq": seq,
    }
    if heading is not None:
        f["heading_deg"] = heading
    return f


async def run(uri, hz, duration, faults):
    period = 1.0 / hz
    # Surfer starts ~80 m offshore on a bearing of ~200 deg from base.
    t_lat, t_lon = move(BASE_LAT, BASE_LON, 200.0, 80.0)
    seq = base_seq = 0
    t0 = time.time()
    injected_stale = injected_ooo = False

    async with websockets.connect(uri) as ws:
        print(f"[replay] connected to {uri}; sending {hz:.1f} Hz for "
              f"{duration:.0f}s (faults={faults})")
        while time.time() - t0 < duration:
            t = time.time() - t0
            # S-turn foiling: heading oscillates, speed ~6-9 m/s.
            course = 200.0 + 50.0 * math.sin(t / 6.0)
            speed = 7.5 + 1.5 * math.sin(t / 3.0)
            t_lat, t_lon = move(t_lat, t_lon, course, speed * period)

            base = fix("iOS", BASE_LAT, BASE_LON, base_seq, heading=210.0,
                       acc=4.0, alt=2.0)
            remote = fix("watchOS", t_lat, t_lon, seq, speed=speed,
                         course=course, acc=3.0, alt=0.5)
            await ws.send(json.dumps({"base": base, "remote": remote}))
            seq += 1
            base_seq += 1

            if faults and t > 5 and not injected_stale:
                stale = fix("watchOS", t_lat, t_lon, seq,
                            ts_ms=int(time.time() * 1000) - 6000)  # 6 s old
                await ws.send(json.dumps({"remote": stale}))
                seq += 1
                injected_stale = True
                print("[replay] injected STALE watch fix (server should DROP)")
            if faults and t > 7 and not injected_ooo:
                ooo = fix("watchOS", t_lat, t_lon, max(seq - 5, 0))  # regressed
                await ws.send(json.dumps({"remote": ooo}))
                injected_ooo = True
                print("[replay] injected OUT-OF-ORDER watch fix (server should DROP)")

            await asyncio.sleep(period)
    print("[replay] done")


def main():
    ap = argparse.ArgumentParser(description="Replay synthetic foiling GPS track")
    ap.add_argument("--uri", default=os.environ.get("GPS_URI", "ws://localhost:8765"))
    ap.add_argument("--hz", type=float, default=1.0)
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--faults", action="store_true",
                    help="inject one stale + one out-of-order fix to test rejection")
    args = ap.parse_args()
    asyncio.run(run(args.uri, args.hz, args.duration, args.faults))


if __name__ == "__main__":
    main()
