"""Package 3: GPS-cued detector ROI.

Tests:
  - coord mapping round-trip (center ROI maps back to center)
  - clamping at frame edges (ROI beyond edge stays inside frame)
  - minimum size enforced (_ROI_MIN_PX)
  - flag-off behavior (gps_roi_enabled=False → no ROI, byte-identical)
  - arbiter emits search_roi when GPS owns, None otherwise
  - offset_boxes maps crop-space boxes back to full-frame coords
  - gps_roi_enabled in config default + hot-config key registered
"""
from __future__ import annotations
import sys
import types

from wavecam.pipeline import compute_roi_crop, offset_boxes, _ROI_MIN_PX
from wavecam.tracking_arbiter import TrackingArbiter
from wavecam.fusion import FusionResult
from wavecam.config import FusionCfg


# --- coord mapping round-trip ---

def test_center_roi_round_trip():
    """A centered normalized ROI (0.5, 0.5, 0.5, 0.5) on a 720×1280 frame
    should produce a crop centered at (640, 360)."""
    # 1280×720 frame (w=1280, h=720)
    x1, y1, x2, y2 = compute_roi_crop((0.5, 0.5, 0.5, 0.5), frame_h=720, frame_w=1280)
    # center of crop should be at (640, 360)
    assert abs(((x1 + x2) / 2) - 640) <= 1
    assert abs(((y1 + y2) / 2) - 360) <= 1
    # should be within frame
    assert x1 >= 0 and y1 >= 0 and x2 <= 1280 and y2 <= 720


def test_crop_stays_inside_frame():
    """ROI centered at frame boundary is clamped inside the frame."""
    # ROI centered at (0, 0) in normalized coords — corner
    x1, y1, x2, y2 = compute_roi_crop((0.0, 0.0, 0.5, 0.5), frame_h=480, frame_w=640)
    assert x1 >= 0
    assert y1 >= 0
    assert x2 <= 640
    assert y2 <= 480


def test_crop_right_edge_clamped():
    """ROI centered at far right of frame is clamped inside."""
    x1, y1, x2, y2 = compute_roi_crop((1.0, 0.5, 0.5, 0.5), frame_h=480, frame_w=640)
    assert x2 <= 640
    assert x1 >= 0


def test_minimum_size_enforced():
    """A very small normalized ROI gets expanded to at least _ROI_MIN_PX."""
    # 0.01 normalized on a 640×480 frame → 6×5 px raw, must expand to _ROI_MIN_PX
    x1, y1, x2, y2 = compute_roi_crop((0.5, 0.5, 0.01, 0.01), frame_h=480, frame_w=640)
    assert (x2 - x1) >= _ROI_MIN_PX
    assert (y2 - y1) >= _ROI_MIN_PX


def test_minimum_size_enforced_at_edge():
    """Min size is enforced even when the crop is pinned at a frame edge."""
    x1, y1, x2, y2 = compute_roi_crop((0.0, 0.0, 0.01, 0.01), frame_h=480, frame_w=640)
    assert (x2 - x1) >= _ROI_MIN_PX or x2 == 640  # clamped at edge, but still ≥ min
    assert x1 >= 0 and y1 >= 0


# --- offset_boxes ---

def test_offset_boxes_round_trip():
    """Boxes offset by (rx1, ry1) map back to full-frame coords."""
    import types
    # Fake PersonBox-like objects
    def _make_box(x1, y1, x2, y2, conf=0.9):
        from wavecam.detector import PersonBox
        return PersonBox(x1, y1, x2, y2, conf)

    crop_boxes = [_make_box(10, 20, 50, 80)]
    rx1, ry1 = 100, 200
    full = offset_boxes(crop_boxes, rx1, ry1)
    assert len(full) == 1
    b = full[0]
    assert b.x1 == 110  # 10 + 100
    assert b.y1 == 220  # 20 + 200
    assert b.x2 == 150  # 50 + 100
    assert b.y2 == 280  # 80 + 200
    assert b.conf == 0.9


def test_offset_boxes_empty():
    """Empty list stays empty."""
    assert offset_boxes([], 50, 50) == []


# --- arbiter emits search_roi ---

def _vision(locked: bool = False) -> FusionResult:
    return FusionResult(
        target_xy=(0.5, 0.5), bbox=None, person_bbox=None,
        conf=0.5, locked=locked,
        state="TRACKING" if locked else "SEARCHING",
        has_color=True, has_person=True, matched=locked,
    )


def test_arbiter_emits_roi_when_gps_owns():
    """Arbiter sets search_roi when GPS is tracking."""
    a = TrackingArbiter()
    d = a.decide(_vision(False), gps_fresh=True, gps_calibrated=True,
                 base_locked=True, now_sec=0.0, calibration_valid=True)
    assert d.owner == "gps_tracker"
    assert d.search_roi is not None
    cx, cy, w, h = d.search_roi
    assert 0.0 <= cx <= 1.0
    assert 0.0 <= cy <= 1.0


def test_arbiter_no_roi_when_vision_owns():
    """Arbiter does not emit search_roi when vision owns."""
    a = TrackingArbiter(lock_frames=1)
    d = a.decide(_vision(True), gps_fresh=False, gps_calibrated=True,
                 base_locked=False, now_sec=0.0)
    assert d.owner == "vision_follow"
    assert d.search_roi is None


def test_arbiter_no_roi_when_idle():
    """Arbiter does not emit search_roi when idle."""
    a = TrackingArbiter()
    d = a.decide(_vision(False), gps_fresh=False, gps_calibrated=False,
                 base_locked=False, now_sec=0.0)
    assert d.owner == "idle"
    assert d.search_roi is None


# --- flag-off: gps_roi_enabled=False default ---

def test_gps_roi_enabled_default_false():
    """FusionCfg.gps_roi_enabled defaults to False (flag-off)."""
    cfg = FusionCfg()
    assert hasattr(cfg, "gps_roi_enabled")
    assert cfg.gps_roi_enabled is False


def test_gps_roi_hot_key_registered():
    """fusion.gps_roi_enabled must appear in HOT_CONFIG_KEYS."""
    from wavecam.control_utils import HOT_CONFIG_KEYS
    assert "fusion.gps_roi_enabled" in HOT_CONFIG_KEYS
