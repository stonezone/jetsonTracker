"""Estimator mode gates — pure helper, no I/O. (WaveCam Backend Plan v3, Phase 7)

Three modes determine whether the Kalman estimator merely observes, proposes
itself as a candidate, or is eligible to own PTZ:

    SHADOW   — observe only; never commands, never proposed (current behavior).
    PROPOSE  — run + propose "estimator" as an ownership candidate when the
               bearing std is within bounds and the FOV gate is satisfied.
    COMMAND  — eligible to own. Requires stable low bearing-std for N consecutive
               frames (command_stable_frames). Falls back to PROPOSE if the
               stability gate is lost.

Backward-compatible: config keys ``mode: str`` and ``shadow: bool`` are
resolved together. ``shadow=True → SHADOW``, ``shadow=False → PROPOSE``,
explicit mode string wins.

CONFIG KEYS (for wiring into EstimatorCfg):
    estimator.mode: str = "SHADOW"          # SHADOW | PROPOSE | COMMAND
    estimator.command_max_bearing_std_deg: float = 5.0  # bearing std gate
    estimator.command_stable_frames: int = 10           # consecutive stable frames required
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

SHADOW = "SHADOW"
PROPOSE = "PROPOSE"
COMMAND = "COMMAND"
VALID_MODES = {SHADOW, PROPOSE, COMMAND}


def resolve_mode(mode: Optional[str] = None, shadow: Optional[bool] = None) -> str:
    """Resolve config to a valid mode, backward-compatible with shadow bool.

    Precedence: explicit mode string > shadow bool > default SHADOW.
    """
    if mode is not None and mode.upper() in VALID_MODES:
        return mode.upper()
    if shadow is False:
        return PROPOSE
    return SHADOW


@dataclass
class ModeState:
    """Per-frame mode assessment. The pipeline reads ``eligible`` and
    ``proposed`` to decide whether to add "estimator" to the owner candidates."""

    mode: str = SHADOW
    eligible: bool = False   # estimator may be a PTZ owner candidate
    command_ready: bool = False  # COMMAND-mode stability gate satisfied
    bearing_std_deg: Optional[float] = None
    stable_frames: int = 0
    fov_populated: bool = False
    initialized: bool = False


def assess(
    mode: str,
    initialized: bool,
    bearing_std_deg: Optional[float],
    stable_frames: int,
    fov_populated: bool,
    max_bearing_std_deg: float = 5.0,
    min_stable_frames: int = 10,
) -> ModeState:
    """Assess estimator eligibility from the raw inputs.

    The pipeline calls this once per estimator tick and feeds the result
    into the owner resolver. The estimator itself is never consulted for
    PTZ authority directly — this function is the gate.
    """
    eligible = False
    command_ready = False

    if mode != SHADOW and initialized and fov_populated:
        if bearing_std_deg is not None and bearing_std_deg <= max_bearing_std_deg:
            eligible = True
            if mode == COMMAND and stable_frames >= min_stable_frames:
                command_ready = True

    return ModeState(
        mode=mode,
        eligible=eligible,
        command_ready=command_ready,
        bearing_std_deg=bearing_std_deg,
        stable_frames=stable_frames,
        fov_populated=fov_populated,
        initialized=initialized,
    )
