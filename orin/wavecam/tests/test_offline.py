"""
Offline self-test — verifies the two error-prone pieces without hardware or
torch/opencv: (1) RAW VISCA-over-IP byte sequences, (2) visual-servo direction/speed.

Run:  python -m tests.test_offline      (from the testbed root)
"""
from __future__ import annotations
import types

from wavecam import ptz_visca as V


def hexs(b: bytes) -> str:
    return " ".join(f"{x:02X}" for x in b)


def test_visca_raw():
    cam = V.ViscaIP("127.0.0.1", port=1259, address=1)
    assert cam.addr == 0x81, "address 1 must encode to 0x81"

    sent = {}
    cam._send = lambda p: sent.__setitem__("p", p)   # capture, never transmit

    # pan/tilt: RAW bytes, NO 8-byte VISCA-over-IP header; over-range speeds clamp
    cam.pan_tilt(99, 99, V.PAN_LEFT, V.TILT_DOWN)
    p = sent["p"]
    assert p[0] == 0x81, "RAW VISCA: first byte 0x81 (no framing header)"
    assert len(p) == 9, "pan_tilt is 9 raw bytes (no struct '>HHI' prefix)"
    assert p[4] == 0x18 and p[5] == 0x14, "speeds clamp to 24/20"
    assert p[6] == V.PAN_LEFT and p[7] == V.TILT_DOWN and p[-1] == 0xFF
    print("  raw pan_tilt:", hexs(p))

    cam.stop()
    assert sent["p"][6] == V.PAN_STOP and sent["p"][7] == V.TILT_STOP, "stop sets dirs to 0x03"
    print("  raw stop:    ", hexs(sent["p"]))

    cam.zoom("tele", 3)
    assert list(sent["p"]) == [0x81, 0x01, 0x04, 0x07, 0x23, 0xFF], "zoom tele speed-3"
    cam.home()
    assert list(sent["p"]) == [0x81, 0x01, 0x06, 0x04, 0xFF], "home bytes"
    print("[PASS] visca raw")


def test_servo():
    from wavecam.controller import VisualServo
    from wavecam.ptz_visca import PAN_LEFT, PAN_RIGHT, TILT_UP, TILT_DOWN, PAN_STOP, TILT_STOP

    cfg = types.SimpleNamespace(
        deadzone=0.10, max_pan_speed=10, max_tilt_speed=8, min_speed=1,
        invert_pan=False, invert_tilt=False,
    )
    s = VisualServo(cfg)
    W, H = 640, 360

    # centered -> stop
    assert s.compute((320, 180), (W, H)).is_stop, "dead-center must stop"

    # target right -> pan RIGHT, speed scales up but below max
    c = s.compute((620, 180), (W, H))
    assert c.pan_dir == PAN_RIGHT and c.tilt_dir == TILT_STOP, "right target -> pan right"
    assert cfg.min_speed < c.pan_speed <= cfg.max_pan_speed, "right -> scaled pan speed"

    # target at the frame edge -> max pan speed
    assert s.compute((W, 180), (W, H)).pan_speed == cfg.max_pan_speed, "edge -> max pan speed"

    # target far left -> pan LEFT
    assert s.compute((20, 180), (W, H)).pan_dir == PAN_LEFT, "left target -> pan left"

    # target high -> tilt UP ; low -> tilt DOWN
    assert s.compute((320, 10), (W, H)).tilt_dir == TILT_UP, "high target -> tilt up"
    assert s.compute((320, 350), (W, H)).tilt_dir == TILT_DOWN, "low target -> tilt down"

    # invert flips pan
    cfg.invert_pan = True
    assert s.compute((620, 180), (W, H)).pan_dir == PAN_LEFT, "invert_pan flips direction"

    # None target -> stop
    assert s.compute(None, (W, H)).is_stop, "no target -> stop"
    print("[PASS] visual servo")


if __name__ == "__main__":
    test_visca_raw()
    test_servo()
    print("\nALL OFFLINE TESTS PASSED")
