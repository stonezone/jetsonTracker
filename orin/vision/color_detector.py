"""HSV color-blob detector for the bright-orange rashguard (+ red hue-wraparound).

The yard-MVP primary visual cue. The rashguard is hyper-saturated against a yard /
turquoise-water background, so HSV segmentation finds it fast (OpenCV, no GPU) and
runs alongside YOLO — color says *where the orange is*, YOLO says *that's a person*.

Red wraps the hue circle (≈0 and ≈180 in OpenCV's 0..179 H), so red needs two
bands; orange is one band. All bands + blob limits live in ColorConfig (tunable in
the yard). detect() returns candidate boxes + the binary mask (for the UI overlay).

Pure CV, no camera/network — offline-testable on synthetic or still frames.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np


@dataclass
class ColorBox:
    cx: float          # bbox center x (px)
    cy: float          # bbox center y (px)
    w: float           # bbox width (px)
    h: float           # bbox height (px)
    area: int          # blob pixel area (not bbox area)
    fill: float        # blob area / bbox area — solid blob ~1.0, sparse glare ~low

    @property
    def conf(self) -> float:
        """A 0..1 cue confidence proxy: solid, sizeable blobs score higher.
        Lets a ColorBox slot into the same fusion as a YOLO Detection."""
        return max(0.0, min(1.0, self.fill))


@dataclass
class ColorConfig:
    # OpenCV HSV: H 0..179, S/V 0..255.
    orange_low: Tuple[int, int, int] = (8, 90, 90)
    orange_high: Tuple[int, int, int] = (26, 255, 255)
    use_red: bool = True
    red_low_1: Tuple[int, int, int] = (0, 90, 90)
    red_high_1: Tuple[int, int, int] = (10, 255, 255)
    red_low_2: Tuple[int, int, int] = (170, 90, 90)
    red_high_2: Tuple[int, int, int] = (179, 255, 255)
    min_area: int = 80                 # px; reject specks / sun sparkle
    max_area_frac: float = 0.5         # reject blobs > this fraction of frame (glare wash)
    morph_kernel: int = 5              # open+close denoise; 0 disables
    blur: int = 3                      # odd Gaussian kernel on HSV; 0 disables


def _band(hsv, lo, hi):
    return cv2.inRange(hsv, np.array(lo, dtype=np.uint8), np.array(hi, dtype=np.uint8))


def build_mask(frame_bgr: np.ndarray, cfg: ColorConfig) -> np.ndarray:
    """Binary mask of orange (+ red) pixels."""
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    if cfg.blur and cfg.blur >= 3:
        k = cfg.blur | 1  # force odd
        hsv = cv2.GaussianBlur(hsv, (k, k), 0)
    mask = _band(hsv, cfg.orange_low, cfg.orange_high)
    if cfg.use_red:
        mask = cv2.bitwise_or(mask, _band(hsv, cfg.red_low_1, cfg.red_high_1))
        mask = cv2.bitwise_or(mask, _band(hsv, cfg.red_low_2, cfg.red_high_2))
    if cfg.morph_kernel and cfg.morph_kernel >= 3:
        el = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cfg.morph_kernel, cfg.morph_kernel))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, el)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, el)
    return mask


def detect(frame_bgr: np.ndarray,
           cfg: Optional[ColorConfig] = None) -> Tuple[List[ColorBox], np.ndarray]:
    """Find orange/red blobs. Returns (boxes sorted largest-first, binary mask)."""
    cfg = cfg or ColorConfig()
    fh, fw = frame_bgr.shape[:2]
    mask = build_mask(frame_bgr, cfg)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    max_area = cfg.max_area_frac * fw * fh
    out: List[ColorBox] = []
    for c in contours:
        area = int(cv2.contourArea(c))
        if area < cfg.min_area or area > max_area:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        bbox_area = float(bw * bh)
        fill = round(area / bbox_area, 3) if bbox_area else 0.0
        out.append(ColorBox(cx=x + bw / 2.0, cy=y + bh / 2.0,
                            w=float(bw), h=float(bh), area=area, fill=fill))
    out.sort(key=lambda b: b.area, reverse=True)
    return out, mask
