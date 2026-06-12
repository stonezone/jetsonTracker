"""T4.2 tests for the offline shadow scorer.

All fixtures are synthetic — no hardware, no rig. The tests:
1. Verify the scorer computes position error correctly against a known offset.
2. Verify divergence event detection works for a planted divergence.
3. Verify the scorer handles edge cases gracefully (empty files, no overlap).
"""
from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import pytest

# The scorer lives in orin/wavecam/tools/score_shadow.py (not inside the package).
# Import directly via path manipulation (same approach as tools/sim/).
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from score_shadow import score, _latlon_to_en

# ── Fixtures helpers ─────────────────────────────────────────────────────────

_BASE_LAT = 21.601
_BASE_LON = -158.001

_EARTH_RADIUS_M = 6_371_000.0


def _en_to_latlon(e: float, n: float, base_lat: float, base_lon: float) -> tuple[float, float]:
    """Inverse of _latlon_to_en for fixture generation."""
    lat = base_lat + math.degrees(n / _EARTH_RADIUS_M)
    lon = base_lon + math.degrees(e / (_EARTH_RADIUS_M * math.cos(math.radians(base_lat))))
    return lat, lon


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def _make_straight_track(
    t0: float,
    duration_s: float,
    speed_e: float = 5.0,
    speed_n: float = 2.0,
    dt: float = 1.0,
) -> tuple[list[dict], list[dict]]:
    """Generate a straight-line watch track + a matching shadow JSONL.

    The shadow estimator position equals the watch position exactly, so the
    expected p50 error is 0.  The caller can add an offset to the shadow to
    test non-zero error.
    """
    watch_gps = []
    shadow = []
    t = t0
    while t <= t0 + duration_s:
        e = speed_e * (t - t0)
        n = speed_n * (t - t0)
        lat, lon = _en_to_latlon(e, n, _BASE_LAT, _BASE_LON)
        watch_gps.append({
            "kind": "gps",
            "timestamp": t,
            "lat": lat,
            "lon": lon,
            "h_acc": 3.0,
            "speed": math.sqrt(speed_e ** 2 + speed_n ** 2),
            "course": math.degrees(math.atan2(speed_e, speed_n)) % 360,
        })
        shadow.append({
            "t": t,
            "e": round(e, 3),
            "n": round(n, 3),
            "ve": speed_e,
            "vn": speed_n,
            "bearing_deg": 45.0,
            "dist_m": math.sqrt(e ** 2 + n ** 2),
            "pan_enc_would": 0,
            "bearing_std_deg": 10.0,  # not well-constrained → no divergence
        })
        t += dt
    return watch_gps, shadow


# ── Tests ────────────────────────────────────────────────────────────────────

class TestCoordinateConversion:
    def test_round_trip(self):
        """_latlon_to_en then inverse should recover original E/N within 1 mm."""
        for e0, n0 in [(0, 0), (100, -50), (-200, 300), (0, 150)]:
            lat, lon = _en_to_latlon(e0, n0, _BASE_LAT, _BASE_LON)
            e1, n1 = _latlon_to_en(lat, lon, _BASE_LAT, _BASE_LON)
            assert abs(e1 - e0) < 0.001, f"East mismatch: {e1:.4f} vs {e0}"
            assert abs(n1 - n0) < 0.001, f"North mismatch: {n1:.4f} vs {n0}"


class TestScorerBasic:
    def test_zero_offset_p50(self, tmp_path):
        """When shadow == watch, p50 error should be < 0.1 m (floating-point only)."""
        t0 = 1_700_000_000.0
        watch_gps, shadow = _make_straight_track(t0, duration_s=30.0)

        shadow_path = tmp_path / "shadow.jsonl"
        watch_path = tmp_path / "watch.jsonl"
        _write_jsonl(shadow_path, shadow)
        _write_jsonl(watch_path, watch_gps)

        result = score(shadow_path, watch_path, base_lat=_BASE_LAT, base_lon=_BASE_LON)
        assert "error" not in result, result.get("error")
        s = result["summary"]
        assert s["p50_error_m"] < 0.1, f"p50 error too high for zero offset: {s['p50_error_m']}"
        assert s["scored_seconds"] > 25

    def test_known_offset_p50(self, tmp_path):
        """When shadow has a constant +10 m east offset, p50 should be ~10 m (±1 m)."""
        t0 = 1_700_000_000.0
        watch_gps, shadow = _make_straight_track(t0, duration_s=30.0)

        # Plant a known 10 m east offset in the shadow
        east_offset = 10.0
        for r in shadow:
            r["e"] = round(r["e"] + east_offset, 3)

        shadow_path = tmp_path / "shadow.jsonl"
        watch_path = tmp_path / "watch.jsonl"
        _write_jsonl(shadow_path, shadow)
        _write_jsonl(watch_path, watch_gps)

        result = score(shadow_path, watch_path, base_lat=_BASE_LAT, base_lon=_BASE_LON)
        assert "error" not in result
        s = result["summary"]
        assert abs(s["p50_error_m"] - east_offset) < 1.0, \
            f"p50 {s['p50_error_m']:.2f} m not within 1 m of planted offset {east_offset} m"

    def test_known_2d_offset(self, tmp_path):
        """15 m east + 20 m north offset → p50 ≈ 25 m (±2 m)."""
        t0 = 1_700_000_000.0
        watch_gps, shadow = _make_straight_track(t0, duration_s=30.0)

        e_off, n_off = 15.0, 20.0
        expected = math.sqrt(e_off ** 2 + n_off ** 2)  # 25 m
        for r in shadow:
            r["e"] = round(r["e"] + e_off, 3)
            r["n"] = round(r["n"] + n_off, 3)

        shadow_path = tmp_path / "shadow.jsonl"
        watch_path = tmp_path / "watch.jsonl"
        _write_jsonl(shadow_path, shadow)
        _write_jsonl(watch_path, watch_gps)

        result = score(shadow_path, watch_path, base_lat=_BASE_LAT, base_lon=_BASE_LON)
        assert "error" not in result
        s = result["summary"]
        assert abs(s["p50_error_m"] - expected) < 2.0, \
            f"p50 {s['p50_error_m']:.2f} m not within 2 m of expected {expected:.1f} m"


