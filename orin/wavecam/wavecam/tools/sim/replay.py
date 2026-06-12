"""Feed a scenario through the estimator and score the output.

replay_scenario() returns a list of (t, output, ground_truth_bearing) tuples.
score_scenario() computes summary statistics from that list.

CLI usage:
  # Run a named synthetic scenario and print bearing-error scores:
  python3 -m wavecam.tools.sim.replay --scenario straight_run
  python3 -m wavecam.tools.sim.replay --scenario bottom_turn

  # Replay a real recorded session JSONL:
  python3 -m wavecam.tools.sim.replay /data/shadow/session_<ts>.jsonl

Adaptation note (Task 6): the plan placed tools/sim/ at orin/wavecam/tools/sim/.
To satisfy the import path 'wavecam.tools.sim' used in the test file, the
directory was placed inside the wavecam package at wavecam/tools/sim/ instead.
The --scenario CLI flag was added beyond the plan's spec to satisfy the
"run end-to-end and capture scoring output" proof requirement.
"""
from __future__ import annotations

import json
import math
import sys
import types
from typing import List, Optional, Tuple

from wavecam.estimator import TargetEstimator, range_from_bbox_height, EstimatorOutput
from wavecam.gps_geo import bearing_deg as _bearing_deg, haversine_m


_BASE_LAT = 21.601
_BASE_LON = -158.001


def _default_pose():
    class _P:
        lat = _BASE_LAT; lon = _BASE_LON; alt_m = 0.0
        has_base = True; calibrated = True
        pan_anchor_enc = 0.0; pan_anchor_bearing = 247.0; pan_enc_per_deg = 4.47
        tilt_anchor_enc = 0.0; tilt_anchor_elev = 0.0; tilt_enc_per_deg = 4.0
        def bearing_to_pan_encoder(self, b):
            return self.pan_anchor_enc + (b - self.pan_anchor_bearing) * self.pan_enc_per_deg
        def pan_encoder_to_bearing(self, enc):
            return self.pan_anchor_bearing + (enc - self.pan_anchor_enc) / self.pan_enc_per_deg
        def elevation_to_tilt_encoder(self, e):
            return self.tilt_anchor_enc + e * self.tilt_enc_per_deg
    return _P()


def _default_cfg(use_vision_range: bool = False,
                 subject_height_m: float = 1.0,
                 r_range_frac: float = 0.3):
    return types.SimpleNamespace(
        shadow=True, enabled=True, q_accel=2.0,
        p0_pos=25.0, p0_vel=9.0,
        r_gps_fresh=4.0, r_gps_age_scale=0.5, r_vis_deg=1.0,
        zoom_cov_wide_deg=4.0, zoom_cov_narrow_deg=1.5, log_every_n=1,
        use_vision_range=use_vision_range,
        subject_height_m=subject_height_m,
        r_range_frac=r_range_frac,
    )


def _default_fov():
    return [(0, 60.0), (8192, 12.0), (16384, 5.0)]


