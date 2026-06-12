"""Typed contracts for the pointing-math seams.

The 2026-06-11 zombie bug — estimator calling a CameraPose method that only
test fakes possessed — was a type error that hand-rolled fakes concealed.
These Protocols make that bug class checkable: production classes conform
structurally, and test fakes declare conformance (``_: PoseLike = fake``)
so mypy validates BOTH sides of every seam.

Scope discipline: only the seams the pointing stack actually crosses.
Widening to other modules is a separate decision (see hardening plan T0.2).
"""
from __future__ import annotations

from typing import Optional, Protocol, Tuple, runtime_checkable


@runtime_checkable
class PoseLike(Protocol):
    """CameraPose as consumed by the estimator and GPS pointing math."""

    lat: float
    lon: float
    alt_m: float

    def pan_encoder_to_bearing(self, enc: float) -> Optional[float]: ...

    def bearing_to_pan_encoder(self, bearing_deg: float) -> float: ...

    def elevation_to_tilt_encoder(self, elev_deg: float) -> float: ...


@runtime_checkable
class GpsFixLike(Protocol):
    """The estimator's GPS observation: position plus freshness."""

    lat: float
    lon: float
    age_sec: float


@runtime_checkable
class EventsLike(Protocol):
    """The event ring as consumed by shadow/verifier paths."""

    def record(self, kind: str, detail: "str | dict") -> None: ...


@runtime_checkable
class PtzAbsoluteLike(Protocol):
    """The single capability PointingVerifier exercises on the camera."""

    def pan_tilt_absolute(self, pan_pos: int, tilt_pos: int,
                          pan_speed: int = ..., tilt_speed: int = ...) -> None: ...


@runtime_checkable
class PtzInquiryLike(Protocol):
    """What PtzState polls. inquire_* return None on timeout/garbage."""

    def inquire_pan_tilt(self) -> Optional[Tuple[int, int]]: ...

    def inquire_zoom(self) -> Optional[int]: ...


@runtime_checkable
class PtzStateLike(Protocol):
    """The poller cache as consumed by pipeline, verifier, and calibration."""

    def latest(self) -> Tuple[Optional[Tuple[int, int]], Optional[float]]: ...

    def is_alive(self) -> bool: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...
