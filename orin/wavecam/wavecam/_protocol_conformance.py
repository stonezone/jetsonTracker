"""Static conformance assertions: production classes vs the seam Protocols.

This module is import-light and exists for mypy (it is in mypy.ini's files
list). If a production class drifts from its Protocol — the 2026-06-11
missing-inverse bug — mypy fails HERE with the class and member named,
at commit time instead of on the rig.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .camera_pose import CameraPose
    from .events import EventRing
    from .protocols import (EventsLike, PoseLike, PtzAbsoluteLike,
                            PtzInquiryLike, PtzStateLike)
    from .ptz_state import PtzState
    from .ptz_visca import ViscaIP

    def _conformance(pose: CameraPose, visca: ViscaIP,
                     state: PtzState, ring: EventRing) -> None:
        _pose: PoseLike = pose
        _abs: PtzAbsoluteLike = visca
        _inq: PtzInquiryLike = visca
        _st: PtzStateLike = state
        _ev: EventsLike = ring