def replay_scenario(fixes, detections, pose=None, cfg=None, fov_curve=None,
                    range_detections=None):
    """Feed fixes and detections through the estimator in time order.

    Args:
        fixes: list of SimFix-like objects with GPS position observations.
        detections: list of SimDetection-like objects with vision bearing obs.
        pose: camera pose (default: _default_pose()).
        cfg: estimator config namespace (default: _default_cfg()).
        fov_curve: FOV calibration curve (default: _default_fov()).
        range_detections: optional list of SimRangeDetection-like objects with
            (.t, .bbox_h_px, .frame_h, .zoom_enc). When provided and
            cfg.use_vision_range is True, range observations are fused.

    Returns list of dicts: {t, output: EstimatorOutput, truth_bearing_deg,
        truth_dist_m, range_obs_m, range_r}.
    """
    pose = pose or _default_pose()
    cfg = cfg or _default_cfg()
    fov_curve = fov_curve or _default_fov()
    gps_cfg = types.SimpleNamespace(stale_threshold_sec=10.0)

    est = TargetEstimator(cfg=cfg, gps_cfg=gps_cfg, pose=pose, fov_curve=fov_curve)
    results = []

    use_range = bool(getattr(cfg, "use_vision_range", False))
    range_events = []
    if range_detections and use_range:
        range_events = [(d.t, "range", d) for d in range_detections]

    # Merge and sort by time
    events = [(f.t, "gps", f) for f in fixes] + \
             [(d.t, "vis", d) for d in detections] + \
             range_events
    events.sort(key=lambda x: x[0])

    for t, kind, ev in events:
        if kind == "gps":
            est.update_gps(ev, now=t)
            out = est.predict_output(now=t)
            truth_bearing = _bearing_deg(pose.lat, pose.lon, ev.lat, ev.lon)
            truth_dist = haversine_m(pose.lat, pose.lon, ev.lat, ev.lon)
            results.append({
                "t": t, "output": out,
                "truth_bearing_deg": truth_bearing,
                "truth_dist_m": truth_dist,
                "range_obs_m": None,
                "range_r": None,
            })
        elif kind == "vis":
            est.update_vision(pan_enc=ev.pan_enc, pixel_cx=ev.pixel_cx,
                              frame_w=ev.frame_w, zoom_enc=ev.zoom_enc, now=t)
        elif kind == "range":
            # Log the SAME observation the estimator fuses — shared function,
            # so the harness can never diverge from production geometry.
            range_obs_m = range_from_bbox_height(
                fov_curve, ev.zoom_enc, ev.bbox_h_px, ev.frame_h,
                float(getattr(cfg, "subject_height_m", 1.0)))
            r_frac = float(getattr(cfg, "r_range_frac", 0.3))
            range_r = r_frac * range_obs_m if range_obs_m is not None else None

            est.update_vision_range(
                bbox_h_px=ev.bbox_h_px, frame_h=ev.frame_h,
                zoom_enc=ev.zoom_enc, now=t,
            )
            # Attach range_obs_m / range_r to the most-recent GPS result entry
            if results and range_obs_m is not None:
                results[-1]["range_obs_m"] = round(range_obs_m, 2)
                results[-1]["range_r"] = round(range_r, 3) if range_r else None

    return results


def score_scenario(results, fixes, warmup_sec: float = 5.0):
    """Compute bearing error statistics, excluding the warmup period."""
    t0 = fixes[0].t if fixes else 0.0
    errors = []
    for r in results:
        if r["t"] < t0 + warmup_sec:
            continue
        out = r["output"]
        if out is None:
            continue
        err = abs(((out.bearing_deg - r["truth_bearing_deg"]) + 180) % 360 - 180)
        errors.append(err)

    if not errors:
        return {"mean_bearing_error_deg": None, "max_bearing_error_deg": None, "n": 0}
    return {
        "mean_bearing_error_deg": sum(errors) / len(errors),
        "max_bearing_error_deg": max(errors),
        "n": len(errors),
    }


_SCENARIOS = {
    "straight_run": lambda: __import__("wavecam.tools.sim.scenarios", fromlist=["straight_run"]).straight_run(),
    "bottom_turn": lambda: __import__("wavecam.tools.sim.scenarios", fromlist=["bottom_turn"]).bottom_turn(),
    "gps_dropout": lambda: __import__("wavecam.tools.sim.scenarios", fromlist=["gps_dropout"]).gps_dropout(),
    "vision_dropout": lambda: __import__("wavecam.tools.sim.scenarios", fromlist=["vision_dropout"]).vision_dropout(),
    "combined_dropout": lambda: __import__("wavecam.tools.sim.scenarios", fromlist=["combined_dropout"]).combined_dropout(),
    "range_obs": lambda: __import__("wavecam.tools.sim.scenarios", fromlist=["range_obs_scenario"]).range_obs_scenario(),
}


