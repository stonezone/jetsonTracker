"""
YOLO26 person validator (Ultralytics). Loads .pt or a TensorRT .engine.
Returns person boxes as (x1, y1, x2, y2, conf). Lazy import so the rest of the
testbed (and the offline self-test) doesn't require torch/ultralytics.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple


# Curated COCO class labels for the on-frame box label and UI pickers.
# Anything else renders as "cls<N>" — the detector accepts any COCO id 0..79.
CLASS_LABELS = {
    0: "person", 1: "bicycle", 2: "car", 3: "moto", 8: "boat",
    14: "bird", 15: "cat", 16: "dog", 32: "ball", 33: "kite",
    37: "surfboard", 41: "cup",
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

    @property
    def center(self) -> Tuple[float, float]:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    @property
    def xywh(self) -> Tuple[int, int, int, int]:
        return (int(self.x1), int(self.y1), int(self.x2 - self.x1), int(self.y2 - self.y1))


class PersonDetector:
    def __init__(self, cfg):
        self.cfg = cfg
        from ultralytics import YOLO  # lazy
        self.model = YOLO(cfg.model)

    def detect(self, frame_bgr) -> List[PersonBox]:
        res = self.model.predict(
            frame_bgr,
            conf=self.cfg.conf,
            classes=[self.cfg.person_class],
            imgsz=self.cfg.imgsz,
            verbose=False,
        )
        out: List[PersonBox] = []
        if not res:
            return out
        r = res[0]
        if r.boxes is None:
            return out
        for b in r.boxes:
            x1, y1, x2, y2 = b.xyxy[0].tolist()
            out.append(PersonBox(x1, y1, x2, y2, float(b.conf[0])))
        return out
