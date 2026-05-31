"""PTZ camera control for the Prisual camera (P2).

Backends drive the camera over the network. VISCA-over-IP (raw UDP) is the
validated default for control + position read-back (no auth, no extra deps).
HTTP-CGI (velocity) and ONVIF (normalized absolute/readback) are alternates.
"""
from .camera_adapter import CameraControlAdapter, MockCameraAdapter, PTZPosition

__all__ = ["CameraControlAdapter", "MockCameraAdapter", "PTZPosition"]
