"""Unit tests for the Meshtastic GPS ingest (no hardware required).

Includes the regression tests for the 2026-06-08 API-hang: the public reads
(get_fix / get_camera_position) must NEVER touch the meshtastic interface, so they
stay non-blocking even when the interface itself would block.
"""
import threading
import time

from wavecam.gps_meshtastic import (
    MeshtasticGps,
    _camera_from_nodes,
    _remote_from_nodes,
    bearing_deg,
    haversine_m,
)
from wavecam.gps_stub import NormalizedFix


# --- pure geo / extraction ---------------------------------------------------

def test_haversine_one_degree_latitude():
    assert abs(haversine_m(0.0, 0.0, 1.0, 0.0) - 111_195) < 500


def test_bearing_cardinals():
    assert abs(bearing_deg(0.0, 0.0, 1.0, 0.0) - 0.0) < 1.0     # north
    assert abs(bearing_deg(0.0, 0.0, 0.0, 1.0) - 90.0) < 1.0    # east
    assert abs(bearing_deg(0.0, 0.0, -1.0, 0.0) - 180.0) < 1.0  # south


def test_remote_skips_local_and_picks_freshest():
    nodes = {
        "!base": {"num": 1, "position": {"time": 100}},
        "!rem": {"num": 2, "user": {"id": "!rem"},
                 "position": {"latitude": 21.0, "longitude": -158.0, "time": 200}},
    }
    assert _remote_from_nodes(nodes, my_num=1, remote_id=None) == ("!rem", 21.0, -158.0, 200.0)


def test_remote_respects_explicit_id():
    nodes = {
        "!a": {"num": 2, "user": {"id": "!a"}, "position": {"latitude": 1.0, "longitude": 2.0, "time": 300}},
        "!b": {"num": 3, "user": {"id": "!b"}, "position": {"latitude": 4.0, "longitude": 5.0, "time": 100}},
    }
    assert _remote_from_nodes(nodes, my_num=1, remote_id="!b")[0] == "!b"


def test_remote_none_without_fix():
    nodes = {"!rem": {"num": 2, "user": {"id": "!rem"}, "position": {"time": 200}}}
    assert _remote_from_nodes(nodes, my_num=1, remote_id=None) is None


def test_camera_from_nodes():
    nodes = {"!base": {"num": 1, "position": {"latitude": 21.6, "longitude": -158.0, "altitude": 12}}}
    assert _camera_from_nodes(nodes, my_num=1) == (21.6, -158.0, 12.0)
    assert _camera_from_nodes({"!base": {"num": 1, "position": {"time": 5}}}, my_num=1) is None  # no fix


# --- derivation (reader-thread logic, called directly) -----------------------

def test_first_fix_is_stationary():
    g = MeshtasticGps()
    f = g._to_fix(21.0, -158.0, ts=1000.0, now=1005.0)
    assert f.course == 0.0 and f.speed == 0.0
    assert (f.lat, f.lon) == (21.0, -158.0)
    assert abs(f.age_sec - 5.0) < 1e-6
    assert f.src == "lora"


def test_motion_derives_speed_and_course():
    g = MeshtasticGps(min_move_m=3.0)
    g._to_fix(0.0, 0.0, ts=1000.0, now=1000.0)
    f = g._to_fix(0.001, 0.0, ts=1010.0, now=1010.0)  # ~111 m north over 10 s
    assert abs(f.speed - 11.1) < 1.0
    assert abs(f.course - 0.0) < 2.0


def test_jitter_below_min_move_is_stationary():
    g = MeshtasticGps(min_move_m=3.0)
    g._to_fix(0.0, 0.0, ts=1000.0, now=1000.0)
    f = g._to_fix(0.000009, 0.0, ts=1005.0, now=1005.0)  # ~1 m < min_move
    assert f.speed == 0.0


def test_age_never_negative():
    g = MeshtasticGps()
    f = g._to_fix(0.0, 0.0, ts=2000.0, now=1990.0)
    assert f.age_sec == 0.0


# --- regression: the public reads must never block (the API-hang fix) --------

class _ExplodingIface:
    """An interface whose .nodes access blocks for a long time. If get_fix() or
    get_camera_position() touched it, these tests would hang — they must not."""

    @property
    def nodes(self):
        time.sleep(30)  # would hang the test if the public reads accessed it
        return {}

    def getMyNodeInfo(self):
        return {"num": 1}

    def close(self):
        pass


