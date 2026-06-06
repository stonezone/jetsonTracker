#!/usr/bin/env python3
"""Live read-only health monitor for the GPS pipeline.

Connects to gps_server (default ws://localhost:8765) as an ordinary local
client and renders a one-line dashboard each interval: connection state,
per-source fix counts + rate, last-fix age, base<->target distance, and
drop/error counters.

This is the Phase-1 "is the link alive" instrument. It takes no actions and
never sends anything but the client handshake.

Usage:
    python3 scripts/gps_monitor.py [--uri ws://localhost:8765] [--interval 1.0]
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gps_fusion.gps_client import GPSClient  # noqa: E402
from gps_fusion.geo_calc import haversine_distance  # noqa: E402


def _age(updated: float) -> str:
    if not updated:
        return "  --  "
    return f"{time.time() - updated:5.1f}s"


def main() -> None:
    ap = argparse.ArgumentParser(description="GPS pipeline health monitor (read-only)")
    ap.add_argument("--uri", default=os.environ.get("GPS_URI", "ws://localhost:8765"))
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args()

    client = GPSClient(uri=args.uri)
    client.start()
    print(f"[monitor] watching {args.uri} (Ctrl-C to quit)")

    prev = {"iOS": 0, "watchOS": 0}
    prev_t = time.time()
    try:
        while True:
            time.sleep(args.interval)
            st = client.get_state()
            now = time.time()
            dt = max(now - prev_t, 1e-6)

            w = st.fixes_received.get("watchOS", 0)
            i = st.fixes_received.get("iOS", 0)
            w_hz = (w - prev["watchOS"]) / dt
            i_hz = (i - prev["iOS"]) / dt
            prev = {"iOS": i, "watchOS": w}
            prev_t = now

            dist = ""
            if st.gimbal and st.target:
                dist = f"dist={haversine_distance(st.gimbal, st.target):6.1f}m"

            conn = "UP  " if st.connected else "DOWN"
            err = f" err={st.last_error}" if st.last_error else ""
            print(
                f"[{conn}] watch:{w:5d}({w_hz:4.1f}Hz) age={_age(st.target_updated)} | "
                f"iphone:{i:5d}({i_hz:4.1f}Hz) age={_age(st.gimbal_updated)} | "
                f"{dist} dropped={st.dropped}{err}"
            )
    except KeyboardInterrupt:
        print("\n[monitor] stopping")
    finally:
        client.stop()


if __name__ == "__main__":
    main()
