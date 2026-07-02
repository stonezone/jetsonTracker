"""Audit round-2 (2026-07-01), Agent B — GPS ingest: R6-B, R7, R8, R9.

R6-B: Wave 2 wrongly re-pointed the SHARED get_camera_position()/get_camera_age()
at the raw instantaneous fix, corrupting calibration base-lock and pointing (they
need the firmware's settled mean). Fix: get_camera_position()/get_camera_age()
are restored to the mean series; a new get_camera_position_raw()/
get_camera_age_raw() pair exposes the instantaneous fix for the drift monitor.

R7: hacc_cm=0 (firmware memset-0 during a brief fix/hdop invalid window) must map
to h_acc_m=None ("unknown"), not a false "perfect 0m" that defeats the h_acc gate.

R8: course=None (not 0.0/due-north) when crs_ok is false, so predict_lead() (which
already skips extrapolation on course_deg is None) doesn't lead a subject due
north on an invalid course.

R9: a glob-discovered (non-configured) /dev/ttyACM* candidate must prove itself
with >=1 parseable base/seq JSONL line within the validation window before the
reader trusts it; a silent wrong device (Arduino/Nucleo/debug probe) is rejected
and the reader keeps cycling. The configured path is never gated this way.
"""
from __future__ import annotations

import time

import pytest

from wavecam.gps_direct_lora import DirectRadioGps


# --- R6-B: get_camera_position()/get_camera_age() return the settled MEAN;
#     get_camera_position_raw()/get_camera_age_raw() return the instantaneous fix.

def test_get_camera_position_returns_mean_not_raw():
    g = DirectRadioGps()
    g._handle_line(
        '{"base":1,"fix":1,"lat_e7":216000000,"lon_e7":-1580000000,'
        '"raw_lat":216005000,"raw_lon":-1580003000,"alt_m":8,"stable":1}',
        now=1000.0,
    )
    # The settled mean (216000000) differs from the instantaneous raw fix
    # (216005000) -- get_camera_position() must return the MEAN.
    assert g.get_camera_position() == (21.6, -158.0, 8.0)


def test_get_camera_position_raw_returns_instantaneous_fix():
    g = DirectRadioGps()
    g._handle_line(
        '{"base":1,"fix":1,"lat_e7":216000000,"lon_e7":-1580000000,'
        '"raw_lat":216005000,"raw_lon":-1580003000,"alt_m":8,"stable":1}',
        now=1000.0,
    )
    assert g.get_camera_position_raw() == (21.6005, -158.0003, 8.0)


def test_get_camera_position_raw_is_fix_gated_not_stable_gated():
    """raw_lat/raw_lon updates even when "stable" is false (fix-gated only) --
    the mean (_cam) does NOT update until stable, but the raw seam should."""
    g = DirectRadioGps()
    g._handle_line(
        '{"base":1,"fix":1,"lat_e7":216000000,"lon_e7":-1580000000,'
        '"raw_lat":216005000,"raw_lon":-1580003000,"alt_m":8,"stable":0}',
        now=1000.0,
    )
    assert g.get_camera_position() is None                          # not stable yet -> mean absent
    assert g.get_camera_position_raw() == (21.6005, -158.0003, 8.0)  # raw present regardless


def test_get_camera_position_raw_none_without_fix_flag():
    g = DirectRadioGps()
    g._handle_line(
        '{"base":1,"fix":0,"lat_e7":0,"lon_e7":0,'
        '"raw_lat":0,"raw_lon":0,"alt_m":0,"stable":0}',
        now=1000.0,
    )
    assert g.get_camera_position_raw() is None


def test_get_camera_position_raw_none_before_any_base_line():
    g = DirectRadioGps()
    assert g.get_camera_position_raw() is None
    assert g.get_camera_age_raw() is None


def test_get_camera_age_matches_mean_series():
    g = DirectRadioGps()
    g._handle_line(
        '{"base":1,"fix":1,"lat_e7":216000000,"lon_e7":-1580000000,'
        '"raw_lat":216005000,"raw_lon":-1580003000,"alt_m":8,"stable":1}',
        now=1000.0,
    )
    assert g.get_camera_age(now=1002.5) == pytest.approx(2.5)