def test_get_fix_none_when_no_snapshot():
    assert MeshtasticGps().get_fix() is None


def test_get_fix_is_non_blocking_and_never_touches_interface():
    g = MeshtasticGps()
    g._iface = _ExplodingIface()
    g._my_num = 1
    g.enabled = True
    g._latest = NormalizedFix(lat=1.0, lon=2.0, course=0.0, speed=0.0, ts=1000.0, age_sec=0.0, src="lora")
    t0 = time.monotonic()
    for _ in range(20000):
        f = g.get_fix(now=1005.0)
    assert time.monotonic() - t0 < 1.0          # 20k calls fast => no interface access
    assert f.lat == 1.0 and abs(f.age_sec - 5.0) < 1e-6  # age refreshed from cached ts


def test_get_camera_position_is_non_blocking_and_never_touches_interface():
    g = MeshtasticGps()
    g._iface = _ExplodingIface()
    g._my_num = 1
    g.enabled = True
    g._cam = (21.0, -158.0, 5.0)
    t0 = time.monotonic()
    for _ in range(20000):
        cam = g.get_camera_position()
    assert time.monotonic() - t0 < 1.0
    assert cam == (21.0, -158.0, 5.0)


def test_concurrent_reads_and_writes_are_safe():
    g = MeshtasticGps()
    stop = threading.Event()

    def writer():
        i = 0
        while not stop.is_set():
            with g._lock:
                g._latest = NormalizedFix(lat=float(i), lon=0.0, course=0.0, speed=0.0,
                                          ts=1000.0 + i, age_sec=0.0, src="lora")
            i += 1

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


# --- reader lifecycle --------------------------------------------------------

class _FakeIface:
    def __init__(self):
        self.nodes = {
            "!base": {"num": 1, "position": {"time": 1}},
            "!rem": {"num": 2, "user": {"id": "!rem"},
                     "position": {"latitude": 21.0, "longitude": -158.0, "time": 200}},
        }
        self.closed = False

    def getMyNodeInfo(self):
        return {"num": 1}

    def close(self):
        self.closed = True


def test_reader_thread_populates_snapshot_and_close_stops_it():
    g = MeshtasticGps(poll_sec=0.02)
    fake = _FakeIface()
    g._iface = fake          # inject a fake to avoid the real meshtastic lib
    g._my_num = 1
    g.enabled = True
    g._stop.clear()
    g._thread = threading.Thread(target=g._reader_loop, name="meshtastic-gps", daemon=True)
    g._thread.start()
    time.sleep(0.1)          # let it poll a few times

    assert g._thread.is_alive()
    fix = g.get_fix()
    assert fix is not None and fix.lat == 21.0          # reader populated the snapshot
    assert g.get_camera_position() is None              # base has no fix (only a time)

    g.close()
    assert g._thread is None
    assert fake.closed is True


# --- camera age ---------------------------------------------------------------

class _FakeIfaceWithBase:
    def __init__(self):
        self.nodes = {
            "!base": {"num": 1, "position": {"latitude": 21.6, "longitude": -158.0, "altitude": 5, "time": 500}},
            "!rem": {"num": 2, "user": {"id": "!rem"},
                     "position": {"latitude": 21.0, "longitude": -158.1, "time": 600}},
        }
        self.closed = False

    def getMyNodeInfo(self):
        return {"num": 1}

    def close(self):
        self.closed = True


def test_get_camera_age_returns_none_when_no_fix():
    g = MeshtasticGps()
    assert g.get_camera_age() is None


def test_get_camera_age_returns_age_when_fix_exists():
    g = MeshtasticGps()
    g._cam = (21.6, -158.0, 5.0)
    g._cam_ts = 1000.0
    age = g.get_camera_age(now=1005.0)
    assert age == 5.0


def test_reader_populates_camera_age():
    g = MeshtasticGps(poll_sec=0.02)
    fake = _FakeIfaceWithBase()
    g._iface = fake
    g._my_num = 1
    g.enabled = True
    g._stop.clear()
    g._thread = threading.Thread(target=g._reader_loop, name="meshtastic-gps", daemon=True)
    g._thread.start()
    time.sleep(0.1)

    cam = g.get_camera_position()
    assert cam is not None and cam[0] == 21.6
    age = g.get_camera_age()
    assert age is not None and age >= 0.0

    g.close()
    assert g._thread is None
    assert fake.closed is True
    assert g.enabled is False
