from __future__ import annotations

import sys
import types

from wavecam.controller import VisualServo
from wavecam.fusion import FusionResult
from wavecam.ptz_owner import PtzOwner


class DummyPtz:
    def __init__(self):
        self.calls = []

    def stop(self):
        self.calls.append(("stop",))

    def zoom(self, direction, speed=0):
        self.calls.append(("zoom", direction, speed))


def make_pipeline(cinematic_enabled=True):
    sys.modules.setdefault("cv2", types.SimpleNamespace())
    from wavecam.pipeline import Pipeline, SharedState

    pipe = Pipeline.__new__(Pipeline)
    ptz_cfg = types.SimpleNamespace(
        enabled=True,
        cinematic_zoom_enabled=cinematic_enabled,
        zoom_target_frac=0.5,
        zoom_deadband=0.06,
        zoom_max_speed=5,
        deadzone=0.08,
        max_pan_speed=10,
        max_tilt_speed=8,
        min_speed=1,
        command_min_interval=0.50,
        invert_pan=False,
        invert_tilt=False,
    )
    pipe.cfg = types.SimpleNamespace(ptz=ptz_cfg)
    pipe.ptz = DummyPtz()
    pipe.state = SharedState()
    pipe.servo = VisualServo(ptz_cfg)
    pipe.owner = PtzOwner()
    pipe.owner.request("testbed")
    pipe._last_zoom_key = None
    pipe._last_zoom_time = 0.0
    pipe._cinematic_zoom_suppressed_until = 0.0
    return pipe


def tracking(person_bbox):
    return FusionResult(
        target_xy=(320, 180),
        bbox=(0, 0, 40, 90),
        person_bbox=person_bbox,
        locked=True,
        state="TRACKING",
    )


def test_cinematic_zoom_is_default_off_and_sends_no_zoom_command():
    pipe = make_pipeline(cinematic_enabled=False)

    pipe._maybe_send_cinematic_zoom(tracking((0, 0, 40, 90)), 360)

    assert pipe.ptz.calls == []


def test_cinematic_zoom_uses_person_bbox_when_locked():
    pipe = make_pipeline()

    pipe._maybe_send_cinematic_zoom(tracking((0, 0, 40, 90)), 360)

    assert pipe.ptz.calls
    assert pipe.ptz.calls[0][0] == "zoom"
    assert pipe.ptz.calls[0][1] == "tele"
    assert pipe.ptz.calls[0][2] > 0


def test_cinematic_zoom_holds_on_color_only_bbox():
    pipe = make_pipeline()

    pipe._maybe_send_cinematic_zoom(tracking(None), 360)

    assert pipe.ptz.calls == [("zoom", "stop", 0)]


def test_cinematic_zoom_requires_locked_target():
    pipe = make_pipeline()
    fr = FusionResult(
        target_xy=(320, 180),
        bbox=(0, 0, 40, 90),
        person_bbox=(0, 0, 40, 90),
        locked=False,
        state="SEARCHING",
    )

    pipe._maybe_send_cinematic_zoom(fr, 360)

    assert pipe.ptz.calls == []


def test_cinematic_zoom_stops_when_lock_drops_after_active_zoom():
    pipe = make_pipeline()
    pipe._maybe_send_cinematic_zoom(tracking((0, 0, 40, 90)), 360)
    pipe.ptz.calls.clear()
    fr = FusionResult(
        target_xy=None,
        bbox=None,
        person_bbox=None,
        locked=False,
        state="SEARCHING",
    )

    pipe._maybe_send_cinematic_zoom(fr, 360)

    assert pipe.ptz.calls == [("zoom", "stop", 0)]


def test_manual_zoom_suppression_preserves_pan_tilt_owner():
    pipe = make_pipeline()

    pipe.suppress_cinematic_zoom(1.0)
    pipe._maybe_send_cinematic_zoom(tracking((0, 0, 40, 90)), 360)

    assert pipe.owner.owner == "testbed"
    assert pipe.ptz.calls == []


def test_manual_zoom_suppression_stops_active_cinematic_zoom():
    pipe = make_pipeline()
    pipe._maybe_send_cinematic_zoom(tracking((0, 0, 40, 90)), 360)
    pipe.ptz.calls.clear()

    pipe.suppress_cinematic_zoom(1.0)
    result = pipe._maybe_send_cinematic_zoom(tracking((0, 0, 40, 90)), 360)

    assert result == "manual_override"
    assert pipe.owner.owner == "testbed"
    assert pipe.ptz.calls == [("zoom", "stop", 0)]


def test_cinematic_zoom_uses_separate_rate_limit():
    pipe = make_pipeline()

    pipe._maybe_send_cinematic_zoom(tracking((0, 0, 40, 90)), 360)
    pipe._maybe_send_cinematic_zoom(tracking((0, 0, 40, 90)), 360)

    assert len(pipe.ptz.calls) == 1


if __name__ == "__main__":
    test_cinematic_zoom_is_default_off_and_sends_no_zoom_command()
    test_cinematic_zoom_uses_person_bbox_when_locked()
    test_cinematic_zoom_holds_on_color_only_bbox()
    test_cinematic_zoom_requires_locked_target()
    test_cinematic_zoom_stops_when_lock_drops_after_active_zoom()
    test_manual_zoom_suppression_preserves_pan_tilt_owner()
    test_manual_zoom_suppression_stops_active_cinematic_zoom()
    test_cinematic_zoom_uses_separate_rate_limit()
    print("CINEMATIC ZOOM TESTS PASSED")
