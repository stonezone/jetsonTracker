"""One persisted document for ALL calibration state — the pose mapping AND the
per-step capture log AND reference_heading. Replaces the adapter's in-memory
_calibration dict + separate camera_pose.json, whose split produced
"gps_calibrated true but reference_heading null" after every restart."""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from .camera_pose import CameraPose

_POSE_FIELDS = set(CameraPose.__dataclass_fields__)


@dataclass
class CalibrationStore:
    path: str
    pose: CameraPose = field(default_factory=CameraPose)
    reference_heading: Optional[float] = None
    steps: dict = field(default_factory=dict)       # step name -> capture entry
    updated_at_unix_ms: Optional[int] = None

    def set_step(self, step: str, entry: dict) -> None:
        now = int(time.time() * 1000)
        self.steps[step] = {**entry, "captured_at_unix_ms": now}
        self.updated_at_unix_ms = now
        if step == "heading" and "heading_deg" in entry:
            self.reference_heading = entry["heading_deg"]

    def save(self) -> None:
        doc = {"pose": asdict(self.pose), "reference_heading": self.reference_heading,
               "steps": self.steps, "updated_at_unix_ms": self.updated_at_unix_ms}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2)
        os.replace(tmp, self.path)

    @classmethod
    def load(cls, path: str) -> "CalibrationStore":
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except Exception:
            return cls(path=path)
        if "pose" not in doc and _POSE_FIELDS & set(doc):
            return cls(path=path, pose=CameraPose(**{k: v for k, v in doc.items()
                                                     if k in _POSE_FIELDS}))   # legacy migration
        return cls(path=path,
                   pose=CameraPose(**doc.get("pose", {})),
                   reference_heading=doc.get("reference_heading"),
                   steps=doc.get("steps", {}),
                   updated_at_unix_ms=doc.get("updated_at_unix_ms"))
