"""Audit round-2 (2026-07-01), Agent A — pipeline & servo: R1, R2, R3, R4, R5, R6-A.

R1 (H6 defeats H7 for a moving subject): _gps_pointing_cmd recomputed lead_s from
fix.age_sec on EVERY frame even though the GPS reader hands back the SAME cached
fix (same .ts) between ~1 Hz LoRa packets and age_sec is recomputed live on every
get_fix() call. The absolute target crept 1-4 counts/frame, so _send_absolute_cmd
(H7's change-dedupe) saw a "new" target every frame -> 20+ changed sends/sec that
spammed pan_tilt_absolute at frame rate and reset the verifier's settle clock via
record_move() on every send. Fix: freeze lead_s to the value computed the first
time a given fix.ts is observed; _send_absolute_cmd additionally tolerates drift
up to POINTING_TOLERANCE_ENC counts as "unchanged".

R2 (H8 deadzone unbounded): controller.compute()'s degree-denominated deadzone
(cfg.deadzone / fov_scale) exceeds 1.0 at full tele with the checked-in
deadzone=0.08 (3.4/55 deg -> 1.29), so the servo can never leave the deadzone --
dead at exactly the long-range zooms this project exists for. Fix: cap the
scaled deadzone at 0.25.

R3 (Pipeline._stop shadows threading.Thread._stop): Thread.join() calls the
internal self._stop() from _wait_for_tstate_lock(); overwriting self._stop with
a threading.Event raised TypeError: 'Event' object is not callable, so every
clean SIGTERM/SIGINT shutdown (run.py's shutdown_pipeline -> pipe.join()) died
with a traceback. Renamed to _stop_evt (mirrors capture.py's M22 fix).

R4 (M5 stale-box skip misses absolute & manual pans): the M5 "camera panned
since these boxes were captured" check only looked at the velocity
_last_cmd_key, so GPS absolute slews and manual PTZ nudges never invalidated
the cached YOLO boxes. Fix: also compare _last_abs_cmd_time and
_last_manual_cmd_time against _last_boxes_time.

R5 (ViscaIP._send has no exception guard): a bare sendto() lets an OSError
(ENETUNREACH/EHOSTUNREACH on a camera-LAN drop) propagate out of any ptz.* call
in the vision loop -- including the C1 no-video stop and the KILL path's
ptz.stop() -- killing the pipeline thread (zombie rig). Fix: wrap in
try/except OSError, log/record once, swallow.

R6-A (coordinated w/ Agent B's R6-B): the base-drift monitor must read the RAW
instantaneous camera position (get_camera_position_raw), not the settled mean
(get_camera_position) that R6-B restored for calibration/pointing/snapshot
consumers -- a mean would itself mask the drift the monitor exists to detect.
Defensive getattr fallback so this is correct regardless of merge order.
"""
from __future__ import annotations

import time
import types

import pytest

from wavecam.controller import PtzAbsoluteCommand, VisualServo
from wavecam.events import EventRing
from wavecam.gps_stub import NormalizedFix
from wavecam.pipeline import Pipeline, SharedState
from wavecam.pointing_verifier import PointingVerifier
from wavecam.ptz_owner import PtzOwner
from wavecam.ptz_state import POINTING_TOLERANCE_ENC, VERIFY_DELAY_SEC
from wavecam.ptz_visca import ViscaIP


# ============================================================================
# R1 — lead_s freeze + tolerance-band dedupe in _send_absolute_cmd
# ============================================================================

BASE_LAT, BASE_LON = 21.6, -158.0


class _AbsPtz:
    def __init__(self):
        self.abs_calls = []
        self.calls = []

    def pan_tilt_absolute(self, pan, tilt, **kw):
        self.abs_calls.append((pan, tilt))

    def zoom_absolute(self, enc):
        self.calls.append(("zoom_abs", enc))

    def pan_tilt(self, *a):
        self.calls.append(("pan_tilt",) + a)

    def stop(self):
        self.calls.append("stop")

    def zoom(self, direction, speed=0):
        self.calls.append(("zoom", direction, speed))


