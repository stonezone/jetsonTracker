#!/usr/bin/env python3
"""Send one raw VISCA pan/tilt stop plus zoom stop command.

This helper is intentionally narrow: systemd can call it after stopping
``wavecam.service`` without importing the web app, starting capture, or touching
the tracker loop. It is a final brake command, not an ownership bypass for
normal operation.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from wavecam.ptz_visca import ViscaIP  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ip", help="PTZ camera IP address")
    parser.add_argument("port", nargs="?", default=1259, type=int, help="VISCA UDP port")
    parser.add_argument("--address", default=1, type=int, help="VISCA camera address")
    parser.add_argument("--timeout", default=0.3, type=float, help="UDP socket timeout")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ptz = ViscaIP(args.ip, args.port, args.address, timeout=args.timeout)
    try:
        ptz.stop()
        ptz.zoom("stop")
    finally:
        ptz.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
