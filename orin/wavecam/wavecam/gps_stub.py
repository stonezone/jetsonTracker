"""GPS / LoRa ingest seam — DISABLED stub.

The vision testbed runs without GPS. This defines the normalized fix contract and
a disabled stub so a cue source can be added later (Orin-side Meshtastic/LoRa
serial) without reworking the pipeline. NOT wired into the pipeline yet.

When the LoRa hardware lands, replace GpsStub with a real reader that returns a
NormalizedFix; course/speed are derived from position deltas (0.2-2 Hz is plenty
for coarse cueing + wave-state classification).
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class NormalizedFix:
    lat: float
    lon: float
    # R8 (audit round-2, 2026-07-01): Optional -- None when the firmware's
    # crs_ok flag is false (course unknown), so predict_lead() (which already
    # skips extrapolation when course_deg is None) doesn't lead a subject due
    # north on an invalid course.
    course: Optional[float]  # course-over-ground, degrees (0..360), or None if unknown
    speed: float           # m/s
    ts: float              # fix epoch seconds
    age_sec: float         # seconds since the fix was taken
    src: str = "lora"      # "lora" | "watch" | ...
    # M9 (audit 2026-07-01): remote fix horizontal accuracy in meters, parsed
    # from the firmware's hacc_cm when present. None = unknown (older firmware
    # / non-LoRa source) -- callers must treat None as "cannot judge", not "good".
    h_acc_m: Optional[float] = None


class GpsStub:
    """Disabled GPS source: always off, never returns a fix. The pipeline only
    depends on get_fix(); swap this for a real Meshtastic/LoRa reader later."""
    enabled = False

    def get_fix(self) -> Optional[NormalizedFix]:
        return None

    def close(self) -> None:
        pass
