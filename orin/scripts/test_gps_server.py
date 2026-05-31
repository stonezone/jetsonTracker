#!/usr/bin/env python3
"""Unit tests for gps_server fix acceptance (stale / out-of-order rejection).

Pure-logic tests for RobotGpsServer._should_accept; no sockets, no async.
Run directly (python3 scripts/test_gps_server.py) or via pytest.
"""

import asyncio
import json
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gps_server import RobotGpsServer, LocationFix, LocationSource  # noqa: E402


class _DummyWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(message)


def _fix(seq, source=LocationSource.WATCH, age_ms=0):
    return LocationFix(
        lat=21.3, lon=-157.8, timestamp_ms=int(time.time() * 1000) - age_ms,
        source=source, horizontal_accuracy=3.0, vertical_accuracy=4.0,
        speed_mps=7.0, course_deg=200.0, battery_pct=0.8, sequence=seq,
    )


def _cbor_uint(value):
    if value <= 23:
        return bytes([value])
    if value <= 0xFF:
        return bytes([0x18, value])
    if value <= 0xFFFF:
        return b"\x19" + value.to_bytes(2, "big")
    if value <= 0xFFFFFFFF:
        return b"\x1a" + value.to_bytes(4, "big")
    return b"\x1b" + value.to_bytes(8, "big")


def _cbor_double(value):
    return b"\xfb" + struct.pack(">d", value)


def _cbor_fix(seq, source=0):
    ts_ms = int(time.time() * 1000)
    return (
        b"\xac"
        + b"\x00" + _cbor_uint(ts_ms)
        + b"\x01" + _cbor_uint(source)
        + b"\x02" + _cbor_double(21.3)
        + b"\x03" + _cbor_double(-157.8)
        + b"\x04\xf6"
        + b"\x05" + _cbor_double(3.0)
        + b"\x06" + _cbor_double(4.0)
        + b"\x07" + _cbor_double(7.0)
        + b"\x08" + _cbor_double(200.0)
        + b"\x09\xf6"
        + b"\x0a" + _cbor_double(0.8)
        + b"\x0b" + _cbor_uint(seq)
    )


def _cbor_relay(remote_seq=None, base_seq=None):
    fields = []
    if base_seq is not None:
        fields.append(b"\x00" + _cbor_fix(base_seq, source=1))
    if remote_seq is not None:
        fields.append(b"\x01" + _cbor_fix(remote_seq, source=0))
    return bytes([0xA0 | len(fields)]) + b"".join(fields)


def test_fresh_in_order_accepted():
    s = RobotGpsServer()
    assert s._should_accept(_fix(0))
    assert s._should_accept(_fix(1))
    assert s._should_accept(_fix(2))
    assert s.dropped_stale == 0 and s.dropped_out_of_order == 0


def test_out_of_order_dropped():
    s = RobotGpsServer()
    assert s._should_accept(_fix(10))
    assert not s._should_accept(_fix(9))    # regressed
    assert not s._should_accept(_fix(10))   # duplicate
    assert s.dropped_out_of_order == 2


def test_sequence_reset_accepted():
    s = RobotGpsServer()
    assert s._should_accept(_fix(5000))
    # Large backward jump => device restart => accept + re-baseline.
    assert s._should_accept(_fix(0))
    assert s.dropped_out_of_order == 0


def test_stale_dropped():
    s = RobotGpsServer()
    assert s._should_accept(_fix(0))                    # baseline offset ~0
    assert s._should_accept(_fix(1))
    assert not s._should_accept(_fix(2, age_ms=6000))   # 6 s old -> stale
    assert s.dropped_stale == 1


def test_constant_clock_skew_not_dropped():
    # All fixes "10 s old" (constant skew) must NOT be treated as stale.
    s = RobotGpsServer()
    assert s._should_accept(_fix(0, age_ms=10000))
    assert s._should_accept(_fix(1, age_ms=10000))
    assert s._should_accept(_fix(2, age_ms=10000))
    assert s.dropped_stale == 0


def test_per_source_independent():
    s = RobotGpsServer()
    assert s._should_accept(_fix(100, source=LocationSource.WATCH))
    assert s._should_accept(_fix(1, source=LocationSource.IOS))
    assert s.dropped_out_of_order == 0


def test_binary_ping_frame_accepted():
    s = RobotGpsServer()
    ws = _DummyWebSocket()
    message = json.dumps({"type": "ping", "id": "binary-test"}).encode("utf-8")

    asyncio.run(s.process_message(ws, message))

    assert len(ws.sent) == 1
    assert json.loads(ws.sent[0]) == {"type": "pong", "id": "binary-test"}


def test_binary_relay_frame_accepted_and_acked():
    s = RobotGpsServer()
    ws = _DummyWebSocket()
    payload = {
        "remote": {
            "ts_unix_ms": int(time.time() * 1000),
            "source": "watchOS",
            "lat": 21.3,
            "lon": -157.8,
            "alt_m": 1.0,
            "h_accuracy_m": 3.0,
            "v_accuracy_m": 4.0,
            "speed_mps": 7.0,
            "course_deg": 200.0,
            "battery_pct": 0.8,
            "seq": 42,
        }
    }

    asyncio.run(s.process_message(ws, json.dumps(payload).encode("utf-8")))

    assert s.last_watch_fix is not None
    assert s.last_watch_fix.sequence == 42
    assert len(ws.sent) == 1
    ack = json.loads(ws.sent[0])
    assert ack["type"] == "ack"
    assert ack["seq"] == 42
    assert ack["accepted"] is True


def test_cbor_relay_frame_accepted_and_rebroadcast_as_json():
    s = RobotGpsServer()
    sender = _DummyWebSocket()
    receiver = _DummyWebSocket()
    s.connected_clients.add(sender)
    s.connected_clients.add(receiver)

    asyncio.run(s.process_message(sender, _cbor_relay(remote_seq=77, base_seq=78)))

    assert s.last_watch_fix is not None
    assert s.last_watch_fix.sequence == 77
    assert s.last_iphone_fix is not None
    assert s.last_iphone_fix.sequence == 78
    assert len(sender.sent) == 1
    assert json.loads(sender.sent[0])["accepted"] is True
    assert len(receiver.sent) == 1
    rebroadcast = json.loads(receiver.sent[0])
    assert rebroadcast["remote"]["seq"] == 77
    assert rebroadcast["base"]["seq"] == 78


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
