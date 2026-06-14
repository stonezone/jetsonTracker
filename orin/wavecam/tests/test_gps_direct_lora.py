"""Unit tests for direct-LoRa USB serial GPS ingest."""
from __future__ import annotations

import sys
import threading
import time
import types

from wavecam.gps_direct_lora import DirectRadioGps


def test_remote_seq_line_updates_subject_fix_from_scaled_json():
    g = DirectRadioGps()
    g._handle_line(
        '{"seq":7,"tracker_ms":1234,"fix":1,'
        '"lat_e7":216123456,"lon_e7":-1581234567,"gps_age_ms":250,'
        '"speed_cm_s":734,"course_cdeg":9123,"hacc_cm":180,'
        '"sats":9,"batt_mv":3890,"rssi_x10":-845,"snr_x10":73,"rx":4,"lost":0}',
        now=1000.0,
    )

    fix = g.get_fix(now=1001.0)
    assert fix is not None
    assert abs(fix.lat - 21.6123456) < 1e-7
    assert abs(fix.lon - -158.1234567) < 1e-7
    assert abs(fix.speed - 7.34) < 1e-6
    assert abs(fix.course - 91.23) < 1e-6
    assert abs(fix.ts - 999.75) < 1e-6
    assert abs(fix.age_sec - 1.25) < 1e-6
    assert fix.src == "direct_lora"
    assert g.get_target_telemetry() == {
        "target_battery_mv": 3890,
        "target_sats": 9,
    }


def test_remote_no_fix_clears_subject_snapshot():
    g = DirectRadioGps()
    g._handle_line(
        '{"seq":1,"fix":1,"lat_e7":100000000,"lon_e7":200000000,'
        '"gps_age_ms":0,"speed_cm_s":0,"course_cdeg":0}',
        now=1000.0,
    )
    assert g.get_fix(now=1000.0) is not None

    g._handle_line('{"seq":2,"fix":0,"gps_age_ms":65535,"sats":4,"batt_mv":3810}', now=1001.0)
    assert g.get_fix(now=1001.0) is None
    assert g.get_target_telemetry() == {
        "target_battery_mv": 3810,
        "target_sats": 4,
    }


def test_base_line_updates_camera_position_only_when_stable():
    g = DirectRadioGps()
    g._handle_line(
        '{"base":1,"fix":1,"lat_e7":216000000,"lon_e7":-1580000000,'
        '"alt_m":12,"sats":11,"hdop_x10":13,"stable":0,"hold_s":5}',
        now=1000.0,
    )
    assert g.get_camera_position() is None
    assert g.get_camera_age(now=1000.5) is None

    g._handle_line(
        '{"base":1,"fix":1,"lat_e7":216000100,"lon_e7":-1580000100,'
        '"alt_m":13,"sats":12,"hdop_x10":10,"stable":1,"hold_s":22}',
        now=1002.0,
    )
    assert g.get_camera_position() == (21.60001, -158.00001, 13.0)
    assert g.get_camera_age(now=1004.5) == 2.5


class _ExplodingSerial:
    def readline(self):  # pragma: no cover - proves public reads avoid serial
        time.sleep(30)
        return b""

    def close(self):
        pass


def test_public_reads_are_non_blocking_and_never_touch_serial():
    g = DirectRadioGps()
    g._serial = _ExplodingSerial()
    g.enabled = True
    g._handle_line(
        '{"seq":1,"fix":1,"lat_e7":100000000,"lon_e7":200000000,'
        '"gps_age_ms":0,"speed_cm_s":0,"course_cdeg":0}',
        now=1000.0,
    )
    t0 = time.monotonic()
    for _ in range(20000):
        g.get_fix(now=1001.0)
        g.get_camera_position()
        g.get_target_telemetry()
    assert time.monotonic() - t0 < 1.0


class _FakeSerial:
    def __init__(self, lines):
        self._lines = [line.encode("utf-8") + b"\n" for line in lines]
        self.closed = False

    def readline(self):
        if self._lines:
            return self._lines.pop(0)
        time.sleep(0.01)
        return b""

    def close(self):
        self.closed = True


def test_reader_thread_populates_snapshots_and_close_stops_it():
    fake = _FakeSerial([
        '{"base":1,"fix":1,"lat_e7":216000000,"lon_e7":-1580000000,'
        '"alt_m":8,"stable":1}',
        '{"seq":3,"fix":1,"lat_e7":216123456,"lon_e7":-1581234567,'
        '"gps_age_ms":100,"speed_cm_s":250,"course_cdeg":18000}',
    ])
    g = DirectRadioGps(serial_factory=lambda *_args, **_kwargs: fake)
    assert g.connect() is True

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and (g.get_fix() is None or g.get_camera_position() is None):
        time.sleep(0.01)

    assert g.reader_alive() is True
    assert g.get_fix() is not None
    assert g.get_camera_position() == (21.6, -158.0, 8.0)
    assert g.last_poll_age_sec() is not None

    g.close()
    assert g.reader_alive() is False
    assert fake.closed is True


def test_run_selector_uses_direct_lora_source(monkeypatch):
    from run import start_gps_reader
    from wavecam.config import GpsCfg

    made = {}

    class FakeDirect:
        def __init__(self, dev_path, baud, reconnect_sec):
            made["args"] = (dev_path, baud, reconnect_sec)

        def connect(self):
            made["connected"] = True
            return True

    monkeypatch.setitem(
        sys.modules,
        "wavecam.gps_direct_lora",
        types.SimpleNamespace(DirectRadioGps=FakeDirect),
    )
    cfg = types.SimpleNamespace(
        gps=GpsCfg(
            enabled=True,
            source="direct_lora",
            direct_dev_path="/dev/serial/by-id/wio-base",
            direct_baud=115200,
            direct_reconnect_sec=0.5,
        )
    )

    gps = start_gps_reader(cfg)
    assert isinstance(gps, FakeDirect)
    assert made == {
        "args": ("/dev/serial/by-id/wio-base", 115200, 0.5),
        "connected": True,
    }


def test_concurrent_direct_lora_reads_and_writes_are_safe():
    g = DirectRadioGps()
    stop = threading.Event()

    def writer():
        seq = 0
        while not stop.is_set():
            g._handle_line(
                f'{{"seq":{seq},"fix":1,"lat_e7":216000000,'
                f'"lon_e7":-1580000000,"gps_age_ms":0,'
                f'"speed_cm_s":0,"course_cdeg":0}}',
                now=time.time(),
            )
            seq += 1

    wt = threading.Thread(target=writer, daemon=True)
    wt.start()
    errors = []

    def reader():
        try:
            for _ in range(5000):
                g.get_fix()
        except Exception as e:  # pragma: no cover
            errors.append(e)

    readers = [threading.Thread(target=reader) for _ in range(8)]
    for r in readers:
        r.start()
    for r in readers:
        r.join()
    stop.set()
    wt.join(timeout=1.0)
    assert not errors