def test_get_camera_age_raw_matches_raw_series_independently():
    """The mean and raw fix can arrive/refresh at different ticks; get_camera_age()
    and get_camera_age_raw() must each report the age of their own series."""
    g = DirectRadioGps()
    g._handle_line(
        '{"base":1,"fix":1,"lat_e7":216000000,"lon_e7":-1580000000,'
        '"raw_lat":216005000,"raw_lon":-1580003000,"alt_m":8,"stable":1}',
        now=1000.0,
    )
    # A later base line updates the raw fix again but is NOT stable, so the mean
    # (_cam/_cam_ts) stays pinned at t=1000 while the raw fix refreshes at t=1005.
    g._handle_line(
        '{"base":1,"fix":1,"lat_e7":216000000,"lon_e7":-1580000000,'
        '"raw_lat":216006000,"raw_lon":-1580004000,"alt_m":8,"stable":0}',
        now=1005.0,
    )
    assert g.get_camera_age(now=1010.0) == pytest.approx(10.0)      # mean still ts=1000
    assert g.get_camera_age_raw(now=1010.0) == pytest.approx(5.0)   # raw refreshed at ts=1005


def test_get_camera_position_falls_back_to_mean_without_raw_fields_still_works():
    """Older firmware that never emits raw_lat/raw_lon: get_camera_position() is
    unaffected (this behavior predates both Wave 2 and this fix)."""
    g = DirectRadioGps()
    g._handle_line(
        '{"base":1,"fix":1,"lat_e7":216000000,"lon_e7":-1580000000,'
        '"alt_m":8,"stable":1}',
        now=1000.0,
    )
    assert g.get_camera_position() == (21.6, -158.0, 8.0)
    assert g.get_camera_position_raw() is None


# --- R7: hacc_cm=0 -> h_acc_m=None (not "perfect 0m") ------------------------

def test_h_acc_m_none_when_hacc_cm_is_zero():
    g = DirectRadioGps()
    g._handle_line(
        '{"seq":1,"fix":1,"lat_e7":216000000,"lon_e7":-1580000000,'
        '"gps_age_ms":0,"hacc_cm":0}',
        now=1000.0,
    )
    fix = g.get_fix(now=1000.0)
    assert fix is not None
    assert fix.h_acc_m is None


def test_h_acc_m_still_parsed_when_hacc_cm_positive():
    g = DirectRadioGps()
    g._handle_line(
        '{"seq":1,"fix":1,"lat_e7":216000000,"lon_e7":-1580000000,'
        '"gps_age_ms":0,"hacc_cm":250}',
        now=1000.0,
    )
    fix = g.get_fix(now=1000.0)
    assert fix is not None
    assert fix.h_acc_m == pytest.approx(2.5)


def test_h_acc_m_none_when_hacc_cm_absent():
    g = DirectRadioGps()
    g._handle_line(
        '{"seq":1,"fix":1,"lat_e7":216000000,"lon_e7":-1580000000,"gps_age_ms":0}',
        now=1000.0,
    )
    fix = g.get_fix(now=1000.0)
    assert fix is not None
    assert fix.h_acc_m is None


# --- R8: crs_ok=false -> course=None (not due-north) -------------------------

def test_course_is_none_when_crs_ok_false():
    g = DirectRadioGps()
    g._handle_line(
        '{"seq":1,"fix":1,"lat_e7":216000000,"lon_e7":-1580000000,'
        '"gps_age_ms":0,"speed_cm_s":30,"course_cdeg":0,'
        '"spd_ok":1,"crs_ok":0}',
        now=1000.0,
    )
    fix = g.get_fix(now=1000.0)
    assert fix is not None
    assert fix.course is None
    assert fix.speed == pytest.approx(0.3)  # spd_ok true -> speed still reported


def test_course_reported_when_crs_ok_true():
    g = DirectRadioGps()
    g._handle_line(
        '{"seq":1,"fix":1,"lat_e7":216000000,"lon_e7":-1580000000,'
        '"gps_age_ms":0,"speed_cm_s":500,"course_cdeg":9000,'
        '"crs_ok":1}',
        now=1000.0,
    )
    fix = g.get_fix(now=1000.0)
    assert fix is not None
    assert fix.course == pytest.approx(90.0)


def test_course_reported_when_ok_flags_absent_backward_compatible():
    g = DirectRadioGps()
    g._handle_line(
        '{"seq":1,"fix":1,"lat_e7":216000000,"lon_e7":-1580000000,'
        '"gps_age_ms":0,"speed_cm_s":500,"course_cdeg":9000}',
        now=1000.0,
    )
    fix = g.get_fix(now=1000.0)
    assert fix is not None
    assert fix.course == pytest.approx(90.0)


