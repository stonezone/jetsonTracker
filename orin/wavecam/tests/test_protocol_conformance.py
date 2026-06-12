"""Runtime conformance: production classes satisfy the seam Protocols.

runtime_checkable isinstance validates method PRESENCE — exactly the check
that would have caught the missing pan_encoder_to_bearing before the rig did.
(mypy validates signatures statically via wavecam/_protocol_conformance.py.)
"""
from wavecam.camera_pose import CameraPose
from wavecam.events import EventRing
from wavecam.protocols import (EventsLike, PoseLike, PtzAbsoluteLike,
                               PtzInquiryLike, PtzStateLike)
from wavecam.ptz_state import PtzState
from wavecam.ptz_visca import NullPtz, ViscaIP


def test_production_classes_satisfy_seam_protocols():
    assert isinstance(CameraPose(), PoseLike)
    assert isinstance(EventRing(), EventsLike)
    assert isinstance(PtzState(NullPtz()), PtzStateLike)
    for cls in (ViscaIP, NullPtz):
        for proto in (PtzAbsoluteLike, PtzInquiryLike):
            assert all(hasattr(cls, m) for m in proto.__protocol_attrs__
                       if not m.startswith("_")), (cls, proto)
