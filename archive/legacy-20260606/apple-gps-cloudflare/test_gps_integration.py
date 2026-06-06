#!/usr/bin/env python3
"""End-to-end integration test for the GPS pipeline (no hardware).

Spins up a real RobotGpsServer in a background thread, connects a sender that
pushes RelayUpdate envelopes the way the Watch/iPhone do (including one stale
and one out-of-order fix), and a GPSClient consumer. Verifies:

  * the consumer receives BOTH base (iPhone) and remote (Watch) via the
    RelayUpdate envelope -> proves the envelope-parse fix in gps_client, and
  * the server DROPS the stale and out-of-order fixes.

Self-contained and self-terminating (daemon server thread); safe for CI.
"""

import os

os.environ.setdefault("GPS_HOST", "127.0.0.1")
os.environ.setdefault("GPS_PORT", "8799")

import asyncio  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import websockets  # noqa: E402

from gps_server import RobotGpsServer, PORT  # noqa: E402
from gps_fusion.gps_client import GPSClient  # noqa: E402

URI = f"ws://127.0.0.1:{PORT}"


def _fix(source, seq, lat, lon, age_ms=0):
    return {
        "ts_unix_ms": int(time.time() * 1000) - age_ms,
        "source": source,
        "lat": lat,
        "lon": lon,
        "alt_m": 1.0,
        "h_accuracy_m": 3.0,
        "v_accuracy_m": 4.0,
        "speed_mps": 7.0,
        "course_deg": 200.0,
        "battery_pct": 0.8,
        "seq": seq,
    }


async def send_track():
    async with websockets.connect(URI) as ws:
        for seq in range(6):
            base = _fix("iOS", seq, 21.2760, -157.8270)
            remote = _fix("watchOS", seq, 21.2755 + seq * 1e-4, -157.8285)
            await ws.send(json.dumps({"base": base, "remote": remote}))
            await asyncio.sleep(0.1)
        # Fault 1: stale watch fix (6 s old) -> server must drop.
        await ws.send(json.dumps({"remote": _fix("watchOS", 6, 21.276, -157.829, age_ms=6000)}))
        # Fault 2: out-of-order watch fix (seq regressed) -> server must drop.
        await ws.send(json.dumps({"remote": _fix("watchOS", 1, 21.276, -157.829)}))
        await asyncio.sleep(0.3)


def main():
    server = RobotGpsServer()
    threading.Thread(target=lambda: asyncio.run(server.start()), daemon=True).start()
    time.sleep(1.0)  # let the server bind

    client = GPSClient(uri=URI)
    client.start()
    time.sleep(0.5)  # let the consumer connect before traffic flows

    asyncio.run(send_track())
    time.sleep(0.5)  # let final broadcasts arrive

    st = client.get_state()
    client.stop()

    checks = [
        ("consumer connected", st.connected or st.fixes_received["watchOS"] > 0),
        ("envelope parse: got Watch (remote)", st.target is not None),
        ("envelope parse: got iPhone (base)", st.gimbal is not None),
        ("watch fixes >= 6", st.fixes_received["watchOS"] >= 6),
        ("iphone fixes >= 6", st.fixes_received["iOS"] >= 6),
        ("server dropped >= 1 stale", server.dropped_stale >= 1),
        ("server dropped >= 1 out-of-order", server.dropped_out_of_order >= 1),
        ("consumer not fed the dropped fixes", st.fixes_received["watchOS"] <= 7),
    ]
    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print(
        f"\nserver: dropped_stale={server.dropped_stale} "
        f"dropped_out_of_order={server.dropped_out_of_order} "
        f"by_source={server.fixes_by_source}"
    )
    print(
        f"client: watch={st.fixes_received['watchOS']} iphone={st.fixes_received['iOS']} "
        f"dropped={st.dropped} target={'set' if st.target else 'none'} "
        f"gimbal={'set' if st.gimbal else 'none'}"
    )
    if failed:
        print(f"\n{len(failed)} check(s) FAILED")
        sys.exit(1)
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