def test_predict_lead_does_not_extrapolate_due_north_when_course_none():
    """End-to-end: an invalid course must not get treated as due-north by
    predict_lead (it already skips extrapolation when course_deg is None)."""
    from wavecam.gps_geo import GeoPoint, predict_lead

    point = GeoPoint(lat=21.6, lon=-158.0, speed_mps=2.0, course_deg=None)
    out = predict_lead(point, 2.0)
    assert out is point  # unchanged: no course -> no extrapolation


# --- R9: glob-discovered candidates must prove themselves with real JSONL ----

class _SilentSerial:
    """Opens fine but never emits anything (mimics a wrong ACM device -- an
    Arduino/Nucleo/debug probe -- that must NOT be trusted)."""

    def __init__(self):
        self.closed = False

    def readline(self):
        return b""

    def close(self):
        self.closed = True


class _TalkativeSerial:
    """Emits a real base/seq JSONL line immediately."""

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


def test_configured_path_is_never_validation_gated():
    """The configured device path is trusted immediately (no JSONL proof
    required) even if it never emits anything -- only glob-discovered
    fallback candidates are gated."""
    serial = _SilentSerial()
    g = DirectRadioGps(
        dev_path="/dev/ttyACM0",
        serial_factory=lambda *_a, **_k: serial,
        glob_validate_sec=5.0,
    )
    assert g.connect() is True
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not g.reader_alive():
        time.sleep(0.01)
    assert g.reader_alive() is True
    assert g.current_dev_path == "/dev/ttyACM0"
    g.close()


def test_glob_candidate_rejected_when_silent_and_cycling_continues(monkeypatch):
    """A wrong device on the glob-discovered fallback path that opens but never
    emits parseable JSONL must be rejected (closed) rather than latched onto
    forever; the reader keeps retrying instead of going quiet permanently."""
    opened_silent: list[_SilentSerial] = []

    def factory(dev_path, baud, timeout=1):
        if dev_path == "/dev/ttyACM0":
            raise OSError("configured device gone")
        ser = _SilentSerial()
        opened_silent.append(ser)
        return ser

    g = DirectRadioGps(
        dev_path="/dev/ttyACM0",
        reconnect_sec=0.001,
        serial_factory=factory,
        glob_validate_sec=0.05,  # short window so the test doesn't wait 10s
    )
    g._open_fail_count = 5  # force glob discovery on the very first cycle
    monkeypatch.setattr(
        "wavecam.gps_direct_lora.glob.glob",
        lambda pattern: ["/dev/ttyACM1"],
    )
    assert g.connect() is True
    # Give the reader thread a couple of validate-and-reject cycles.
    time.sleep(0.6)
    assert g.reader_alive() is False       # never accepted the silent device
    assert g.get_camera_position() is None
    assert len(opened_silent) >= 1
    # Stop the reader before inspecting close() state -- otherwise the candidate
    # currently mid-validation hasn't been closed yet (still in-flight), which is
    # a benign race in the test, not a real leak (it gets closed as soon as its
    # validation window elapses or the reader is asked to stop).
    g.close()
    assert all(s.closed for s in opened_silent)  # rejected candidates are closed


def test_glob_candidate_accepted_once_it_emits_parseable_jsonl(monkeypatch):
    """A glob-discovered candidate that DOES emit a real base/seq line within
    the validation window is trusted (the happy-path recovery L2 added)."""
    talkative = _TalkativeSerial([
        '{"base":1,"fix":1,"lat_e7":216000000,"lon_e7":-1580000000,'
        '"alt_m":8,"stable":1}',
    ])

    def factory(dev_path, baud, timeout=1):
        if dev_path == "/dev/ttyACM0":
            raise OSError("configured device gone")
        return talkative

    g = DirectRadioGps(
        dev_path="/dev/ttyACM0",
        reconnect_sec=0.001,
        serial_factory=factory,
        glob_validate_sec=2.0,
    )
    g._open_fail_count = 5
    monkeypatch.setattr(
        "wavecam.gps_direct_lora.glob.glob",
        lambda pattern: ["/dev/ttyACM1"],
    )
    assert g.connect() is True
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not g.reader_alive():
        time.sleep(0.01)
    assert g.reader_alive() is True
    assert g.current_dev_path == "/dev/ttyACM1"
    # The validating line itself was fed through the normal handler, so the
    # camera position is already populated -- not wasted.
    assert g.get_camera_position() == (21.6, -158.0, 8.0)
    g.close()
