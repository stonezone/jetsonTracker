"""Estimator mode gate tests — pure, no I/O."""
import pytest

from wavecam.estimator_mode import (
    SHADOW, PROPOSE, COMMAND,
    ModeState, assess, resolve_mode,
)


# --- resolve_mode ---------------------------------------------------------

def test_resolve_defaults_to_shadow():
    assert resolve_mode() == SHADOW


def test_resolve_explicit_mode_wins():
    assert resolve_mode(mode="PROPOSE") == PROPOSE
    assert resolve_mode(mode="COMMAND") == COMMAND
    assert resolve_mode(mode="shadow") == SHADOW  # case-insensitive


def test_resolve_shadow_false_maps_to_propose():
    assert resolve_mode(shadow=False) == PROPOSE


def test_resolve_shadow_true_maps_to_shadow():
    assert resolve_mode(shadow=True) == SHADOW


def test_resolve_mode_wins_over_shadow():
    # explicit mode wins even when shadow is set
    assert resolve_mode(mode="PROPOSE", shadow=True) == PROPOSE
    assert resolve_mode(mode="COMMAND", shadow=False) == COMMAND


def test_resolve_unknown_mode_falls_back():
    assert resolve_mode(mode="UNKNOWN") == SHADOW
    assert resolve_mode(mode="UNKNOWN", shadow=False) == PROPOSE


# --- assess ------------------------------------------------------------------

def _eligible(**kw):
    defaults = dict(
        initialized=True, bearing_std_deg=2.0, stable_frames=15,
        fov_populated=True, max_bearing_std_deg=5.0, min_stable_frames=10,
    )
    defaults.update(kw)
    return assess(defaults.pop("mode", SHADOW), **defaults)


def test_shadow_never_eligible():
    s = _eligible(mode=SHADOW)
    assert not s.eligible
    assert not s.command_ready


def test_propose_eligible():
    s = _eligible(mode=PROPOSE)
    assert s.eligible
    assert not s.command_ready  # PROPSE can't command


def test_command_ready_when_stable():
    s = _eligible(mode=COMMAND, stable_frames=10, min_stable_frames=10)
    assert s.eligible
    assert s.command_ready


def test_command_not_ready_when_unstable():
    s = _eligible(mode=COMMAND, stable_frames=9, min_stable_frames=10)
    assert s.eligible
    assert not s.command_ready


def test_not_initialized_blocks_eligibility():
    s = _eligible(mode=PROPOSE, initialized=False)
    assert not s.eligible


def test_high_bearing_std_blocks_eligibility():
    s = _eligible(mode=PROPOSE, bearing_std_deg=8.0, max_bearing_std_deg=5.0)
    assert not s.eligible


def test_none_bearing_std_blocks_eligibility():
    s = _eligible(mode=PROPOSE, bearing_std_deg=None)
    assert not s.eligible


def test_empty_fov_blocks_eligibility():
    s = _eligible(mode=PROPOSE, fov_populated=False)
    assert not s.eligible


def test_command_requires_stable_plus_low_std():
    # both conditions: mode=COMMAND + stable + low bearing
    s = _eligible(mode=COMMAND, stable_frames=10, bearing_std_deg=3.0)
    assert s.command_ready


def test_command_high_std_no_command_ready():
    s = _eligible(mode=COMMAND, stable_frames=10, bearing_std_deg=8.0)
    assert not s.eligible
    assert not s.command_ready


def test_mode_state_dataclass_fields():
    s = ModeState(mode=SHADOW)
    assert s.eligible is False
    assert s.command_ready is False
    assert s.stable_frames == 0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"ESTIMATOR MODE TESTS PASSED ({len(fns)})")
