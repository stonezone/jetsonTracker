"""Shared HSV color presets for WaveCam marker tracking."""
from __future__ import annotations


COLOR_PRESETS = {
    "orange_red": {
        "red_low_1": [0, 140, 80],
        "red_high_1": [12, 255, 255],
        "red_low_2": [170, 140, 80],
        "red_high_2": [180, 255, 255],
        "orange_low": [8, 140, 100],
        "orange_high": [28, 255, 255],
    },
    "orange": {
        "orange_low": [8, 140, 100],
        "orange_high": [28, 255, 255],
    },
    "red": {
        # Both hue wraps; sat floor below orange_red's 140 so sun-washed red still reads
        "red_low_1": [0, 120, 70],
        "red_high_1": [10, 255, 255],
        "red_low_2": [170, 120, 70],
        "red_high_2": [180, 255, 255],
    },
    "cyan": {
        "cyan_low": [85, 90, 70],
        "cyan_high": [100, 255, 255],
    },
    "blue": {
        "blue_low": [95, 90, 70],
        "blue_high": [130, 255, 255],
    },
    "green": {
        "green_low": [38, 80, 70],
        "green_high": [85, 255, 255],
    },
    "yellow": {
        "yellow_low": [24, 90, 90],
        "yellow_high": [38, 255, 255],
    },
    "pink": {
        "pink_low": [140, 80, 90],
        "pink_high": [170, 255, 255],
    },
}


def preset_hsv_ranges(name: str) -> dict:
    if name not in COLOR_PRESETS:
        raise KeyError(name)
    return {k: list(v) for k, v in COLOR_PRESETS[name].items()}