def _gps_pipe():
    """A GPS-tracker-owning pipeline wired for _gps_pointing_cmd + _send_absolute_cmd,
    mirroring test_abs_move_gate.py's fixture plus a calibrated pose."""
    from wavecam.camera_pose import CameraPose

    pipe = Pipeline.__new__(Pipeline)
    pipe.cfg = types.SimpleNamespace(
        ptz=types.SimpleNamespace(enabled=True, command_min_interval=0.05,
                                  stop_resend_interval=0.25),
        gps=types.SimpleNamespace(max_pan_speed=4, max_tilt_speed=3, drive_zoom=False),
    )
    pose = CameraPose(lat=BASE_LAT, lon=BASE_LON, alt_m=2.0)
    pose.calibrate_pan_aim(enc=0.0, bearing_deg=0.0, enc_per_deg=14.4)
    pipe.pose = pose
    pipe.gps = None
    pipe.ptz = _AbsPtz()
    pipe.state = SharedState()
    pipe.owner = PtzOwner()
    pipe.owner.request("gps_tracker")
    pipe.events = EventRing()
    pipe.ptz_state = types.SimpleNamespace(latest=lambda: ((0, 0), 0.01),
                                           latest_zoom=lambda: (None, None))
    pipe._pointing_verifier = PointingVerifier(
        pipe.ptz, pipe.ptz_state, pipe.events,
        blocked=lambda: pipe.owner.killed or pipe.owner.owner != "gps_tracker")
    pipe._last_abs_cmd_key = None
    pipe._last_abs_cmd_time = 0.0
    pipe._last_lead_fix_ts = None
    pipe._frozen_lead_s = 0.0
    return pipe


