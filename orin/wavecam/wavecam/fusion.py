"""
Fusion (vision-only, no GPS, no camera-AI):

  - candidate priority: a color-confirmed person (orange near a YOLO person) is
    always top. For ACQUISITION a color blob outranks a person-with-no-orange
    (orange is the primary cue). But while LOCKED, a person near the last target
    keeps the lock, so an unmatched far blob can't steal a tracked subject.
  - within the chosen source, prefer the candidate nearest the last smoothed
    target (temporal continuity) so the lock does not flip between objects.
  - lock uses hysteresis (lock/unlock thresholds) so it doesn't flap.
  - target center is EMA-smoothed to keep the servo calm.
  - a short grace window holds lock through brief dropouts.

Returns a FusionResult the controller and overlay consume.
"""
from __future__ import annotations
import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Tuple

if TYPE_CHECKING:                       # type-only: keeps fusion importable cv2-free
    from .color_detector import Blob
    from .detector import PersonBox


@dataclass
class FusionResult:
    target_xy: Optional[Tuple[float, float]] = None
    bbox: Optional[Tuple[int, int, int, int]] = None
    conf: float = 0.0
    locked: bool = False
    state: str = "SEARCHING"          # SEARCHING | TRACKING | COASTING
    has_color: bool = False
    has_person: bool = False
    matched: bool = False             # color blob and person box agree


def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


class Fusion:
    def __init__(self, cfg):
        self.cfg = cfg
        self._locked = False
        self._ema: Optional[Tuple[float, float]] = None
        self._last_seen = 0.0

    def _continuity(self, items, center_of):
        """Prefer the candidate nearest the last smoothed center (anti-flip);
        else the first (detector inputs arrive largest-first)."""
        if self._ema is not None and items:
            return min(items, key=lambda it: _dist(center_of(it), self._ema))
        return items[0]

    def _nearest_person(self, persons, xy) -> Tuple[Optional[PersonBox], float]:
        best, best_d = None, 1e9
        for p in persons:
            d = _dist(p.center, xy)
            if d < best_d:
                best, best_d = p, d
        return best, best_d

    def _confirmed(self, blobs, persons):
        """Persons with a color blob within match_dist (orange + YOLO agree)."""
        md = self.cfg.match_dist
        return [p for p in persons
                if any(_dist((b.cx, b.cy), p.center) <= md for b in blobs)]

    def _select(self, blobs, persons):
        """(raw_xy, bbox, conf, matched) by priority + continuity. While tracking,
        a person near the last target keeps the lock so an unmatched far blob can't
        steal it; color-blob outranks person-only only for acquisition."""
        confirmed = self._confirmed(blobs, persons)
        if confirmed:
            p = self._continuity(confirmed, lambda x: x.center)
            return p.center, p.xywh, 0.5 + 0.5 * p.conf, True
        if self.cfg.require_person:
            return None, None, 0.0, False
        if self._ema is not None and persons:
            p, d = self._nearest_person(persons, self._ema)
            if p is not None and d <= self.cfg.match_dist:
                return p.center, p.xywh, 0.45, False      # locked subject -> sustain
        if blobs:
            b = self._continuity(blobs, lambda x: (x.cx, x.cy))
            return (b.cx, b.cy), b.bbox, 0.45, False
        if persons:
            p = self._continuity(persons, lambda x: x.center)
            return p.center, p.xywh, 0.2, False
        return None, None, 0.0, False

    def update(self, blobs: List[Blob], persons: Optional[List[PersonBox]]) -> FusionResult:
        now = time.time()
        persons = persons or []
        has_color, has_person = len(blobs) > 0, len(persons) > 0

        raw_xy, bbox, conf, matched = self._select(blobs, persons)

        if conf >= self.cfg.lock_threshold:
            self._locked = True
        elif conf < self.cfg.unlock_threshold:
            self._locked = False

        if raw_xy is not None and conf > 0:
            self._last_seen = now
            a = self.cfg.ema_alpha
            self._ema = raw_xy if self._ema is None else \
                (a * raw_xy[0] + (1 - a) * self._ema[0],
                 a * raw_xy[1] + (1 - a) * self._ema[1])

        coasting = (raw_xy is None or conf == 0) and self._ema is not None and \
                   (now - self._last_seen) <= self.cfg.lost_grace_sec

        if raw_xy is not None and conf > 0 and self._locked:
            state, out_xy = "TRACKING", self._ema
        elif coasting and self._locked:
            state, out_xy = "COASTING", self._ema
        else:
            state, out_xy = "SEARCHING", None
            if not coasting:
                self._locked = False
                self._ema = None

        return FusionResult(
            target_xy=out_xy,
            bbox=bbox if state != "SEARCHING" else None,
            conf=round(conf, 3),
            locked=self._locked,
            state=state,
            has_color=has_color,
            has_person=has_person,
            matched=matched,
        )
