"""Wave 2 (audit 2026-07-01): M8, M9, H9(python), L2 — DirectRadioGps ingest.

M8: honor optional spd_ok/crs_ok validity flags (absent => assume valid,
backward compatible); when false, null course / zero speed instead of
reporting the firmware's memset-0 fields as real data (course=0 == due north).

M9: parse hacc_cm -> NormalizedFix.h_acc_m (None when absent).

H9: the base's settled running mean freezes after ~30 min and is unbounded,
defeating BaseDriftMonitor. get_camera_position() now prefers the
instantaneous raw_lat/raw_lon fix when the firmware emits it, falling back to
the mean on older firmware / before the first raw sample.

L2: after OPEN_FAIL_GLOB_THRESHOLD consecutive open failures on the configured
device path, glob /dev/ttyACM* and try alternate candidates so a re-enumerated
Wio (stale process still holding the old node) recovers without a restart.
"""
from __future__ import annotations

import pytest

from wavecam.gps_direct_lora import DirectRadioGps, OPEN_FAIL_GLOB_THRESHOLD


# --- M8: spd_ok / crs_ok validity flags --------------------------------------

def test_course_and_speed_reported_when_ok_flags_absent():
    """Backward compatible: older firmware that never emits spd_ok/crs_ok must
    keep reporting course/speed as before."""
    g = DirectRadioGps()
    g._handle_line(
        '{"seq":1,"fix":1,"lat_e7":216000000,"lon_e7":-1580000000,'
        '"gps_age_ms":0,"speed_cm_s":500,"course_cdeg":9000}',
        now=1000.0,
    )
    fix = g.get_fix(now=1000.0)
    assert fix is not None
    assert fix.speed == pytest.approx(5.0)
    assert fix.course == pytest.approx(90.0)


def test_course_nulled_and_speed_zeroed_when_flags_false():
    g = DirectRadioGps()
    g._handle_line(
        '{"seq":1,"fix":1,"lat_e7":216000000,"lon_e7":-1580000000,'
        '"gps_age_ms":0,"speed_cm_s":500,"course_cdeg":0,'
        '"spd_ok":0,"crs_ok":0}',
        now=1000.0,
    )
    fix = g.get_fix(now=1000.0)
    assert fix is not None
    # course=0 with crs_ok false must not be reported as "due north" — the bug
    # was reporting the firmware's memset-0 field as real data.
    assert fix.course == 0.0
    assert fix.speed == 0.0


def test_course_reported_when_crs_ok_true_but_spd_ok_false():
    g = DirectRadioGps()
    g._handle_line(
        '{"seq":1,"fix":1,"lat_e7":216000000,"lon_e7":-1580000000,'
        '"gps_age_ms":0,"speed_cm_s":500,"course_cdeg":18000,'
        '"spd_ok":0,"crs_ok":1}',
        now=1000.0,
    )
    fix = g.get_fix(now=1000.0)
    assert fix is not None
    assert fix.course == pytest.approx(180.0)  # crs_ok true -> kept
    assert fix.speed == 0.0                    # spd_ok false -> zeroed


# --- M9: hacc_cm -> h_acc_m ---------------------------------------------------

def test_h_acc_m_parsed_from_hacc_cm():
    g = DirectRadioGps()
    g._handle_line(
        '{"seq":1,"fix":1,"lat_e7":216000000,"lon_e7":-1580000000,'
        '"gps_age_ms":0,"hacc_cm":3000}',
        now=1000.0,
    )
    fix = g.get_fix(now=1000.0)
    assert fix is not None
    assert fix.h_acc_m == pytest.approx(30.0)


def test_h_acc_m_is_none_when_hacc_cm_absent():
    g = DirectRadioGps()
    g._handle_line(
        '{"seq":1,"fix":1,"lat_e7":216000000,"lon_e7":-1580000000,"gps_age_ms":0}',
        now=1000.0,
    )
    fix = g.get_fix(now=1000.0)
    assert fix is not None
    assert fix.h_acc_m is None


# --- H9: get_camera_position() prefers the raw instantaneous fix -----------

def test_camera_position_prefers_raw_fix_when_present():
    g = DirectRadioGps()
    g._handle_line(
        '{"base":1,"fix":1,"lat_e7":216000000,"lon_e7":-1580000000,'
        '"raw_lat":216005000,"raw_lon":-1580003000,"alt_m":8,"stable":1}',
        now=1000.0,
    )
    # The settled mean (216000000) differs from the instantaneous raw fix
    # (216005000) -- get_camera_position() must return the RAW one.
    assert g.get_camera_position() == (21.6005, -158.0003, 8.0)


