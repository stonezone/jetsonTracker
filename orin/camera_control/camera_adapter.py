"""Camera control adapter interface + an in-memory mock backend.

Velocity moves are universal across backends. Absolute moves and position
read-back are optional capabilities (advertise via the supports_* flags).

Units: pan/tilt are RAW VISCA encoder units pre-calibration; zoom is raw encoder.
Mapping raw encoder <-> degrees/normalized is a Phase-3 concern (camera_pose +
PTZLimits). The control loop only needs a consistent, read-back-able space here.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class PTZPosition:
    pan: float
    tilt: float
    zoom: float


class CameraControlAdapter(ABC):
    # Capability flags; backends override.
    supports_position_readback: bool = False
    supports_absolute: bool = False

    @abstractmethod
    def connect(self) -> bool:
        ...

    @abstractmethod
    def close(self) -> None:
        ...

    @abstractmethod
    def pan_tilt_velocity(self, pan_vel: float, tilt_vel: float) -> None:
        """pan_vel, tilt_vel in [-1, 1]. ~0 stops that axis.
        Convention: +pan = right, +tilt = up."""

    @abstractmethod
    def zoom_velocity(self, vel: float) -> None:
        """vel in [-1, 1]: + = tele (in), - = wide (out); ~0 stops."""

    @abstractmethod
    def stop(self) -> None:
        ...

    # --- optional capabilities (raise if unsupported) ---
    def get_position(self) -> Optional[PTZPosition]:
        return None

    def move_absolute(self, pan: float, tilt: float,
                      pan_speed: float = 0.5, tilt_speed: float = 0.5) -> None:
        raise NotImplementedError("backend has no absolute move")

    def zoom_absolute(self, zoom: float) -> None:
        raise NotImplementedError("backend has no absolute zoom")

    def home(self) -> None:
        raise NotImplementedError("backend has no home")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()


class MockCameraAdapter(CameraControlAdapter):
    """In-memory PTZ for offline tests. Absolute jumps immediately; velocity is
    integrated by calling step(dt)."""

    supports_position_readback = True
    supports_absolute = True

    def __init__(self, pan: float = 0.0, tilt: float = 0.0, zoom: float = 0.0):
        self._pos = PTZPosition(pan, tilt, zoom)
        self._vel = [0.0, 0.0, 0.0]  # pan, tilt, zoom
        self.connected = False

    def connect(self) -> bool:
        self.connected = True
        return True

    def close(self) -> None:
        self.connected = False

    def pan_tilt_velocity(self, pan_vel: float, tilt_vel: float) -> None:
        self._vel[0], self._vel[1] = pan_vel, tilt_vel

    def zoom_velocity(self, vel: float) -> None:
        self._vel[2] = vel

    def stop(self) -> None:
        self._vel = [0.0, 0.0, 0.0]

    def get_position(self) -> Optional[PTZPosition]:
        return PTZPosition(self._pos.pan, self._pos.tilt, self._pos.zoom)

    def move_absolute(self, pan: float, tilt: float,
                      pan_speed: float = 0.5, tilt_speed: float = 0.5) -> None:
        self._pos.pan, self._pos.tilt = pan, tilt

    def zoom_absolute(self, zoom: float) -> None:
        self._pos.zoom = zoom

    def home(self) -> None:
        self._pos = PTZPosition(0.0, 0.0, 0.0)

    def step(self, dt: float = 0.1, scale: float = 1000.0) -> None:
        """Integrate velocity into position (for closed-loop offline tests)."""
        self._pos.pan += self._vel[0] * scale * dt
        self._pos.tilt += self._vel[1] * scale * dt
        self._pos.zoom = max(0.0, self._pos.zoom + self._vel[2] * scale * dt)
