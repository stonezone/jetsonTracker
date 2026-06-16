"""
YOLO26 person validator (Ultralytics). Loads .pt or a TensorRT .engine.
Returns person boxes as (x1, y1, x2, y2, conf). Lazy import so the rest of the
testbed (and the offline self-test) doesn't require torch/ultralytics.
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import List, Tuple


# Curated COCO class labels for the on-frame box label and UI pickers.
# Anything else renders as "cls<N>" — the detector accepts any COCO id 0..79.
CLASS_LABELS = {
    0: "person", 1: "bicycle", 2: "car", 3: "moto", 7: "truck",
    8: "boat", 14: "bird", 15: "cat", 16: "dog", 17: "horse",
    24: "backpack", 25: "umbrella", 29: "frisbee", 32: "ball",
    33: "kite", 36: "skateboard", 37: "surfboard", 41: "cup",
    56: "chair",
}


def class_label(class_id: int) -> str:
    return CLASS_LABELS.get(int(class_id), f"cls{int(class_id)}")


@dataclass
class PersonBox:
    x1: float
    y1: float
    x2: float
    y2: float
    conf: float
    track_id: int | None = None

    @property
    def center(self) -> Tuple[float, float]:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    @property
    def xywh(self) -> Tuple[int, int, int, int]:
        return (int(self.x1), int(self.y1), int(self.x2 - self.x1), int(self.y2 - self.y1))


def _check_model_path(model) -> None:
    """Fail fast if an explicit engine/weights file is missing. A TensorRT .engine
    is never auto-downloaded, and any path with a separator is explicit, so both must
    exist; a bare weights name (e.g. "yolov8n.pt") is left to Ultralytics' auto-
    download. Without this, a missing model surfaces as an opaque Ultralytics crash
    during pipeline construction — i.e. a zombie rig (API up, vision loop dead)."""
    model_str = str(model)
    needs_local_file = model_str.endswith(".engine") or os.sep in model_str
    if needs_local_file and not os.path.exists(model):
        raise FileNotFoundError(
            f"detector model not found: {model_str!r}. Check detector.model in config "
            f"(field rollback: yolov8n.engine)."
        )


class PersonDetector:
    def __init__(self, cfg):
        self.cfg = cfg
        _check_model_path(cfg.model)
        from ultralytics import YOLO  # lazy
        self.model = YOLO(cfg.model)

    def detect(self, frame_bgr) -> List[PersonBox]:
        # Phase-2 (v3): when a tracker is configured, use YOLO.track for persistent
        # IDs; fail-open to plain predict on any error or when tracker is None so the
        # default path is byte-identical to before. Adapted from Kimi's Phase-B draft.
        tracker = getattr(self.cfg, "tracker", None)
        if tracker:
            try:
                return self._track(frame_bgr, tracker)
            except Exception as e:  # pragma: no cover - optional dependency
                print(f"[detector] tracker failed ({e}), falling back to predict")
        return self._predict(frame_bgr)

    def _predict(self, frame_bgr) -> List[PersonBox]:
        res = self.model.predict(
            frame_bgr,
            conf=self.cfg.conf,
            classes=[self.cfg.person_class],
            imgsz=self.cfg.imgsz,
            verbose=False,
        )
        return self._boxes_from_result(res)

    def _track(self, frame_bgr, tracker: str) -> List[PersonBox]:
        res = self.model.track(
            frame_bgr,
            conf=self.cfg.conf,
            classes=[self.cfg.person_class],
            imgsz=self.cfg.imgsz,
            tracker=tracker,
            persist=True,
            verbose=False,
        )
        return self._boxes_from_result(res, with_track=True)

    def _boxes_from_result(self, res, with_track: bool = False) -> List[PersonBox]:
        out: List[PersonBox] = []
        if not res:
            return out
        r = res[0]
        if r.boxes is None:
            return out
        for b in r.boxes:
            x1, y1, x2, y2 = b.xyxy[0].tolist()
            tid = None
            if with_track and b.id is not None:
                tid = int(b.id[0])
            out.append(PersonBox(x1, y1, x2, y2, float(b.conf[0]), track_id=tid))
        return out
