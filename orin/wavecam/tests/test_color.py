"""Color regression test for wavecam.color_detector — synthetic frames, no camera.

Locks down the merged behavior Codex asked for: fill ratio, glare-wash rejection
via max_area_frac, and a legacy cfg with no blur / no max_area_frac (backward-compat).

    cd ~/Downloads/wavecam-testbed && python -m tests.test_color
"""
from __future__ import annotations
import sys
from types import SimpleNamespace

import cv2
import numpy as np

from wavecam.color_detector import ColorDetector

W, H = 640, 360
TEAL = (128, 128, 0)        # BGR, hue far from orange/red
ORANGE = (0, 165, 255)      # BGR for RGB(255,165,0)

HSV = {
    "red_low_1": [0, 90, 80], "red_high_1": [12, 255, 255],
    "red_low_2": [170, 90, 80], "red_high_2": [180, 255, 255],
    "orange_low": [8, 90, 100], "orange_high": [28, 255, 255],
}


def _cfg(**kw):
    base = dict(hsv_ranges=HSV, morph_kernel=5, min_area=60, max_area=200000)
    base.update(kw)
    return SimpleNamespace(**base)


def _frame(bg=TEAL):
    f = np.zeros((H, W, 3), np.uint8)
    f[:] = bg
    return f


_n = 0


def check(c, m):
    global _n
    _n += 1
    if not c:
        print("FAIL:", m)
        sys.exit(1)
    print("  ok:", m)


def main():
    # fill ratio: a solid orange rectangle -> high fill, conf in range
    f = _frame()
    cv2.rectangle(f, (300, 150), (360, 250), ORANGE, -1)
    blobs, _ = ColorDetector(_cfg(blur=3, max_area_frac=0.5)).detect(f)
    check(bool(blobs) and blobs[0].fill > 0.8, "solid blob -> fill > 0.8")
    check(0.0 <= blobs[0].conf <= 1.0, "conf clamped to [0,1]")

    # glare wash: whole frame orange -> rejected by max_area_frac
    blobs, _ = ColorDetector(_cfg(max_area_frac=0.5)).detect(_frame(ORANGE))
    check(len(blobs) == 0, "full-frame wash rejected by max_area_frac")

    # legacy cfg: no blur / no max_area_frac attrs -> still detects + fills
    f = _frame()
    cv2.rectangle(f, (300, 150), (360, 250), ORANGE, -1)
    blobs, _ = ColorDetector(_cfg()).detect(f)
    check(bool(blobs) and blobs[0].fill > 0.8, "legacy cfg (no blur/frac) still works")

    # teal-only frame -> no blobs
    check(len(ColorDetector(_cfg()).detect(_frame())[0]) == 0, "teal-only -> no blobs")

    print("\nALL %d CHECKS PASSED" % _n)


if __name__ == "__main__":
    main()


def test_all_presets_have_paired_bounds_and_valid_hsv():
    """Every preset (incl. new red/cyan) must expose *_low/*_high pairs with
    in-range HSV values — a malformed preset would silently detect nothing."""
    from wavecam.color_presets import COLOR_PRESETS, preset_hsv_ranges
    assert "red" in COLOR_PRESETS and "cyan" in COLOR_PRESETS
    for name in COLOR_PRESETS:
        d = preset_hsv_ranges(name)
        lows = [k for k in d if "_low" in k]
        assert lows, name
        for k in lows:
            hk = k.replace("_low", "_high")
            assert hk in d, f"{name}: {k} missing {hk}"
            lo, hi = d[k], d[hk]
            assert len(lo) == 3 and len(hi) == 3
            assert 0 <= lo[0] <= hi[0] <= 180, f"{name} hue"
            assert all(0 <= lo[i] <= hi[i] <= 255 for i in (1, 2)), name


def test_detector_class_label():
    from wavecam.detector import class_label
    assert class_label(0) == "person"
    assert class_label(3) == "moto"
    assert class_label(37) == "surfboard"
    assert class_label(63) == "cls63"
