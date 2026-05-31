#!/usr/bin/env python3
"""Probe VISCA-over-IP on the PTZ camera: read pan/tilt + zoom position.

Confirms the VISCA framing/decoding works against the real camera before the
full backend is built on it. Tries raw UDP (port 1259) first, then Sony
VISCA-over-IP framing (port 52381). Read-only: only sends inquiries, no moves.

Run from the Orin (it is on the camera subnet):
    python3 scripts/probe_visca.py [--host 192.168.100.88]
"""

import argparse
import socket
import struct

PT_POS_INQ = bytes([0x81, 0x09, 0x06, 0x12, 0xFF])
ZOOM_POS_INQ = bytes([0x81, 0x09, 0x04, 0x47, 0xFF])
VERSION_INQ = bytes([0x81, 0x09, 0x00, 0x02, 0xFF])


def nibbles_to_int(b: bytes) -> int:
    """4 low-nibbles -> 16-bit signed int (VISCA position encoding)."""
    v = 0
    for x in b:
        v = (v << 4) | (x & 0x0F)
    return v - 0x10000 if v >= 0x8000 else v


def xchg_raw(sock, host, port, payload, timeout=2.0):
    sock.sendto(payload, (host, port))
    sock.settimeout(timeout)
    data, _ = sock.recvfrom(64)
    return data


def xchg_framed(sock, host, port, payload, seq, timeout=2.0):
    # Sony VISCA-over-IP header: type(2)=0x0100 cmd / 0x0110 inq, len(2), seq(4)
    ptype = 0x0110  # inquiry
    header = struct.pack(">HHI", ptype, len(payload), seq)
    sock.sendto(header + payload, (host, port))
    sock.settimeout(timeout)
    data, _ = sock.recvfrom(64)
    return data[8:] if len(data) > 8 else data  # strip header on reply


def decode(label, r):
    print(f"{label} raw: {r.hex()}")
    if len(r) >= 11 and r[0] == 0x90 and r[1] == 0x50 and label.startswith("PanTilt"):
        print(f"  -> pan={nibbles_to_int(r[2:6])} tilt={nibbles_to_int(r[6:10])}")
    elif len(r) >= 7 and r[0] == 0x90 and r[1] == 0x50 and label.startswith("Zoom"):
        print(f"  -> zoom={nibbles_to_int(r[2:6])}")


def probe(host, port, framed):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    mode = "framed(52381)" if framed else "raw(%d)" % port
    print(f"=== VISCA {mode} @ {host}:{port} ===")
    ok = False
    seq = 1
    for label, payload in [("Version", VERSION_INQ), ("PanTilt", PT_POS_INQ), ("Zoom", ZOOM_POS_INQ)]:
        try:
            r = xchg_framed(s, host, port, payload, seq) if framed else xchg_raw(s, host, port, payload)
            seq += 1
            decode(label, r)
            ok = True
        except Exception as e:
            print(f"{label}: FAILED ({e})")
    s.close()
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.100.88")
    args = ap.parse_args()
    if not probe(args.host, 1259, framed=False):
        print()
        probe(args.host, 52381, framed=True)


if __name__ == "__main__":
    main()
