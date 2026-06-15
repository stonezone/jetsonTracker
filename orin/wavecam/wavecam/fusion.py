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

P2 GPS-cue boost (gps_cue_px in update()):
  When the TrackingArbiter hands ownership to gps_tracker, the camera is already
  aimed at the GPS-predicted subject position. A color blob near frame center is
  therefore very likely the subject and deserves a confidence boost that can cross
  the lock threshold. The cue is (cue_x, cue_y, radius_px); a blob within that
  radius gets +gps_boost (cfg.fusion.gps_boost), capped at 0.95. The boost is only
  applied when gps_cue_px is not None (caller gates it on GPS ownership), so a
  random orange object cannot self-lock when GPS isn't directing the camera.

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

# Confidence model (see test_fusion_invariants.py for the asserted semantics):
CONF_MATCHED_BASE = 0.5     # color+person agree: 0.5 + 0.5*person_conf — acquires
CONF_SUSTAIN = 0.45         # color-only / person-near-track: holds a lock, never starts one
CONF_PERSON_ONLY = 0.2      # person with no color: never holds, never starts
CONF_BOOST_CAP = 0.95

# Scale-aware match_dist reference height (px). 240 px ≈ a near subject at 720p;
# at distance the person is smaller so the radius scales down proportionally.
# Clamp range ensures the radius stays usable across the full zoom range.
# (review 2026-06-12 — empirical, to be field-tuned)
MATCH_DIST_SCALE_REF_H = 240.0  # reference bbox height (px)
MATCH_DIST_SCALE_LO = 40.0      # minimum effective radius (px)
MATCH_DIST_SCALE_HI = 240.0     # maximum effective radius (px)


def _effective_match_dist(cfg, person_bbox_h: float) -> float:
    """Return effective match_dist for a person with the given bbox height.

    When cfg.match_dist_scale is False (default) this is always cfg.match_dist
    so the flag-off path is byte-identical.  When True, the radius scales with
    person height: a far (small) box gets a tighter association window.
    """
    base = float(cfg.match_dist)
    if not bool(getattr(cfg, "match_dist_scale", False)):
        return base
    scaled = base * (person_bbox_h / MATCH_DIST_SCALE_REF_H)
    return max(MATCH_DIST_SCALE_LO, min(MATCH_DIST_SCALE_HI, scaled))


@dataclass
class FusionResult:
    target_xy: Optional[Tuple[float, float]] = None
    bbox: Optional[Tuple[int, int, int, int]] = None
    person_bbox: Optional[Tuple[int, int, int, int]] = None
    conf: float = 0.0
    locked: bool = False
    state: str = "SEARCHING"          # SEARCHING | TRACKING | COASTING
    has_color: bool = False
    has_person: bool = False
    matched: bool = False             # color blob and person box agree
    track_id: Optional[int] = None    # persistent id of the tracked person (None = no tracker)


def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _person_xywh(p) -> Tuple[int, int, int, int]:
    if hasattr(p, "xywh"):
        return p.xywh
    return (int(p.x1), int(p.y1), int(p.x2 - p.x1), int(p.y2 - p.y1))


def _person_center(p) -> Tuple[float, float]:
    if hasattr(p, "center"):
        return p.center
    x, y, w, h = _person_xywh(p)
    return (x + w / 2.0, y + h / 2.0)


