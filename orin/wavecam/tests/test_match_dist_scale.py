"""Package 4: scale-aware match_dist (fusion.match_dist_scale flag-off).

Tests:
  - flag-off: effective radius equals match_dist (byte-identical behavior)
  - small/far person box tightens radius (scaled below base)
  - large/near person box approaches the cap
  - clamp lo=40 and hi=240 hold
  - Fusion._confirmed with match_dist_scale: far blob outside scaled radius → not confirmed
  - match_dist_scale in config default + hot-config key registered
"""
from __future__ import annotations
from types import SimpleNamespace

from wavecam.fusion import (
    _effective_match_dist,
    MATCH_DIST_SCALE_REF_H,
    MATCH_DIST_SCALE_LO,
    MATCH_DIST_SCALE_HI,
    Fusion,
)
from wavecam.config import FusionCfg


# --- _effective_match_dist ---

def _cfg(match_dist: float = 120.0, match_dist_scale: bool = False):
    return SimpleNamespace(match_dist=match_dist, match_dist_scale=match_dist_scale)


def test_flag_off_returns_base():
    """match_dist_scale=False returns match_dist unchanged."""
    assert _effective_match_dist(_cfg(120.0, False), person_bbox_h=80.0) == 120.0
    assert _effective_match_dist(_cfg(120.0, False), person_bbox_h=300.0) == 120.0


def test_scaled_reference_height_returns_base():
    """At the reference height (240 px) the scaled radius equals base."""
    md = _effective_match_dist(_cfg(120.0, True), person_bbox_h=MATCH_DIST_SCALE_REF_H)
    assert abs(md - 120.0) < 0.01


def test_small_far_box_tightens_radius():
    """Person bbox_h=120 (half reference=240) → scaled = 60 px (below base 120)."""
    md = _effective_match_dist(_cfg(120.0, True), person_bbox_h=120.0)
    assert abs(md - 60.0) < 0.1  # 120 * (120/240) = 60, above LO=40 so no clamp


def test_large_near_box_approaches_cap():
    """A very tall box (600 px) → scaled = 300 px → clamped at MATCH_DIST_SCALE_HI."""
    md = _effective_match_dist(_cfg(120.0, True), person_bbox_h=600.0)
    assert md == MATCH_DIST_SCALE_HI


def test_tiny_box_clamps_at_lo():
    """A tiny box (8 px) → scaled = 4 px → clamped at MATCH_DIST_SCALE_LO."""
    md = _effective_match_dist(_cfg(120.0, True), person_bbox_h=8.0)
    assert md == MATCH_DIST_SCALE_LO


def test_clamp_lo_boundary():
    """At height producing exactly MATCH_DIST_SCALE_LO, no undershoot."""
    ref_h = MATCH_DIST_SCALE_REF_H  # 240
    base = 120.0
    # scaled = base * (h / ref_h) == MATCH_DIST_SCALE_LO
    # h = MATCH_DIST_SCALE_LO * ref_h / base = 40*240/120 = 80
    h = MATCH_DIST_SCALE_LO * ref_h / base  # = 80
    md = _effective_match_dist(_cfg(base, True), person_bbox_h=h)
    assert abs(md - MATCH_DIST_SCALE_LO) < 0.01
    # Below this height → clamped
    md_below = _effective_match_dist(_cfg(base, True), person_bbox_h=h - 0.1)
    assert md_below == MATCH_DIST_SCALE_LO


# --- Fusion integration ---

def _blob(cx, cy, h=80, area=5000):
    return SimpleNamespace(cx=cx, cy=cy, area=area,
                           bbox=(int(cx - 20), int(cy - h / 2), 40, h), fill=0.9)


def _person(cx, cy, h=90, conf=0.85):
    return SimpleNamespace(center=(cx, cy), xywh=(int(cx - 20), int(cy - h / 2), 40, h), conf=conf)


def _fusion_cfg(match_dist=120.0, match_dist_scale=False):
    return SimpleNamespace(
        match_dist=match_dist,
        match_dist_scale=match_dist_scale,
        require_person=False,
        lock_threshold=0.60,
        unlock_threshold=0.35,
        ema_alpha=0.5,
        lost_grace_sec=0.8,
        person_aim_x=0.5,
        person_aim_y=0.5,
    )


def test_fusion_flag_off_identical_to_original():
    """With match_dist_scale=False, Fusion._confirmed gives the same result as
    a fixed-radius check at match_dist."""
    f = Fusion(_fusion_cfg(120.0, False))
    # Blob 100 px away from person center
    blob = _blob(220, 180)   # at (220, 180)
    person = _person(320, 180, h=90)  # center at (320, 180), dist=100 px
    # dist=100 < match_dist=120 → person is confirmed (blob is close enough)
    confirmed = f._confirmed([blob], [person])
    assert person in confirmed
    # A blob far away (>120 px) should NOT be confirmed
    blob_far = _blob(500, 180)   # dist=180 > 120
    confirmed_far = f._confirmed([blob_far], [person])
    assert confirmed_far == []


def test_fusion_scale_on_far_person_rejects_distant_blob():
    """With match_dist_scale=True and a small far person (h=60), the effective
    radius is 60 px. A blob 80 px away should NOT be confirmed."""
    f = Fusion(_fusion_cfg(120.0, True))
    # Far person: h=60 → effective_md ≈ 60 px
    person = _person(320, 180, h=60)   # person center ~ (320, 180)
    # Blob 80 px away: should be outside 60 px radius
    blob = _blob(400, 180)   # cx=400 → dist from person_center(320,180) = 80 px
    confirmed = f._confirmed([blob], [person])
    assert confirmed == []   # 80 > 60 → not confirmed


def test_fusion_scale_on_near_person_accepts_close_blob():
    """With match_dist_scale=True and a near person (h=240), effective_md=120 px.
    A blob 100 px away should be confirmed."""
    f = Fusion(_fusion_cfg(120.0, True))
    person = _person(320, 180, h=240)  # near person → effective_md=120
    blob = _blob(420, 180)   # dist=100 < 120 → confirmed
    confirmed = f._confirmed([blob], [person])
    assert person in confirmed


# --- config default + hot-key ---

def test_match_dist_scale_default_false():
    """FusionCfg.match_dist_scale defaults to False (flag-off)."""
    cfg = FusionCfg()
    assert hasattr(cfg, "match_dist_scale")
    assert cfg.match_dist_scale is False


def test_match_dist_scale_hot_key_registered():
    """fusion.match_dist_scale must appear in HOT_CONFIG_KEYS."""
    from wavecam.control_utils import HOT_CONFIG_KEYS
    assert "fusion.match_dist_scale" in HOT_CONFIG_KEYS