class TestDivergenceDetection:
    def test_planted_divergence_event_counted(self, tmp_path):
        """A shadow record with bearing_std < 3° and |bearing error| > threshold
        should be counted as a divergence event."""
        t0 = 1_700_000_000.0
        watch_gps, shadow = _make_straight_track(t0, duration_s=30.0)

        # Watch track moves north (bearing ≈ 0°).
        # Plant one shadow record with bearing_std < 3° and est_bearing 20° off.
        # Pick middle of track so it's within the watch coverage window.
        mid = len(shadow) // 2
        shadow[mid]["bearing_std_deg"] = 1.5          # well-constrained
        shadow[mid]["bearing_deg"] = 180.0            # pointing south — wrong by ~180°

        shadow_path = tmp_path / "shadow.jsonl"
        watch_path = tmp_path / "watch.jsonl"
        _write_jsonl(shadow_path, shadow)
        _write_jsonl(watch_path, watch_gps)

        result = score(shadow_path, watch_path, base_lat=_BASE_LAT, base_lon=_BASE_LON,
                       bearing_err_threshold=10.0)
        assert "error" not in result
        assert result["summary"]["divergence_events"] >= 1, \
            "Expected at least 1 divergence event for the planted record"

    def test_no_divergence_when_well_constrained_and_accurate(self, tmp_path):
        """When bearing_std < 3° and each shadow bearing matches the watch bearing, no divergence."""
        t0 = 1_700_000_000.0
        # Use a fast track so the subject moves well away from base, giving stable bearings.
        speed_e, speed_n = 10.0, 30.0  # mostly north → bearing ~18°
        watch_gps, shadow = _make_straight_track(t0, duration_s=30.0,
                                                  speed_e=speed_e, speed_n=speed_n)

        from wavecam.gps_geo import bearing_deg

        # For each shadow record, compute the exact bearing from base to the
        # corresponding watch position so the scorer sees zero bearing error.
        t_min = watch_gps[0]["timestamp"]
        for r in shadow:
            dt = r["t"] - t_min
            e = speed_e * dt
            n = speed_n * dt
            lat, lon = _en_to_latlon(e, n, _BASE_LAT, _BASE_LON)
            exact_bearing = bearing_deg(_BASE_LAT, _BASE_LON, lat, lon)
            r["bearing_std_deg"] = 1.0
            r["bearing_deg"] = exact_bearing

        shadow_path = tmp_path / "shadow.jsonl"
        watch_path = tmp_path / "watch.jsonl"
        _write_jsonl(shadow_path, shadow)
        _write_jsonl(watch_path, watch_gps)

        result = score(shadow_path, watch_path, base_lat=_BASE_LAT, base_lon=_BASE_LON,
                       bearing_err_threshold=10.0)
        assert "error" not in result
        assert result["summary"]["divergence_events"] == 0, \
            f"Unexpected divergence events: {result['summary']['divergence_events']}"


class TestEdgeCases:
    def test_no_overlap(self, tmp_path):
        """Shadow time range before watch → error result."""
        t0 = 1_700_000_000.0
        watch_gps, shadow = _make_straight_track(t0, duration_s=10.0)

        # Offset watch to start 1 hour later
        for r in watch_gps:
            r["timestamp"] += 3600

        shadow_path = tmp_path / "shadow.jsonl"
        watch_path = tmp_path / "watch.jsonl"
        _write_jsonl(shadow_path, shadow)
        _write_jsonl(watch_path, watch_gps)

        result = score(shadow_path, watch_path, base_lat=_BASE_LAT, base_lon=_BASE_LON)
        assert "error" in result

    def test_empty_shadow(self, tmp_path):
        shadow_path = tmp_path / "shadow.jsonl"
        watch_path = tmp_path / "watch.jsonl"
        shadow_path.write_text("")
        watch_path.write_text('{"kind": "gps", "timestamp": 1.0, "lat": 21.601, "lon": -158.001, "h_acc": 3, "speed": 1, "course": 0}\n')
        result = score(shadow_path, watch_path, base_lat=_BASE_LAT, base_lon=_BASE_LON)
        assert "error" in result

    def test_csv_written(self, tmp_path):
        """Scorer writes a .scored.csv beside the shadow input."""
        from score_shadow import _write_csv
        shadow_path = tmp_path / "shadow.jsonl"
        rows = [{"t": 1.0, "est_e": 0.0, "est_n": 0.0, "watch_e": 0.0, "watch_n": 0.0,
                 "error_m": 0.0, "divergence": 0}]
        csv_path = _write_csv(rows, shadow_path)
        assert csv_path.exists()
        content = csv_path.read_text()
        assert "error_m" in content
