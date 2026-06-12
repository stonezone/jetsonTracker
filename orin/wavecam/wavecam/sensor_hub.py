"""Phone-on-tripod sensor ingest — Phase-3 T3.2.

The iPhone sits on the STATIC tripod plate as the operator console.
Its heading is a *tripod reference*, constant regardless of camera pan.
This hub records the latest phone POST and drives two alert-only monitors:

  (a) heading-drift — phone heading vs its own session baseline; deviation
      beyond sensors.drift_alert_deg for >10s fires an anchor_suspect event
      once per excursion (hysteresis: re-arms after returning within half
      the threshold).
  (b) bump — accelerometer spike above threshold; fires anchor_suspect
      immediately, rate-limited to 1/10s.

NEVER corrective. Observe-only until Phase-3 post-G-PH evidence justifies it.

Threading: HTTP POSTs ARE the feed — no background thread needed.  All
reads/writes are lock-guarded so the alert loop and the request handler
never race.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional


@dataclass
class PhoneSample:
    """One inbound POST from the iOS publisher."""
    heading_deg: Optional[float]       # None → absent / not reported
    heading_acc: Optional[float]       # <0 → invalid (iOS convention)
    lat: Optional[float]
    lon: Optional[float]
    h_acc: Optional[float]
    bump: bool
    received_at: float                 # time.time() at ingest


def _normalize_180(delta: float) -> float:
    """Wrap an angular delta to (−180, +180]."""
    delta = delta % 360.0
    if delta > 180.0:
        delta -= 360.0
    return delta


class SensorHub:
    """Lock-guarded cache of the latest phone sample plus alert state.

    No background threads. `ingest()` is called on the FastAPI request
    thread; `latest()` is a non-blocking snapshot read.
    """

    def __init__(self, events, cfg) -> None:
        """
        events: EventRing instance (or None in unit tests — hub skips recording).
        cfg:    live Config object; reads cfg.sensors.enabled / drift_alert_deg.
        """
        self._events = events
        self._cfg = cfg
        self._lock = threading.Lock()

        # Latest sample (None until first POST).
        self._sample: Optional[PhoneSample] = None

        # Heading baseline: first valid heading sample after service start (or reset).
        self._heading_baseline: Optional[float] = None

        # Drift alert state.
        self._excursion_start: Optional[float] = None   # time when drift went over threshold
        self._excursion_fired: bool = False             # True if anchor_suspect already fired
        self._ALERT_SUSTAIN_SEC: float = 10.0
        self._REARM_FRAC: float = 0.5               # re-arm when within 50% of threshold

        # Bump rate-limit: at most one anchor_suspect/bump per 10s.
        self._last_bump_event: float = 0.0
        self._BUMP_RATE_LIMIT_SEC: float = 10.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ingest(self, sample: PhoneSample) -> None:
        """Called by the route handler with the decoded sample.

        Stores the sample, updates baseline, runs drift and bump monitors.
        No-ops if sensors.enabled is False (cheap kill-switch; route still 200s).
        """
        if not getattr(getattr(self._cfg, "sensors", None), "enabled", False):
            return

        with self._lock:
            self._sample = sample
            self._update_baseline(sample)
            self._check_drift(sample)
            self._check_bump(sample)

    def latest(self) -> Optional[PhoneSample]:
        """Non-blocking snapshot of the most recent sample (None if none yet)."""
        with self._lock:
            return self._sample

    def reset_baseline(self) -> None:
        """Force the heading baseline to be re-captured on the next valid sample."""
        with self._lock:
            self._heading_baseline = None
            self._excursion_start = None
            self._excursion_fired = False

    # ------------------------------------------------------------------
    # Internal helpers (must be called under self._lock)
    # ------------------------------------------------------------------

    def _update_baseline(self, sample: PhoneSample) -> None:
        if self._heading_baseline is not None:
            return  # already set
        h = sample.heading_deg
        acc = sample.heading_acc
        if h is None or acc is None or acc < 0:
            return  # invalid heading — don't set baseline
        self._heading_baseline = h

    def _drift_alert_deg(self) -> float:
        return float(getattr(getattr(self._cfg, "sensors", None), "drift_alert_deg", 12.0))

    def _check_drift(self, sample: PhoneSample) -> None:
        baseline = self._heading_baseline
        h = sample.heading_deg
        acc = sample.heading_acc
        if baseline is None or h is None or acc is None or acc < 0:
            # No valid heading — reset excursion tracking.
            self._excursion_start = None
            self._excursion_fired = False
            return

        threshold = self._drift_alert_deg()
        deviation = abs(_normalize_180(h - baseline))
        now = sample.received_at

        if deviation > threshold:
            if self._excursion_start is None:
                self._excursion_start = now
            # Fire once per excursion after sustained deviation.
            if not self._excursion_fired and (now - self._excursion_start) >= self._ALERT_SUSTAIN_SEC:
                self._fire_event("anchor_suspect", {
                    "reason": "heading_drift",
                    "deviation_deg": round(deviation, 1),
                    "threshold_deg": threshold,
                    "baseline_deg": round(baseline, 1),
                    "heading_deg": round(h, 1),
                    "heading_acc": round(acc, 1),
                })
                self._excursion_fired = True
        else:
            # Within threshold — reset excursion counter.
            self._excursion_start = None
            # Re-arm (allow next excursion to fire) only after returning within half the threshold.
            if self._excursion_fired and deviation <= threshold * self._REARM_FRAC:
                self._excursion_fired = False

    def _check_bump(self, sample: PhoneSample) -> None:
        if not sample.bump:
            return
        now = sample.received_at
        if (now - self._last_bump_event) >= self._BUMP_RATE_LIMIT_SEC:
            self._fire_event("anchor_suspect", {"reason": "bump"})
            self._last_bump_event = now

    def _fire_event(self, kind: str, detail: dict) -> None:
        if self._events is not None:
            self._events.record(kind, detail)