def _moving_fix(t: float, fix_rate_hz: float = 1.0, speed: float = 8.0, course: float = 90.0):
    """Simulate the GPS reader's get_fix(): .ts (and lat/lon/speed/course) only
    change once per fix_rate_hz (~1 Hz LoRa cadence), but .age_sec is the LIVE
    seconds-since-that-ts -- recomputed on every call, exactly like the real
    reader -- even though the underlying fix is unchanged. This is the precise
    R1 scenario: a per-frame get_fix() call on a slowly-arriving fix stream."""
    period = 1.0 / fix_rate_hz
    fix_index = int(t // period)
    ts = fix_index * period
    age = t - ts
    return NormalizedFix(lat=BASE_LAT + 0.001, lon=BASE_LON, course=course,
                         speed=speed, ts=ts, age_sec=age, src="lora")


def test_lead_s_is_frozen_across_frames_of_the_same_fix():
    """Two calls with the SAME fix.ts but different (live-recomputed) age_sec
    must produce the IDENTICAL command -- the root fix for the per-frame creep."""
    pipe = _gps_pipe()
    fix_a = _moving_fix(t=0.1)   # ts=0.0, age=0.1
    fix_b = _moving_fix(t=0.9)   # ts=0.0 (same fix), age=0.9 -- would have crept
    cmd_a = pipe._gps_pointing_cmd(fix_a, calibration_valid=True)
    cmd_b = pipe._gps_pointing_cmd(fix_b, calibration_valid=True)
    assert cmd_a.pan_enc == cmd_b.pan_enc and cmd_a.tilt_enc == cmd_b.tilt_enc, \
        "lead_s must freeze to the value computed when this fix.ts was first seen"


def test_lead_s_updates_on_a_genuinely_new_fix():
    pipe = _gps_pipe()
    pipe._gps_pointing_cmd(_moving_fix(t=0.1), calibration_valid=True)
    frozen_after_first = pipe._frozen_lead_s
    pipe._gps_pointing_cmd(_moving_fix(t=1.5), calibration_valid=True)  # new ts=1.0
    assert pipe._frozen_lead_s != frozen_after_first, \
        "a new fix.ts must recompute (not keep freezing at the old fix's lead)"


def test_moving_subject_changed_sends_per_sec_bounded():
    """End-to-end through both R1 mechanisms (freeze + tolerance dedupe): drive
    _gps_pointing_cmd + _send_absolute_cmd at 35 Hz for 2s of sim time against a
    1 Hz-arriving GPS fix stream. Before the fix, the continuously-recomputed
    age_sec crept the target every frame -> ~70 changed sends over 2s (35/sec).
    After: at most one changed send per NEW fix arrival."""
    pipe = _gps_pipe()
    fps = 35.0
    duration_s = 2.0
    n_frames = int(duration_s * fps)
    for i in range(n_frames):
        t = i / fps
        fix = _moving_fix(t)
        cmd = pipe._gps_pointing_cmd(fix, calibration_valid=True)
        assert cmd is not None
        pipe._send_absolute_cmd(cmd)
    sends_per_sec = len(pipe.ptz.abs_calls) / duration_s
    assert sends_per_sec <= 2.0, \
        f"changed-sends/sec must drop to <=~2 for a moving subject, got {sends_per_sec}"


def test_verifier_settle_clock_survives_the_moving_subject_loop():
    """The whole point of H7 (and why R1 matters): the verifier must still be
    able to fire even while _gps_pointing_cmd is being called every frame."""
    pipe = _gps_pipe()
    # Encoder reports a position far from any GPS target -> every issued move misses.
    pipe.ptz_state = types.SimpleNamespace(latest=lambda: ((-9999, -9999), 0.01),
                                           latest_zoom=lambda: (None, None))
    pipe._pointing_verifier = PointingVerifier(
        pipe.ptz, pipe.ptz_state, pipe.events,
        blocked=lambda: pipe.owner.killed or pipe.owner.owner != "gps_tracker")

    fps = 35.0
    for i in range(int(0.9 * fps)):   # under the fix's 1 Hz cadence -> same fix
        t = i / fps
        cmd = pipe._gps_pointing_cmd(_moving_fix(t), calibration_valid=True)
        pipe._send_absolute_cmd(cmd)
    v = pipe._pointing_verifier
    issue_t = v._issue_t
    assert issue_t is not None, "the first send must have recorded a pending verify"

    # Force the settle window to have elapsed, then tick: the verifier must
    # still be able to fire (its settle clock wasn't smashed every frame).
    v._issue_t = time.time() - VERIFY_DELAY_SEC - 0.1
    v.tick()
    assert any(e["kind"] == "pointing_miss" for e in pipe.events.since(0)), \
        "the verifier must be able to fire despite per-frame _gps_pointing_cmd calls"


def test_small_drift_within_tolerance_is_not_a_change():
    pipe = _gps_pipe()
    pipe._send_absolute_cmd(PtzAbsoluteCommand(pan_enc=1000, tilt_enc=0))
    within = PtzAbsoluteCommand(pan_enc=1000 + POINTING_TOLERANCE_ENC,
                                tilt_enc=POINTING_TOLERANCE_ENC)
    pipe._send_absolute_cmd(within)
    assert pipe.ptz.abs_calls == [(1000, 0)], \
        "drift within POINTING_TOLERANCE_ENC must be treated as unchanged"


def test_drift_beyond_tolerance_is_a_change():
    pipe = _gps_pipe()
    pipe._send_absolute_cmd(PtzAbsoluteCommand(pan_enc=1000, tilt_enc=0))
    beyond = PtzAbsoluteCommand(pan_enc=1000 + POINTING_TOLERANCE_ENC + 5, tilt_enc=0)
    pipe._send_absolute_cmd(beyond)
    assert pipe.ptz.abs_calls == [(1000, 0), (1000 + POINTING_TOLERANCE_ENC + 5, 0)], \
        "drift beyond POINTING_TOLERANCE_ENC must still be sent"


# ============================================================================
# R2 — capped FOV-scaled deadzone (controller.py)
# ============================================================================

W, H = 640, 360
WIDE_HFOV = 55.0
TELE_HFOV = 3.4


def test_uncapped_deadzone_would_exceed_unity_at_tele():
    """Pin the defect this finding describes, so the cap's necessity stays
    documented: the checked-in deadzone (0.08) divided by the tele fov_scale
    exceeds 1.0 -- i.e. without a cap NO on-screen error could ever leave the
    deadzone at full tele."""
    fov_scale = TELE_HFOV / WIDE_HFOV
    uncapped_dz = 0.08 / fov_scale
    assert uncapped_dz > 1.0


def test_capped_deadzone_moves_at_full_tele():
    cfg = types.SimpleNamespace(deadzone=0.08, max_pan_speed=20, max_tilt_speed=20,
                                min_speed=1, invert_pan=False, invert_tilt=False)
    servo = VisualServo(cfg)
    target = (W * 0.75, H / 2.0)   # ex = 0.5: a mid-frame-edge target
    cmd = servo.compute(target, (W, H), hfov_deg=TELE_HFOV, hfov_ref_deg=WIDE_HFOV)
    assert not cmd.is_stop, \
        "a mid-frame-edge target must produce a non-zero pan command at full tele"
    assert cmd.pan_speed >= 1


def test_capped_deadzone_still_centers_a_small_error():
    """The cap must not reopen the original (pre-H8) hunting problem: an error
    smaller than the capped 0.25 deadzone still holds still."""
    cfg = types.SimpleNamespace(deadzone=0.08, max_pan_speed=20, max_tilt_speed=20,
                                min_speed=1, invert_pan=False, invert_tilt=False)
    servo = VisualServo(cfg)
    target = (W / 2.0 + 10, H / 2.0)   # tiny error
    cmd = servo.compute(target, (W, H), hfov_deg=TELE_HFOV, hfov_ref_deg=WIDE_HFOV)
    assert cmd.is_stop


# ============================================================================
# R3 — Pipeline._stop renamed to _stop_evt (no longer shadows Thread._stop)
# ============================================================================

def _minimal_real_pipeline(ptz_enabled: bool = False):
    """A REAL Pipeline (via __init__, not __new__) with a NO_VIDEO-forever fake
    grabber, so pipe.start()/pipe.join() exercise the actual threading.Thread
    lifecycle -- the only way to reproduce the R3 collision (a wrapper thread
    around a bound method, as several older tests use, never calls Thread._stop()
    on the Pipeline instance itself)."""
    class _NullPtz:
        def stop(self): pass
        def zoom(self, *a, **k): pass

    cfg = types.SimpleNamespace(
        camera=types.SimpleNamespace(source=0, use_gstreamer=False, codec="h264",
                                     reconnect_sec=1.0),
        ptz=types.SimpleNamespace(enabled=ptz_enabled, command_min_interval=0.05,
                                  stop_resend_interval=0.25),
        color=types.SimpleNamespace(enabled=False),
        detector=types.SimpleNamespace(enabled=False, every_n=1, box_ttl_sec=0.2),
        fusion=types.SimpleNamespace(
            lock_threshold=0.6, unlock_threshold=0.35, require_person=False,
            match_dist=120, person_aim_x=0.5, person_aim_y=0.5, ema_alpha=0.5,
            lost_grace_sec=0.8, gps_boost=0.2, gps_boost_radius_frac=0.25),
        gps=types.SimpleNamespace(lock_frames=1, grace_sec=1.0, drive_stale_sec=8.0,
                                  max_pan_speed=4, max_tilt_speed=3, drive_zoom=False),
        web=types.SimpleNamespace(jpeg_quality=80, show_hud=True),
        loop=types.SimpleNamespace(target_fps=100.0, log_every_sec=100),
    )
    pipe = Pipeline(cfg, _NullPtz(), detector_factory=lambda: None)
    pipe.grab = types.SimpleNamespace(start=lambda: None, read=lambda: None,
                                      connected=False, stop=lambda: None, frames=0)
    return pipe


def test_join_after_stop_does_not_raise_type_error():
    """R3: self._stop (a threading.Event) used to shadow threading.Thread._stop(),
    the internal method _wait_for_tstate_lock() calls from join() once the thread
    state lock is released -- calling the Event like a function raised
    TypeError: 'Event' object is not callable, so run.py's shutdown_pipeline
    (pipe.stop(); pipe.join()) died instead of exiting cleanly on SIGTERM/SIGINT."""
    pipe = _minimal_real_pipeline()
    pipe.start()
    time.sleep(0.2)
    pipe.stop()
    pipe.join(timeout=3)   # must not raise
    assert not pipe.is_alive()


def test_stop_evt_attribute_name():
    """Direct pin of the rename (mirrors capture.py's _stop_evt naming): the
    INSTANCE dict must not contain "_stop" -- that's exactly what shadowed
    threading.Thread._stop (a real method on the class itself, hence this
    checks __dict__, not hasattr)."""
    pipe = _minimal_real_pipeline()
    assert hasattr(pipe, "_stop_evt")
    assert "_stop" not in pipe.__dict__


# ============================================================================
# R4 — M5 stale-box skip also covers absolute & manual pans
# ============================================================================

def _boxpanning_pipe():
    pipe = Pipeline.__new__(Pipeline)
    pipe._last_cmd_key = None
    pipe._last_cmd_time = 0.0
    pipe._last_abs_cmd_time = 0.0
    pipe._last_manual_cmd_time = 0.0
    return pipe


def _panning(pipe, boxes_time):
    """Reproduce the exact predicate from pipeline.py's detector block."""
    from wavecam.controller import STOP_CMD
    return (
        (pipe._last_cmd_key is not None
         and pipe._last_cmd_key != STOP_CMD.key()
         and pipe._last_cmd_time >= boxes_time)
        or pipe._last_abs_cmd_time >= boxes_time
        or pipe._last_manual_cmd_time >= boxes_time
    )


def test_absolute_move_after_box_capture_marks_panning():
    pipe = _boxpanning_pipe()
    boxes_time = 100.0
    pipe._last_abs_cmd_time = 100.5   # GPS absolute slew AFTER the boxes were cached
    assert _panning(pipe, boxes_time), \
        "a GPS absolute slew since the boxes were captured must skip their reuse"


def test_manual_move_after_box_capture_marks_panning():
    pipe = _boxpanning_pipe()
    boxes_time = 100.0
    pipe._last_manual_cmd_time = 100.5   # manual PTZ nudge AFTER the boxes were cached
    assert _panning(pipe, boxes_time)


def test_no_motion_since_capture_is_not_panning():
    pipe = _boxpanning_pipe()
    boxes_time = 100.0
    pipe._last_abs_cmd_time = 99.0    # predates the boxes
    pipe._last_manual_cmd_time = 50.0  # predates the boxes
    assert not _panning(pipe, boxes_time)


def test_record_manual_cmd_time_sets_the_timestamp():
    pipe = Pipeline.__new__(Pipeline)
    pipe._last_manual_cmd_time = 0.0
    pipe.record_manual_cmd_time(t=123.5)
    assert pipe._last_manual_cmd_time == 123.5


# ============================================================================
# R5 — ViscaIP._send swallows OSError instead of propagating
# ============================================================================

class _RaisingSocket:
    def __init__(self, exc):
        self._exc = exc

    def sendto(self, *a, **k):
        raise self._exc

    def settimeout(self, *a, **k):
        pass


def _visca_with_raising_socket(exc):
    v = ViscaIP("192.168.100.88", port=1259)
    v._sock = _RaisingSocket(exc)
    return v


def test_send_swallows_os_error():
    v = _visca_with_raising_socket(OSError("ENETUNREACH"))
    v.stop()          # must not raise
    v.zoom("stop")    # must not raise


def test_send_swallows_os_error_on_the_kill_path():
    """R5's stated worst case: the KILL path's ptz.stop() must not propagate and
    kill the pipeline thread when the camera LAN is down."""
    v = _visca_with_raising_socket(OSError(101, "Network is unreachable"))
    try:
        v.stop()
        v.zoom("stop")
    except OSError:
        pytest.fail("ptz.stop()/zoom('stop') must swallow a transient OSError")


def test_send_only_logs_once_per_outage(capsys):
    v = _visca_with_raising_socket(OSError("ENETUNREACH"))
    for _ in range(5):
        v.stop()
    out = capsys.readouterr().out
    assert out.count("sendto failed") == 1, "the outage must be logged once, not per-send"


# ============================================================================
# R6-A — base-drift monitor uses the raw camera position (getattr fallback)
# ============================================================================

class _GpsWithRaw:
    """Both variants present (Agent B has landed R6-B) -- raw must win."""

    def __init__(self):
        self.mean_calls = 0
        self.raw_calls = 0

    def get_camera_position(self):
        self.mean_calls += 1
        return (21.60001, -158.00001, 2.0)   # the settled mean

    def get_camera_position_raw(self):
        self.raw_calls += 1
        return (21.60005, -158.00003, 2.0)   # the instantaneous fix

    def get_camera_age(self):
        return 0.5

    def get_camera_age_raw(self):
        return 0.05


class _GpsMeanOnly:
    """Defensive fallback: an older/partial gps object with only the mean API
    (e.g. if Agent B's change hasn't landed yet) must still work."""

    def get_camera_position(self):
        return (21.60001, -158.00001, 2.0)

    def get_camera_age(self):
        return 0.5


def _drift_pipe(gps):
    from wavecam.base_drift import BaseDriftMonitor
    from wavecam.camera_pose import CameraPose

    pipe = Pipeline.__new__(Pipeline)
    pipe.cfg = types.SimpleNamespace(
        gps=types.SimpleNamespace(base_drift_enabled=True),
    )
    pipe.gps = gps
    pose = CameraPose(lat=BASE_LAT, lon=BASE_LON, alt_m=2.0)  # has_base is derived
    pose.base_locked = True
    pipe.pose = pose
    pipe.events = EventRing()
    pipe._base_drift = BaseDriftMonitor(threshold_m=4.0, min_trend_m=2.0,
                                        window_size=10, min_consecutive=5,
                                        max_fix_age_sec=10.0, min_sats=0)
    pipe._base_drift_interval_sec = 0.0   # always due
    pipe._base_drift_last_run = 0.0
    pipe._base_drift_latched_at = None
    pipe._base_drift_last_result = None
    return pipe


def test_base_drift_prefers_raw_camera_position_when_available():
    gps = _GpsWithRaw()
    pipe = _drift_pipe(gps)
    pipe._update_base_drift(now=1000.0)
    assert gps.raw_calls == 1
    assert gps.mean_calls == 0, \
        "the drift monitor must use the RAW fix, not the settled mean (R6-A)"


def test_base_drift_falls_back_to_mean_when_raw_is_absent():
    gps = _GpsMeanOnly()
    pipe = _drift_pipe(gps)
    pipe._update_base_drift(now=1000.0)   # must not raise AttributeError