def run_range_comparison() -> dict:
    """Run the range-obs scenario with range on vs off and return comparison stats."""
    from wavecam.tools.sim.scenarios import range_obs_scenario
    fixes, range_dets = range_obs_scenario(
        speed_mps=8.0, start_dist_m=80.0, duration_sec=30.0, dt_gps=2.0, dt_vis=1.0,
    )

    # Range OFF
    cfg_off = _default_cfg(use_vision_range=False)
    results_off = replay_scenario(fixes, [], cfg=cfg_off, range_detections=None)
    score_off = score_scenario(results_off, fixes, warmup_sec=5.0)

    # Range ON — feed the synthetic range detections
    cfg_on = _default_cfg(use_vision_range=True, subject_height_m=1.0, r_range_frac=0.3)
    results_on = replay_scenario(fixes, [], cfg=cfg_on, range_detections=range_dets)
    score_on = score_scenario(results_on, fixes, warmup_sec=5.0)

    # Radial covariance (P_ee + P_nn trace) at final step — range should tighten it
    def _final_cov(results):
        for r in reversed(results):
            out = r.get("output")
            if out is not None:
                cov = out.cov
                return cov[0][0] + cov[1][1]
        return None

    return {
        "range_off": score_off,
        "range_on": score_on,
        "final_radial_cov_off": _final_cov(results_off),
        "final_radial_cov_on": _final_cov(results_on),
    }


def _run_scenario_cli(name: str) -> None:
    from wavecam.tools.sim import scenarios as _sc
    if name == "range_obs_comparison":
        stats = run_range_comparison()
        print("Scenario: range_obs — range-on vs range-off comparison")
        print(f"  Range OFF: mean={stats['range_off']['mean_bearing_error_deg']:.2f}°  "
              f"max={stats['range_off']['max_bearing_error_deg']:.2f}°  "
              f"n={stats['range_off']['n']}")
        print(f"  Range ON : mean={stats['range_on']['mean_bearing_error_deg']:.2f}°  "
              f"max={stats['range_on']['max_bearing_error_deg']:.2f}°  "
              f"n={stats['range_on']['n']}")
        print(f"  Final radial cov (P_ee+P_nn): OFF={stats['final_radial_cov_off']:.3f}  "
              f"ON={stats['final_radial_cov_on']:.3f}")
        return
    generator = getattr(_sc, name, None)
    if generator is None:
        print(f"Unknown scenario '{name}'. Available: {', '.join(_SCENARIOS)}, range_obs_comparison",
              file=sys.stderr)
        sys.exit(1)
    fixes, detections = generator()
    results = replay_scenario(fixes, detections)
    score = score_scenario(results, fixes, warmup_sec=5.0)
    print(f"Scenario: {name}")
    print(f"  GPS fixes: {len(fixes)}  |  vision detections: {len(detections)}")
    print(f"  Outputs (post-warmup): {score['n']}")
    if score["mean_bearing_error_deg"] is not None:
        print(f"  Mean bearing error : {score['mean_bearing_error_deg']:.2f}°")
        print(f"  Max  bearing error : {score['max_bearing_error_deg']:.2f}°")
    else:
        print("  No post-warmup outputs to score.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Usage:")
        print("  python3 -m wavecam.tools.sim.replay --scenario <name>")
        print("  python3 -m wavecam.tools.sim.replay <session.jsonl>")
        print(f"Scenarios: {', '.join(_SCENARIOS)}")
        sys.exit(1)

    if args[0] == "--scenario":
        if len(args) < 2:
            print("--scenario requires a scenario name", file=sys.stderr)
            sys.exit(1)
        _run_scenario_cli(args[1])
    else:
        # JSONL replay mode
        path = args[0]
        records = [json.loads(line) for line in open(path) if line.strip()]
        print(f"Loaded {len(records)} shadow records from {path}")
        errors = []
        for r in records:
            if r.get("gps_updated") and r.get("bearing_deg") is not None:
                errors.append(0.0)
        print(f"Records with GPS update: {len(errors)}")
        print("(Full scoring vs footage is a post-session analysis task, not automated here.)")
