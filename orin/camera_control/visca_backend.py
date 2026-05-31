"""VISCA-over-IP backend (raw UDP) for the Prisual PTZ camera.

Validated against the camera on UDP 1259: PanTilt/Zoom inquiries return position
(no auth, stdlib sockets only). Pan/tilt/zoom are raw VISCA encoder units.

VISCA reference (camera address 1 -> first byte 0x81):
  PanTiltPosInq   81 09 06 12 FF        -> 90 50 [pan*4 nibbles][tilt*4 nibbles] FF
  ZoomPosInq      81 09 04 47 FF        -> 90 50 [zoom*4 nibbles] FF
  AbsolutePos     81 01 06 02 vv ww [pan*4][tilt*4] FF   (vv/ww = pan/tilt speed)
  Pan/Tilt drive  81 01 06 01 vv ww p q FF   (p:1=L,2=R,3=stop; q:1=U,2=D,3=stop)
  Zoom variable   81 01 04 07 0p          (p: 2x=tele,3x=wide,0=stop; x=speed 0-7)
  Home            81 01 06 04 FF
"""

import socket
from typing import Optional

from .camera_adapter import CameraControlAdapter, PTZPosition

HDR = 0x81
PAN_SPEED_MAX = 0x18   # 24
TILT_SPEED_MAX = 0x14  # 20
ZOOM_SPEED_MAX = 7


def int_to_nibbles(v: int, n: int = 4) -> bytes:
    """Signed int -> n bytes, each carrying one low nibble (VISCA position)."""
    v &= (1 << (4 * n)) - 1
    return bytes((v >> (4 * (n - 1 - i))) & 0x0F for i in range(n))


def nibbles_to_int(b: bytes) -> int:
    v = 0
    for x in b:
        v = (v << 4) | (x & 0x0F)
    return v - 0x10000 if (len(b) == 4 and v >= 0x8000) else v


class ViscaBackend(CameraControlAdapter):
    supports_position_readback = True
    supports_absolute = True

    def __init__(self, host: str, port: int = 1259, timeout: float = 1.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None

    def connect(self) -> bool:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(self.timeout)
        return self.get_position() is not None

    def close(self) -> None:
        if self._sock:
            self._sock.close()
            self._sock = None

    # --- transport ---
    def _send(self, payload: bytes) -> None:
        self._sock.sendto(payload, (self.host, self.port))

    def _drain(self) -> None:
        """Discard pending ACK/completion datagrams left by prior commands."""
        self._sock.setblocking(False)
        try:
            while True:
                self._sock.recvfrom(64)
        except OSError:
            pass
        finally:
            self._sock.settimeout(self.timeout)

    def _inq(self, payload: bytes) -> Optional[bytes]:
        # Inquiry replies are "90 50 ... FF". Drain stale frames, send, then read
        # past any late ACK/completion ("90 4x"/"90 5x" with x!=0) to find it.
        self._drain()
        self._send(payload)
        for _ in range(4):
            try:
                data, _ = self._sock.recvfrom(64)
            except socket.timeout:
                break
            if len(data) >= 3 and data[0] == 0x90 and data[1] == 0x50:
                return data
        return None

    # --- read-back ---
    def get_position(self) -> Optional[PTZPosition]:
        pt = self._inq(bytes([HDR, 0x09, 0x06, 0x12, 0xFF]))
        zm = self._inq(bytes([HDR, 0x09, 0x04, 0x47, 0xFF]))
        if not pt or len(pt) < 11 or not zm or len(zm) < 7:
            return None
        return PTZPosition(
            nibbles_to_int(pt[2:6]),
            nibbles_to_int(pt[6:10]),
            nibbles_to_int(zm[2:6]),
        )

    # --- velocity ---
    @staticmethod
    def _speed(vel: float, vmax: int) -> int:
        return max(1, min(vmax, int(round(abs(vel) * vmax))))

    def pan_tilt_velocity(self, pan_vel: float, tilt_vel: float) -> None:
        pdir = 0x03 if abs(pan_vel) < 1e-3 else (0x02 if pan_vel > 0 else 0x01)  # +=right
        tdir = 0x03 if abs(tilt_vel) < 1e-3 else (0x01 if tilt_vel > 0 else 0x02)  # +=up
        self._send(bytes([HDR, 0x01, 0x06, 0x01,
                          self._speed(pan_vel, PAN_SPEED_MAX),
                          self._speed(tilt_vel, TILT_SPEED_MAX),
                          pdir, tdir, 0xFF]))

    def zoom_velocity(self, vel: float) -> None:
        if abs(vel) < 1e-3:
            self._send(bytes([HDR, 0x01, 0x04, 0x07, 0x00, 0xFF]))
            return
        spd = max(0, min(ZOOM_SPEED_MAX, int(round(abs(vel) * ZOOM_SPEED_MAX))))
        p = (0x20 if vel > 0 else 0x30) | spd  # tele / wide
        self._send(bytes([HDR, 0x01, 0x04, 0x07, p, 0xFF]))

    def stop(self) -> None:
        self._send(bytes([HDR, 0x01, 0x06, 0x01, 0x01, 0x01, 0x03, 0x03, 0xFF]))
        self._send(bytes([HDR, 0x01, 0x04, 0x07, 0x00, 0xFF]))

    # --- absolute ---
    def move_absolute(self, pan: float, tilt: float,
                      pan_speed: float = 0.5, tilt_speed: float = 0.5) -> None:
        body = (bytes([HDR, 0x01, 0x06, 0x02,
                       self._speed(pan_speed, PAN_SPEED_MAX),
                       self._speed(tilt_speed, TILT_SPEED_MAX)])
                + int_to_nibbles(int(round(pan)))
                + int_to_nibbles(int(round(tilt)))
                + bytes([0xFF]))
        self._send(body)

    def zoom_absolute(self, zoom: float) -> None:
        self._send(bytes([HDR, 0x01, 0x04, 0x47]) + int_to_nibbles(int(round(zoom))) + bytes([0xFF]))

    def home(self) -> None:
        self._send(bytes([HDR, 0x01, 0x06, 0x04, 0xFF]))
