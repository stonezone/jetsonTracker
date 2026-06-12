"""Draw the annotated debug frame: mask blend, color/person boxes, target,
center crosshair + deadzone, the PTZ command vector, and a text HUD."""
from __future__ import annotations
from typing import List, Optional

import cv2
import numpy as np

from .color_detector import Blob
from .detector import PersonBox
from .fusion import FusionResult
from .controller import PtzCommand
from .ptz_visca import PAN_RIGHT, TILT_DOWN

_GREEN = (80, 230, 120)
_AMBER = (40, 180, 255)
_CYAN = (220, 210, 60)
_RED = (60, 60, 255)
_GREY = (150, 150, 150)
_WHITE = (240, 240, 240)


def annotate(frame: np.ndarray, mask: Optional[np.ndarray], blobs: List[Blob],
             persons: List[PersonBox], fr: FusionResult, cmd: Optional[PtzCommand],
             cfg_ptz, hud: dict, show_mask: bool = True,
             person_label: str = "person") -> np.ndarray:
    out = frame.copy()
    h, w = out.shape[:2]

    if show_mask and mask is not None:
        tint = np.zeros_like(out)
        tint[mask > 0] = (0, 90, 160)
        out = cv2.addWeighted(out, 1.0, tint, 0.45, 0)

    for b in blobs:
        x, y, bw, bh = b.bbox
        cv2.rectangle(out, (x, y), (x + bw, y + bh), _AMBER, 1)

    for p in persons:
        x, y, pw, ph = p.xywh
        cv2.rectangle(out, (x, y), (x + pw, y + ph), _GREY, 1)
        cv2.putText(out, f"{person_label} {p.conf:.2f}", (x, max(12, y - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, _GREY, 1, cv2.LINE_AA)

    # center crosshair + deadzone box
    cx, cy = w // 2, h // 2
    dz = cfg_ptz.deadzone
    dx, dy = int(dz * w / 2), int(dz * h / 2)
    cv2.rectangle(out, (cx - dx, cy - dy), (cx + dx, cy + dy), _GREY, 1)
    cv2.drawMarker(out, (cx, cy), _WHITE, cv2.MARKER_CROSS, 18, 1)

    # locked target
    if fr.target_xy is not None:
        tx, ty = int(fr.target_xy[0]), int(fr.target_xy[1])
        col = _GREEN if fr.state == "TRACKING" else _CYAN
        if fr.bbox is not None:
            x, y, bw, bh = fr.bbox
            cv2.rectangle(out, (x, y), (x + bw, y + bh), col, 2)
        cv2.drawMarker(out, (tx, ty), col, cv2.MARKER_TILTED_CROSS, 22, 2)
        cv2.line(out, (cx, cy), (tx, ty), col, 1)

    # command vector arrow (from center)
    if cmd is not None and not cmd.is_stop:
        ax = cx + (60 if cmd.pan_dir == PAN_RIGHT else -60) * (1 if cmd.pan_dir != 0x03 else 0)
        ay = cy + (50 if cmd.tilt_dir == TILT_DOWN else -50) * (1 if cmd.tilt_dir != 0x03 else 0)
        if cmd.pan_dir == 0x03:
            ax = cx
        if cmd.tilt_dir == 0x03:
            ay = cy
        cv2.arrowedLine(out, (cx, cy), (ax, ay), _RED, 2, tipLength=0.3)

    # HUD
    bar = [
        f"STATE {fr.state}",
        f"conf {fr.conf:.2f}",
        f"lock {'Y' if fr.locked else '-'}",
        f"C{'Y' if fr.has_color else '-'} P{'Y' if fr.has_person else '-'} M{'Y' if fr.matched else '-'}",
        f"fps {hud.get('fps', 0):.1f}",
        f"PTZ {hud.get('ptz', 'off')}",
    ]
    if cmd is not None and not cmd.is_stop:
        bar.append(f"cmd p{cmd.pan_speed} t{cmd.tilt_speed}")
    cv2.rectangle(out, (0, 0), (w, 22), (20, 20, 20), -1)
    cv2.putText(out, "  |  ".join(bar), (6, 15),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, _WHITE, 1, cv2.LINE_AA)

    if hud.get("killed"):
        cv2.putText(out, "KILLED", (w // 2 - 60, h // 2 + 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, _RED, 3, cv2.LINE_AA)
    return out
