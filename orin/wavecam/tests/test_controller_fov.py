"""H8 (audit 2026-07-01): FOV gain-scheduling in the visual servo.

At full tele (~3.4 deg HFOV) the normalized deadzone was +/-0.085 deg — even
pan speed 1 crossed it faster than the video+command latency chain, so the
camera limit-cycled around the subject at exactly the long-range zooms this
project exists for. compute() now takes (hfov_deg, hfov_ref_deg): speed scales
by hfov/hfov_ref and the deadzone is constant in DEGREES (cfg.deadzone is its
normalized value at the WIDEST FOV).

Invariant pinned first: at the reference FOV (or with the args omitted) the
command is identical to the legacy path — current wide tuning is unchanged.
"""
from __future__ import annotations

from types import SimpleNamespace

from wavecam.controller import VisualServo

BASE = dict(deadzone=0.10, max_pan_speed=20, max_tilt_speed=20, min_speed=1,
            invert_pan=False, invert_tilt=False)
W, H = 640, 360
WIDE = 55.0
TELE = 3.4


def _servo():
    return VisualServo(SimpleNamespace(**BASE))


def test_reference_fov_is_byte_identical_to_legacy():
    for target in [(480, 180), (600, 300), (330, 180), None]:
        legacy = _servo().compute(target, (W, H))
        scheduled = _servo().compute(target, (W, H),
                                     hfov_deg=WIDE, hfov_ref_deg=WIDE)
        assert scheduled == legacy, f"wide-FOV behavior changed for {target}"


def test_no_fov_args_is_byte_identical_to_legacy():
    legacy = _servo().compute((480, 180), (W, H))
    unscheduled = _servo().compute((480, 180), (W, H),
                                   hfov_deg=None, hfov_ref_deg=None)
    assert unscheduled == legacy


def test_tele_scales_speed_down():
    target = (600, 180)   # large pan error
    wide_cmd = _servo().compute(target, (W, H), hfov_deg=WIDE, hfov_ref_deg=WIDE)
    mid_cmd = _servo().compute(target, (W, H), hfov_deg=WIDE / 2, hfov_ref_deg=WIDE)
    assert not wide_cmd.is_stop and not mid_cmd.is_stop
    assert mid_cmd.pan_speed < wide_cmd.pan_speed, \
        "half the FOV covers half the degrees — speed must scale down"


def test_full_tele_saturates_at_min_speed():
    cmd = _servo().compute((620, 180), (W, H), hfov_deg=TELE, hfov_ref_deg=WIDE)
    if not cmd.is_stop:
        assert cmd.pan_speed == BASE["min_speed"]


def test_deadzone_is_denominated_in_degrees():
    """An error that clears the deadzone at wide sits INSIDE the same angular
    deadzone at tele (0.10 normalized-at-wide = 2.75 deg; at hfov=27.5 the
    normalized deadzone doubles to 0.20)."""
    target = (368, 180)   # ex = 0.15: outside 0.10, inside 0.20
    wide_cmd = _servo().compute(target, (W, H), hfov_deg=WIDE, hfov_ref_deg=WIDE)
    tele_cmd = _servo().compute(target, (W, H), hfov_deg=WIDE / 2, hfov_ref_deg=WIDE)
    assert not wide_cmd.is_stop, "0.15 error must move at wide (legacy behavior)"
    assert tele_cmd.is_stop, \
        "the same pixel error spans fewer degrees at tele — inside the deadzone"


def test_tilt_axis_is_scheduled_too():
    target = (320, 350)   # pure tilt error
    wide_cmd = _servo().compute(target, (W, H), hfov_deg=WIDE, hfov_ref_deg=WIDE)
    mid_cmd = _servo().compute(target, (W, H), hfov_deg=WIDE / 3, hfov_ref_deg=WIDE)
    assert not wide_cmd.is_stop
    if not mid_cmd.is_stop:
        assert mid_cmd.tilt_speed < wide_cmd.tilt_speed