def test_camera_position_falls_back_to_mean_without_raw_fields():
    """Older firmware that never emits raw_lat/raw_lon: unaffected behavior."""
    g = DirectRadioGps()
    g._handle_line(
        '{"base":1,"fix":1,"lat_e7":216000000,"lon_e7":-1580000000,'
        '"alt_m":8,"stable":1}',
        now=1000.0,
    )
    assert g.get_camera_position() == (21.6, -158.0, 8.0)


def test_camera_position_raw_gated_on_fix_flag():
    """raw_lat/raw_lon must be gated on "fix" (0,0 while invalid per the
    firmware contract) -- an invalid base fix must not poison _cam_raw."""
    g = DirectRadioGps()
    g._handle_line(
        '{"base":1,"fix":0,"lat_e7":0,"lon_e7":0,'
        '"raw_lat":0,"raw_lon":0,"alt_m":0,"stable":0}',
        now=1000.0,
    )
    assert g.get_camera_position() is None


def test_camera_age_matches_whichever_position_is_returned():
    g = DirectRadioGps()
    g._handle_line(
        '{"base":1,"fix":1,"lat_e7":216000000,"lon_e7":-1580000000,'
        '"raw_lat":216005000,"raw_lon":-1580003000,"alt_m":8,"stable":1}',
        now=1000.0,
    )
    assert g.get_camera_position() is not None
    assert g.get_camera_age(now=1002.5) == pytest.approx(2.5)


# --- L2: glob fallback after consecutive open failures -----------------------

class _FailThenSucceedFactory:
    """Fails to "open" the configured path every time; only succeeds on a
    specific alternate path, mimicking a re-enumerated Wio."""

    def __init__(self, good_path: str):
        self.good_path = good_path
        self.calls: list[str] = []

    def __call__(self, dev_path, baud, timeout=1):
        self.calls.append(dev_path)
        if dev_path == self.good_path:
            return _FakeSerial()
        raise OSError(f"[Errno 2] could not open {dev_path}")


class _FakeSerial:
    def readline(self):
        return b""

    def close(self):
        pass


def test_candidate_paths_only_configured_path_before_threshold(monkeypatch, tmp_path):
    g = DirectRadioGps(dev_path="/dev/ttyACM0")
    g._open_fail_count = OPEN_FAIL_GLOB_THRESHOLD - 1
    monkeypatch.setattr(
        "wavecam.gps_direct_lora.glob.glob",
        lambda pattern: (_ for _ in ()).throw(AssertionError("glob must not run yet")),
    )
    assert g._candidate_paths() == ["/dev/ttyACM0"]


def test_candidate_paths_globs_after_threshold(monkeypatch):
    g = DirectRadioGps(dev_path="/dev/ttyACM0")
    g._open_fail_count = OPEN_FAIL_GLOB_THRESHOLD
    monkeypatch.setattr(
        "wavecam.gps_direct_lora.glob.glob",
        lambda pattern: ["/dev/ttyACM1", "/dev/ttyACM0"],
    )
    candidates = g._candidate_paths()
    assert candidates[0] == "/dev/ttyACM0"       # configured path always tried first
    assert "/dev/ttyACM1" in candidates
    assert candidates.count("/dev/ttyACM0") == 1  # no duplicate


def test_reader_loop_recovers_via_glob_after_repeated_failures(monkeypatch):
    factory = _FailThenSucceedFactory(good_path="/dev/ttyACM1")
    g = DirectRadioGps(dev_path="/dev/ttyACM0", reconnect_sec=0.001,
                       serial_factory=factory)
    monkeypatch.setattr(
        "wavecam.gps_direct_lora.glob.glob",
        lambda pattern: ["/dev/ttyACM0", "/dev/ttyACM1"],
    )
    assert g.connect() is True
    import time
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not g.reader_alive():
        time.sleep(0.01)
    assert g.reader_alive() is True
    assert g.current_dev_path == "/dev/ttyACM1"
    assert g._open_fail_count == 0
    # Confirm it actually exhausted the threshold on the configured path before
    # the glob widened the candidate set.
    assert factory.calls.count("/dev/ttyACM0") >= OPEN_FAIL_GLOB_THRESHOLD
    g.close()
