#!/usr/bin/env python3
"""Offline replay scorer — T4.2.

Aligns a shadow estimator JSONL against a watch GPS track JSONL and reports
per-second position error, aggregate stats, and divergence events.

Shadow JSONL record format (fields used):
  {"t": <unix_s>, "e": <east_m>, "n": <north_m>,
   "pan_enc_would": <enc>, "bearing_std_deg": <deg>, ...}

Watch JSONL record format (one record per line, "kind" tag):
  GPS:    {"kind": "gps", "timestamp": <unix_s>, "lat": <deg>, "lon": <deg>,
            "h_acc": <m>, "speed": <m/s>, "course": <deg>}
  Motion: {"kind": "motion", ...}  (skipped by scorer)

Usage:
  python3 score_shadow.py shadow_session.jsonl watch_track.jsonl \\
      [--base-lat LAT] [--base-lon LON] [--bearing-err-threshold DEG]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Iterator, Optional

# Reuse the project's geographic helpers.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wavecam.gps_geo import bearing_deg as _bearing_deg, haversine_m

# ── Constants ────────────────────────────────────────────────────────────────

_EARTH_RADIUS_M = 6_371_000.0

# Divergence criterion: |would-point bearing error| > threshold while bearing_std low
_DEFAULT_BEARING_ERR_THRESHOLD_DEG = 10.0
_BEARING_STD_WELL_CONSTRAINED_DEG  = 3.0


# ── I/O helpers ──────────────────────────────────────────────────────────────

def _iter_jsonl(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def _load_shadow(path: Path) -> list[dict]:
    """Load shadow records sorted by t. Must have 't', 'e', 'n'."""
    records = [r for r in _iter_jsonl(path) if "t" in r and "e" in r and "n" in r]
    records.sort(key=lambda r: r["t"])
    return records


def _load_watch_gps(path: Path) -> list[dict]:
    """Load only GPS records from watch JSONL, sorted by timestamp."""
    records = [r for r in _iter_jsonl(path)
               if r.get("kind") == "gps"
               and "timestamp" in r and "lat" in r and "lon" in r]
    records.sort(key=lambda r: r["timestamp"])
    return records


# ── Coordinate helpers ───────────────────────────────────────────────────────

def _latlon_to_en(lat: float, lon: float, base_lat: float, base_lon: float) -> tuple[float, float]:
    """Convert lat/lon to local East-North metres around a base point.

    Uses a flat-earth approximation accurate to < 1 m over < 1 km.
    """
    dlat = math.radians(lat - base_lat)
    dlon = math.radians(lon - base_lon)
    north = dlat * _EARTH_RADIUS_M
    east  = dlon * _EARTH_RADIUS_M * math.cos(math.radians(base_lat))
    return east, north


def _interpolate_watch(watch_gps: list[dict], t: float) -> Optional[tuple[float, float]]:
    """Linearly interpolate watch EN position at unix time t.

    Returns (east, north) in metres, or None when t is outside the track window
    or the track is empty.
    """
    if not watch_gps:
        return None
    ts = [r["timestamp"] for r in watch_gps]
    if t < ts[0] or t > ts[-1]:
        return None

    # Binary search for surrounding pair
    lo, hi = 0, len(watch_gps) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if ts[mid] <= t:
            lo = mid
        else:
            hi = mid

    r0, r1 = watch_gps[lo], watch_gps[hi]
    dt = r1["timestamp"] - r0["timestamp"]
    if dt < 1e-6:
        return None
    alpha = (t - r0["timestamp"]) / dt

    e0, n0 = r0["_e"], r0["_n"]
    e1, n1 = r1["_e"], r1["_n"]
    return e0 + alpha * (e1 - e0), n0 + alpha * (n1 - n0)


# ── Scoring core ─────────────────────────────────────────────────────────────

def score(shadow_path: Path,
          watch_path: Path,
          base_lat: Optional[float],
          base_lon: Optional[float],
          bearing_err_threshold: float = _DEFAULT_BEARING_ERR_THRESHOLD_DEG,
          ) -> dict:
    """Align shadow and watch, compute per-second errors, return summary + rows."""

    shadow = _load_shadow(shadow_path)
    watch_gps = _load_watch_gps(watch_path)

    if not shadow:
        return {"error": "shadow file empty or no valid records"}
    if not watch_gps:
        return {"error": "watch file empty or no gps records"}

    # Derive base from first watch GPS fix if not supplied
    if base_lat is None:
        base_lat = watch_gps[0]["lat"]
    if base_lon is None:
        base_lon = watch_gps[0]["lon"]

    # Pre-compute EN for all watch fixes
    for r in watch_gps:
        e, n = _latlon_to_en(r["lat"], r["lon"], base_lat, base_lon)
        r["_e"] = e
        r["_n"] = n

    # Time-align: use shadow timestamps; skip where watch doesn't cover
    rows: list[dict] = []
    errors: list[float] = []
    divergence_events = 0
    estimator_present_count = 0

    watch_ts_min = watch_gps[0]["timestamp"]
    watch_ts_max = watch_gps[-1]["timestamp"]
    session_duration = shadow[-1]["t"] - shadow[0]["t"] if len(shadow) > 1 else 0.0

    for rec in shadow:
        t = rec["t"]
        est_e = rec["e"]
        est_n = rec["n"]

        watch_pos = _interpolate_watch(watch_gps, t)
        if watch_pos is None:
            continue  # outside watch coverage window

        estimator_present_count += 1
        w_e, w_n = watch_pos
        err = math.sqrt((est_e - w_e) ** 2 + (est_n - w_n) ** 2)
        errors.append(err)

        # Divergence: would-point bearing vs watch bearing, only when well-constrained
        bearing_std = rec.get("bearing_std_deg", float("inf"))
        pan_enc_would = rec.get("pan_enc_would")
        diverge = False
        if bearing_std < _BEARING_STD_WELL_CONSTRAINED_DEG and pan_enc_would is not None:
            # Derive what bearing the watch track implies (base→watch)
            watch_bearing = _bearing_deg(base_lat, base_lon,
                                         watch_gps[0]["lat"], watch_gps[0]["lon"])
            # Better: use interpolated lat/lon if we stored them
            # Compute lat/lon back from EN for the interpolated watch position
            w_lat = base_lat + math.degrees(w_n / _EARTH_RADIUS_M)
            w_lon = base_lon + math.degrees(w_e / (_EARTH_RADIUS_M * math.cos(math.radians(base_lat))))
            watch_bearing = _bearing_deg(base_lat, base_lon, w_lat, w_lon)
            est_bearing = rec.get("bearing_deg", float("nan"))
            if not math.isnan(est_bearing):
                bearing_err = abs(((est_bearing - watch_bearing) + 180) % 360 - 180)
                if bearing_err > bearing_err_threshold:
                    diverge = True
                    divergence_events += 1

        rows.append({
            "t": round(t, 2),
            "est_e": round(est_e, 2),
            "est_n": round(est_n, 2),
            "watch_e": round(w_e, 2),
            "watch_n": round(w_n, 2),
            "error_m": round(err, 2),
            "divergence": int(diverge),
        })

    if not errors:
        return {"error": "no overlapping timestamps between shadow and watch track"}

    errors_sorted = sorted(errors)
    n = len(errors_sorted)
    p50 = errors_sorted[n // 2]
    p90 = errors_sorted[min(int(n * 0.9), n - 1)]
    max_err = errors_sorted[-1]
    fraction_available = estimator_present_count / max(len(shadow), 1)

    return {
        "summary": {
            "shadow_records": len(shadow),
            "watch_gps_records": len(watch_gps),
            "scored_seconds": n,
            "session_duration_s": round(session_duration, 1),
            "fraction_estimator_available": round(fraction_available, 3),
            "p50_error_m": round(p50, 2),
            "p90_error_m": round(p90, 2),
            "max_error_m": round(max_err, 2),
            "divergence_events": divergence_events,
            "base_lat": base_lat,
            "base_lon": base_lon,
        },
        "rows": rows,
    }


def _print_table(summary: dict) -> None:
    s = summary
    print()
    print("── WaveCam Shadow Scorer ───────────────────────────────")
    print(f"  Shadow records:         {s['shadow_records']}")
    print(f"  Watch GPS records:      {s['watch_gps_records']}")
    print(f"  Scored seconds:         {s['scored_seconds']}")
    print(f"  Session duration:       {s['session_duration_s']} s")
    print(f"  Estimator fraction:     {s['fraction_estimator_available']:.1%}")
    print(f"  p50 position error:     {s['p50_error_m']:.1f} m")
    print(f"  p90 position error:     {s['p90_error_m']:.1f} m")
    print(f"  Max position error:     {s['max_error_m']:.1f} m")
    print(f"  Divergence events:      {s['divergence_events']}")
    print("────────────────────────────────────────────────────────")
    print()


def _write_csv(rows: list[dict], shadow_path: Path) -> Path:
    out = shadow_path.with_suffix(".scored.csv")
    if not rows:
        return out
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return out


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score estimator shadow JSONL against a watch GPS track."
    )
    parser.add_argument("shadow_jsonl", type=Path, help="Shadow session JSONL from the rig")
    parser.add_argument("watch_jsonl", type=Path, help="Watch session JSONL from WaveCamWatch")
    parser.add_argument("--base-lat", type=float, default=None,
                        help="Camera base latitude (defaults to first watch fix)")
    parser.add_argument("--base-lon", type=float, default=None,
                        help="Camera base longitude (defaults to first watch fix)")
    parser.add_argument("--bearing-err-threshold", type=float,
                        default=_DEFAULT_BEARING_ERR_THRESHOLD_DEG,
                        help="Bearing error threshold for divergence events (deg, default 10)")
    args = parser.parse_args()

    result = score(
        shadow_path=args.shadow_jsonl,
        watch_path=args.watch_jsonl,
        base_lat=args.base_lat,
        base_lon=args.base_lon,
        bearing_err_threshold=args.bearing_err_threshold,
    )

    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)

    _print_table(result["summary"])
    csv_path = _write_csv(result["rows"], args.shadow_jsonl)
    print(f"CSV written to: {csv_path}")


if __name__ == "__main__":
    main()
