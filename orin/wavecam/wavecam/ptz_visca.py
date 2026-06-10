"""VISCA-over-IP (UDP) client — RAW framing for the Prisual PTZ.

The Prisual on UDP 1259 (no auth) speaks RAW VISCA: the classic 0x81..0xFF
command bytes with NO Sony VISCA-over-IP 8-byte header, and replies arrive raw
(90 50 .. FF). Bench-validated end-to-end — pan/tilt/zoom, position readback,
two-point calibration (pan_enc_per_deg=4.47), and the vision-follow loop all ran
on this transport (ground truth: orin/camera_control/visca_backend.py).

The controller depends only on the method interface (pan_tilt, stop, zoom, home,
inquire_pan_tilt), so the transport stays swappable if a future camera needs the
Sony framing header.
"""
from __future__ import annotations
import socket
import threading

PAN_LEFT = 0x01
PAN_RIGHT = 0x02
PAN_STOP = 0x03
TILT_UP = 0x01
TILT_DOWN = 0x02
TILT_STOP = 0x03


class ViscaIP:
    def __init__(self, ip: str, port: int = 1259, address: int = 1, timeout: float = 0.3):
        self.ip = ip
        self.port = port
        self.addr = 0x80 | (address & 0x0F)        # 0x81 for address 1
        self.timeout = timeout
        self._lock = threading.Lock()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(timeout)

    # ---- transport: RAW VISCA, no VISCA-over-IP header (validated @1259) ----
    def _send(self, payload: bytes) -> None:
        with self._lock:
            self._sock.sendto(payload, (self.ip, self.port))

    def _drain(self) -> None:
        self._sock.setblocking(False)
        try:
            while True:
                self._sock.recvfrom(64)
        except OSError:
            pass
        finally:
            self._sock.settimeout(self.timeout)

    def reset_sequence(self) -> None:
        # No-op for raw VISCA (no sequence header). Kept for interface parity.
        pass

    # ---- control (payload bytes identical to the validated backend) ----
    def pan_tilt(self, pan_speed: int, tilt_speed: int, pan_dir: int, tilt_dir: int) -> None:
        ps = max(1, min(0x18, int(pan_speed)))     # 1..24
        ts = max(1, min(0x14, int(tilt_speed)))    # 1..20
        self._send(bytes([self.addr, 0x01, 0x06, 0x01, ps, ts,
                          pan_dir & 0xFF, tilt_dir & 0xFF, 0xFF]))

    def stop(self) -> None:
        self.pan_tilt(0x01, 0x01, PAN_STOP, TILT_STOP)

    def zoom(self, direction: str, speed: int = 0) -> None:
        if direction == "stop":
            p = 0x00
        elif direction == "tele":
            p = 0x20 | (speed & 0x07)
        elif direction == "wide":
            p = 0x30 | (speed & 0x07)
        else:
            return
        self._send(bytes([self.addr, 0x01, 0x04, 0x07, p, 0xFF]))

    def pan_tilt_absolute(self, pan_pos: int, tilt_pos: int,
                          pan_speed: int = 5, tilt_speed: int = 5) -> None:
        """Absolute pan/tilt positioning. pan_pos/tilt_pos are signed 16-bit
        encoder values (same unit as inquire_pan_tilt returns). Speeds 1..24 (pan)
        and 1..20 (tilt)."""
        ps = max(1, min(0x18, int(pan_speed)))
        ts = max(1, min(0x14, int(tilt_speed)))
        # Clamp to signed 16-bit
        pan = max(-0x8000, min(0x7FFF, int(pan_pos)))
        tilt = max(-0x8000, min(0x7FFF, int(tilt_pos)))
        # VISCA absolute position: 8x 01 06 02 VV WW 0Y0Y0Y0Y 0Z0Z0Z0Z FF
        # Each position = 4 nibbles (2 bytes), big-endian signed
        pan_unsigned = pan & 0xFFFF
        tilt_unsigned = tilt & 0xFFFF
        self._send(bytes([
            self.addr, 0x01, 0x06, 0x02, ps, ts,
            (pan_unsigned >> 12) & 0xF, (pan_unsigned >> 8) & 0xF,
            (pan_unsigned >> 4) & 0xF, pan_unsigned & 0xF,
            (tilt_unsigned >> 12) & 0xF, (tilt_unsigned >> 8) & 0xF,
            (tilt_unsigned >> 4) & 0xF, tilt_unsigned & 0xF,
            0xFF,
        ]))

    def zoom_absolute(self, zoom_pos: int) -> None:
        """Set absolute zoom position. zoom_pos is an unsigned 16-bit encoder
        value (0x0000=wide, 0x4000=max optical on Prisual)."""
        z = max(0, min(0xFFFF, int(zoom_pos)))
        self._send(bytes([
            self.addr, 0x01, 0x04, 0x47,
            (z >> 12) & 0xF, (z >> 8) & 0xF,
            (z >> 4) & 0xF, z & 0xF,
            0xFF,
        ]))

    def inquire_zoom(self) -> int | None:
        """Zoom position inquiry -> unsigned 16-bit encoder value, or None.
        Same drain-then-send pattern as inquire_pan_tilt — lock held only
        for the sendto, not the blocking recv loop."""
        self._drain()
        with self._lock:
            self._sock.sendto(bytes([self.addr, 0x09, 0x04, 0x47, 0xFF]),
                              (self.ip, self.port))
        for _ in range(4):
            try:
                data, _ = self._sock.recvfrom(64)
            except socket.timeout:
                break
            if len(data) >= 7 and data[0] == 0x90 and data[1] == 0x50:
                return ((data[2] << 12) | (data[3] << 8) |
                        (data[4] << 4) | data[5])
        return None

    def home(self) -> None:
        self._send(bytes([self.addr, 0x01, 0x06, 0x04, 0xFF]))

    def inquire_pan_tilt(self):
        """Pan/tilt position inquiry -> (pan_counts, tilt_counts) signed, or None.
        Raw reply: 90 50 0p 0p 0p 0p 0t 0t 0t 0t FF (no 8-byte header). Reads past
        stale ACK/completion frames. Lock held only for the sendto, not the blocking
        recv loop (same pattern as inquire_zoom)."""
        self._drain()
        with self._lock:
            self._sock.sendto(bytes([self.addr, 0x09, 0x06, 0x12, 0xFF]), (self.ip, self.port))
        for _ in range(4):
            try:
                data, _ = self._sock.recvfrom(64)
            except socket.timeout:
                break
            if len(data) >= 11 and data[0] == 0x90 and data[1] == 0x50:
                pan = (data[2] << 12) | (data[3] << 8) | (data[4] << 4) | data[5]
                tilt = (data[6] << 12) | (data[7] << 8) | (data[8] << 4) | data[9]
                if pan & 0x8000:
                    pan -= 0x10000
                if tilt & 0x8000:
                    tilt -= 0x10000
                return pan, tilt
        return None

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


class NullPtz:
    """Stand-in when ptz.enabled is false — accepts calls, does nothing."""
    def reset_sequence(self): pass
    def pan_tilt(self, *a, **k): pass
    def pan_tilt_absolute(self, *a, **k): pass
    def stop(self): pass
    def zoom(self, *a, **k): pass
    def zoom_absolute(self, *a, **k): pass
    def home(self): pass
    def inquire_pan_tilt(self): return None
    def inquire_zoom(self): return None
    def close(self): pass
