"""
Color detector: orange/red blobs in HSV.

Red wraps the hue circle, so we OR two red bands + one orange band. Returns
candidate blobs (center, area, bbox, fill) sorted largest-first, plus the binary
mask for the overlay. `fill` = blob area / bbox area, a solidity proxy that lets
fusion prefer a solid jersey over sparse sun-sparkle; an optional fractional
area cap rejects a full-frame glare wash.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple

import cv2
import numpy as np


@dataclass
class Blob:
    cx: float
    cy: float
    area: float
    bbox: Tuple[int, int, int, int]   # x, y, w, h
    fill: float = 1.0                  # blob area / bbox area (solidity, 0..1)

    @property
    def conf(self) -> float:
        return max(0.0, min(1.0, self.fill))


class ColorDetector:
    def __init__(self, cfg):
        self.cfg = cfg
        self.update_ranges(cfg.hsv_ranges)
        k = max(1, int(cfg.morph_kernel))
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))

    def update_ranges(self, hsv_ranges: dict) -> None:
        r = hsv_ranges
        self._bands = [
            (np.array(r["red_low_1"]), np.array(r["red_high_1"])),
            (np.array(r["red_low_2"]), np.array(r["red_high_2"])),
            (np.array(r["orange_low"]), np.array(r["orange_high"])),
        ]

    def _mask(self, frame_bgr: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
        blur = int(getattr(self.cfg, "blur", 0) or 0)
        if blur >= 3:
            hsv = cv2.GaussianBlur(hsv, (blur | 1, blur | 1), 0)
        mask = None
        for lo, hi in self._bands:
            m = cv2.inRange(hsv, lo, hi)
            mask = m if mask is None else cv2.bitwise_or(mask, m)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._kernel)
        return mask

    def detect(self, frame_bgr: np.ndarray) -> Tuple[List[Blob], np.ndarray]:
        h, w = frame_bgr.shape[:2]
        mask = self._mask(frame_bgr)
        frac = float(getattr(self.cfg, "max_area_frac", 1.0) or 1.0)
        max_area = min(self.cfg.max_area, frac * w * h)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        blobs: List[Blob] = []
        for c in contours:
            area = cv2.contourArea(c)
            if area < self.cfg.min_area or area > max_area:
                continue
            x, y, bw, bh = cv2.boundingRect(c)
            fill = round(area / float(bw * bh), 3) if bw * bh else 0.0
            blobs.append(Blob(x + bw / 2.0, y + bh / 2.0, area, (x, y, bw, bh), fill))
        blobs.sort(key=lambda b: b.area, reverse=True)
        return blobs, mask