class Fusion:
    def __init__(self, cfg):
        self.cfg = cfg
        self._locked = False
        self._ema: Optional[Tuple[float, float]] = None
        self._last_seen = 0.0
        self._last_track_id: Optional[int] = None

    def _continuity(self, items, center_of):
        """Prefer the candidate nearest the last smoothed center (anti-flip);
        else the first (detector inputs arrive largest-first)."""
        if self._ema is not None and items:
            return min(items, key=lambda it: _dist(center_of(it), self._ema))
        return items[0]

    def _nearest_person(self, persons, xy) -> Tuple[Optional[PersonBox], float]:
        best, best_d = None, 1e9
        for p in persons:
            d = _dist(self._person_aim(p), xy)
            if d < best_d:
                best, best_d = p, d
        return best, best_d

    def _person_aim(self, p) -> Tuple[float, float]:
        x, y, w, h = _person_xywh(p)
        ax = _clamp01(float(getattr(self.cfg, "person_aim_x", 0.5)))
        ay = _clamp01(float(getattr(self.cfg, "person_aim_y", 0.5)))
        return (x + w * ax, y + h * ay)

    def _confirmed(self, blobs, persons):
        """Persons with a color blob within match_dist (orange + YOLO agree)."""
        return [p for p in persons
                if any(_dist((b.cx, b.cy), _person_center(p))
                       <= _effective_match_dist(self.cfg, _person_xywh(p)[3])
                       for b in blobs)]

    def _prefer_track_id(self, persons):
        """Return the person whose track_id matches the last selected box, else None.
        A no-op when no tracker is active (last_track_id stays None), so the
        flag-off path is byte-identical. (Plan v3 Phase 2; adapted from Kimi.)"""
        if self._last_track_id is None:
            return None
        for p in persons:
            if getattr(p, "track_id", None) == self._last_track_id:
                return p
        return None

    def _select(self, blobs, persons,
                gps_cue_px: Optional[Tuple[float, float, float]] = None):
        """(raw_xy, bbox, person_bbox, conf, matched) by priority + continuity.
        While tracking, a person near the last target keeps the lock so an
        unmatched far blob can't steal it; color-blob outranks person-only only
        for acquisition.

        gps_cue_px = (cue_x, cue_y, radius_px) when the camera is GPS-pointed;
        blobs within radius get a confidence boost (see module docstring)."""
        confirmed = self._confirmed(blobs, persons)
        if confirmed:
            p = self._prefer_track_id(confirmed) or self._continuity(confirmed, self._person_aim)
            self._last_track_id = getattr(p, "track_id", None)
            bbox = _person_xywh(p)
            return self._person_aim(p), bbox, bbox, CONF_MATCHED_BASE + CONF_MATCHED_BASE * p.conf, True
        if self.cfg.require_person:
            return None, None, None, 0.0, False
        if self._ema is not None and persons:
            p, d = self._nearest_person(persons, self._ema)
            # Prefer the same persistent track over pure proximity (no-op without a tracker)
            same_id = self._prefer_track_id(persons)
            if same_id is not None:
                p, d = same_id, _dist(self._person_aim(same_id), self._ema)
            if p is not None and d <= _effective_match_dist(self.cfg, _person_xywh(p)[3]):
                self._last_track_id = getattr(p, "track_id", None)
                bbox = _person_xywh(p)
                conf = CONF_SUSTAIN
                if gps_cue_px is not None:
                    cx, cy, r = gps_cue_px
                    if _dist(self._person_aim(p), (cx, cy)) <= r:
                        boost = float(getattr(self.cfg, "gps_boost", 0.2))
                        conf = min(CONF_BOOST_CAP, conf + boost)
                return self._person_aim(p), bbox, bbox, conf, False
        if blobs:
            if gps_cue_px is not None and self._ema is None:
                # No existing EMA track: choose the blob nearest the GPS cue
                # (camera is already pointed there; prefer that signal over continuity).
                cx, cy, r = gps_cue_px
                b = min(blobs, key=lambda x: _dist((x.cx, x.cy), (cx, cy)))
            else:
                b = self._continuity(blobs, lambda x: (x.cx, x.cy))
            conf = CONF_SUSTAIN
            if gps_cue_px is not None:
                cx, cy, r = gps_cue_px
                if _dist((b.cx, b.cy), (cx, cy)) <= r:
                    boost = float(getattr(self.cfg, "gps_boost", 0.2))
                    conf = min(CONF_BOOST_CAP, conf + boost)
            return (b.cx, b.cy), b.bbox, None, conf, False
        if persons:
            p = self._continuity(persons, self._person_aim)
            bbox = _person_xywh(p)
            return self._person_aim(p), bbox, bbox, CONF_PERSON_ONLY, False
        return None, None, None, 0.0, False

    def update(self, blobs: List[Blob], persons: Optional[List[PersonBox]],
               gps_cue_px: Optional[Tuple[float, float, float]] = None) -> "FusionResult":
        now = time.time()
        persons = persons or []
        has_color, has_person = len(blobs) > 0, len(persons) > 0

        raw_xy, bbox, person_bbox, conf, matched = self._select(blobs, persons, gps_cue_px)

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
            person_bbox=person_bbox if state != "SEARCHING" else None,
            conf=round(conf, 3),
            locked=self._locked,
            state=state,
            has_color=has_color,
            has_person=has_person,
            matched=matched,
            track_id=self._last_track_id if state != "SEARCHING" else None,
        )
