"""Unit tests for the Meshtastic GPS ingest logic (no hardware required)."""
from wavecam.gps_meshtastic import (
    MeshtasticGps,
    _remote_from_nodes,
    bearing_deg,
    haversine_m,
)


def test_haversine_one_degree_latitude():
    # 1 degree of latitude ~= 111.2 km
    assert abs(haversine_m(0.0, 0.0, 1.0, 0.0) - 111_195) < 500


def test_bearing_cardinals():
    assert abs(bearing_deg(0.0, 0.0, 1.0, 0.0) - 0.0) < 1.0     # north
    assert abs(bearing_deg(0.0, 0.0, 0.0, 1.0) - 90.0) < 1.0    # east
    assert abs(bearing_deg(0.0, 0.0, -1.0, 0.0) - 180.0) < 1.0  # south


def test_remote_skips_local_and_picks_freshest():
    nodes = {
        "!base": {"num": 1, "position": {"time": 100}},  # local, no fix
        "!rem": {"num": 2, "user": {"id": "!rem"},
                 "position": {"latitude": 21.0, "longitude": -158.0, "time": 200}},
    }
    assert _remote_from_nodes(nodes, my_num=1, remote_id=None) == ("!rem", 21.0, -158.0, 200.0)


def test_remote_respects_explicit_id():
    nodes = {
        "!a": {"num": 2, "user": {"id": "!a"},
               "position": {"latitude": 1.0, "longitude": 2.0, "time": 300}},
        "!b": {"num": 3, "user": {"id": "!b"},
               "position": {"latitude": 4.0, "longitude": 5.0, "time": 100}},
    }
    assert _remote_from_nodes(nodes, my_num=1, remote_id="!b")[0] == "!b"


def test_remote_none_without_fix():
    nodes = {"!rem": {"num": 2, "user": {"id": "!rem"}, "position": {"time": 200}}}
    assert _remote_from_nodes(nodes, my_num=1, remote_id=None) is None


def test_first_fix_is_stationary():
    g = MeshtasticGps()
    f = g._to_fix(21.0, -158.0, ts=1000.0, now=1005.0)
    assert f.course == 0.0 and f.speed == 0.0
    assert (f.lat, f.lon) == (21.0, -158.0)
    assert abs(f.age_sec - 5.0) < 1e-6
    assert f.src == "lora"


def test_motion_derives_speed_and_course():
    g = MeshtasticGps(min_move_m=3.0)
    g._to_fix(0.0, 0.0, ts=1000.0, now=1000.0)            # seed
    f = g._to_fix(0.001, 0.0, ts=1010.0, now=1010.0)      # ~111 m north over 10 s
    assert abs(f.speed - 11.1) < 1.0
    assert abs(f.course - 0.0) < 2.0


def test_jitter_below_min_move_is_stationary():
    g = MeshtasticGps(min_move_m=3.0)
    g._to_fix(0.0, 0.0, ts=1000.0, now=1000.0)
    f = g._to_fix(0.000009, 0.0, ts=1005.0, now=1005.0)   # ~1 m < min_move
    assert f.speed == 0.0


def test_age_never_negative():
    g = MeshtasticGps()
    f = g._to_fix(0.0, 0.0, ts=2000.0, now=1990.0)  # clock skew / future ts
    assert f.age_sec == 0.0


def test_get_fix_none_when_disabled():
    assert MeshtasticGps().get_fix() is None  # not connected
