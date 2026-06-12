# Plan 2 — Closed-Loop Pointing

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`
> (recommended) or `superpowers:executing-plans` to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Prisual PTZ camera encoder feedback — a periodic, non-blocking
`inquire_pan_tilt` poller that caches last-known position + staleness, a
verify-and-resend step after every absolute move, and `/status.ptz` fields that
expose `pan_enc / tilt_enc / enc_age_sec`. The pipeline loop's vision-servoing
behavior is **unchanged**; the poller is additive telemetry until the Phase 4
estimator consumes it.

**Architecture:**

```
PtzState (new thread)            Pipeline (existing thread)
─────────────────────────────    ─────────────────────────────────────────
poll_loop()                      run()
  ├─ every 1/POLL_HZ s:            ├─ _send_absolute_cmd(cmd)
  │    inquire_pan_tilt()  ──────► │    ptz.pan_tilt_absolute(...)
  │    (lock held for sendto        └─ _verify_absolute_move(...)  ← NEW
  │     only, recv outside)              └─ PtzState.latest() → (enc, age)
  └─ _cache: (pan, tilt, t)              └─ if |enc-target| > TOLERANCE:
       (threading.Lock, atomic)                 ptz.pan_tilt_absolute(...)
                                                events.record("pointing_miss")
                                          └─ health.beat("ptz_poller", ...)
```

**Interaction with the existing socket-lock discipline:**
`ViscaIP._lock` is held **only** for `sendto` (see `ptz_visca.py:135-137` —
drain is outside the lock, recv loop is outside the lock). `PtzState` follows
the same discipline: it calls `ptz.inquire_pan_tilt()` which internally drains +
sends (with lock) + recvs (without lock). The pipeline never calls
`inquire_pan_tilt` on the hot path, so there is no cross-thread recv
contention. `PtzState` never issues move commands — it is read-only.

**Tech Stack:** Python 3.10+ / threading / pytest (backend). No new deps.

**Ground rules:** stage files explicitly; never `git add -A`; failing test first;
never weaken a test; commit messages explain why; end commits with
`Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

**Gate satisfied by:** pre-water bundle deployed (Plan 1 done) + M1 field
session complete. Branch from `main` after M1 merges.

---

## Bench Parameters

> **Sanctioned placeholder policy (one exception, documented here):**
> The four constants below are the ONLY deliberate placeholders in this plan.
> Every other code fragment, file path, and command is final. The bench-fill
> task (Task 0) is the first deliverable and must be completed before any code
> task begins. A reviewer scanning for `FILL_FROM_BENCH` can confirm scope.

| Constant | Candidate | Filled by bench? | Description |
|---|---|---|---|
| `POLL_HZ` | 2 / 5 / 10 | **10** (0.0% loss, p95 81.8ms — bench 2026-06-11) | Inquiry rate that keeps reply loss below `REPLY_LOSS_PCT` without starving the socket |
| `REPLY_LATENCY_P95_MS` | — | **82** | 95th-percentile round-trip for `inquire_pan_tilt` under 10Hz velocity commands |
| `REPLY_LOSS_PCT` | — | **0.0** (at 10Hz; 0.5% at 2Hz) | Fraction of inquiry sends that receive no valid reply in one timeout window |
| `INTERLEAVE_OBSERVED` | — | **True** (5964 events in 300s at 10Hz) | `True` if ACK/completion bytes ever arrived interleaved with inquiry replies on the shared socket |

**Bench procedure (requires camera powered, Orin reachable):**

```bash
# Run from orin/wavecam/ on the Orin (or Mac with VISCA reachable)
python3 - <<'EOF'
import socket, time, statistics, collections

CAMERA_IP = "192.168.100.88"
CAMERA_PORT = 1259

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(0.3)

def pan_tilt_velocity(ps=8, ts=6):
    """Alternate left/right to keep the camera moving."""
    return bytes([0x81, 0x01, 0x06, 0x01, ps, ts, 0x02, 0x03, 0xFF])

def inq_pan_tilt():
    return bytes([0x81, 0x09, 0x06, 0x12, 0xFF])

def drain():
    sock.setblocking(False)
    try:
        while True: sock.recvfrom(64)
    except OSError: pass
    finally: sock.settimeout(0.3)

results = {2: [], 5: [], 10: []}
DURATION_SEC = 300   # 5 minutes per rate
CMD_HZ = 10

for poll_hz in (2, 5, 10):
    print(f"\n=== poll_hz={poll_hz} for {DURATION_SEC}s ===")
    latencies = []
    sent = lost = interleave = 0
    t_end = time.time() + DURATION_SEC
    t_cmd = time.time()
    t_poll = time.time()
    direction = 0x02  # PAN_RIGHT

    while time.time() < t_end:
        now = time.time()

        # 10 Hz velocity commands (fire-and-forget, no reply expected)
        if now - t_cmd >= 1.0 / CMD_HZ:
            direction = 0x01 if direction == 0x02 else 0x02
            sock.sendto(pan_tilt_velocity(8 if direction == 0x02 else 1, 6),
                        (CAMERA_IP, CAMERA_PORT))
            t_cmd = now

        # POLL_HZ inquiry
        if now - t_poll >= 1.0 / poll_hz:
            drain()
            sock.sendto(inq_pan_tilt(), (CAMERA_IP, CAMERA_PORT))
            t_send = time.time()
            sent += 1
            got_reply = False
            for _ in range(4):
                try:
                    data, _ = sock.recvfrom(64)
                    rt = (time.time() - t_send) * 1000
                    if len(data) >= 11 and data[0] == 0x90 and data[1] == 0x50:
                        latencies.append(rt)
                        got_reply = True
                        break
                    else:
                        interleave += 1  # non-position frame arrived first
                except socket.timeout:
                    break
            if not got_reply:
                lost += 1
            t_poll = now

        time.sleep(0.001)

    # stop camera
    sock.sendto(bytes([0x81, 0x01, 0x06, 0x01, 1, 1, 0x03, 0x03, 0xFF]),
                (CAMERA_IP, CAMERA_PORT))
    p95 = statistics.quantiles(latencies, n=100)[94] if latencies else None
    results[poll_hz] = {"sent": sent, "lost": lost, "loss_pct": 100*lost/max(1,sent),
                        "p95_ms": p95, "interleave_events": interleave}
    print(f"  sent={sent} lost={lost} loss%={100*lost/max(1,sent):.1f} "
          f"p95={p95:.1f}ms interleave={interleave}")

print("\n=== SUMMARY ===")
for hz, r in results.items():
    print(f"  {hz}Hz: loss={r['loss_pct']:.1f}% p95={r['p95_ms']}ms interleave={r['interleave_events']}")
print("\nRECOMMENDED POLL_HZ: pick the highest Hz with loss% < 5 and p95 < 100ms")
sock.close()
EOF
```

**How to fill the table:** from the script output, choose `POLL_HZ` as the
highest rate where loss < 5% and p95 < 100ms. Set `INTERLEAVE_OBSERVED = True`
if `interleave_events > 0` at the chosen rate. Copy all four values into the
`ptz_state.py` module constants (Task 1, Step 2) before writing any other code.

---

## Task 0 — Run the bench protocol and fill the parameter table

**Files:** bench script output only (not committed); `orin/wavecam/wavecam/ptz_state.py`
gets the filled constants in Task 1.

**Owner:** Zack (camera powered) + Claude (drives the script).

- [ ] **Step 0.1 — Run the bench script** (above) against the live camera.
  Requires: camera on, Orin reachable at `192.168.100.88:1259`.
  Expected: script runs 15 minutes total (3 × 5 min), prints a summary table.

- [ ] **Step 0.2 — Fill the Bench Parameters table** in this document. Replace
  each `FILL_FROM_BENCH` cell with the measured value. Commit this document with
  the table filled before writing any code:

  ```bash
  git add docs/superpowers/plans/2026-06-12-closed-loop-pointing.md
  git commit -m "$(cat <<'EOF'
  docs(plan2): fill bench parameter table from measured camera data

  POLL_HZ, REPLY_LATENCY_P95_MS, REPLY_LOSS_PCT, INTERLEAVE_OBSERVED filled
  from the 5-min-per-rate inquiry stress test against the live Prisual.
  These constants gate all implementation tasks.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 1 — `PtzState` poller class

**Files:**
- Create: `orin/wavecam/wavecam/ptz_state.py`
- Create: `orin/wavecam/tests/test_ptz_state.py`

### Step 1.1 — Write the failing tests

```python
# orin/wavecam/tests/test_ptz_state.py
"""Tests for the PtzState background poller.

Uses a fake transport: a callable that returns controlled values on demand,
without any real socket. The fake is deliberately simple — it only needs to
exercise the cache, staleness logic, and interleave/lost-reply handling.
"""
import threading
import time
import types

from wavecam.ptz_state import PtzState


def _make_fake_ptz(replies):
    """Returns a NullPtz-compatible object whose inquire_pan_tilt pops from
    the replies list. None = simulated timeout/loss."""
    q = list(replies)
    obj = types.SimpleNamespace(
        inquire_pan_tilt=lambda: q.pop(0) if q else None,
        inquire_zoom=lambda: None,
    )
    return obj


def test_cache_is_none_before_first_reply():
    ptz = _make_fake_ptz([None])
    ps = PtzState(ptz, poll_hz=100)
    enc, age = ps.latest()
    assert enc is None and age is None


def test_cache_holds_latest_valid_reply():
    ptz = _make_fake_ptz([(1000, -200), (1500, -250)])
    ps = PtzState(ptz, poll_hz=100)
    ps._poll_once()
    enc, age = ps.latest()
    assert enc == (1000, -200)
    assert age is not None and age < 0.5


def test_lost_reply_leaves_cache_intact():
    ptz = _make_fake_ptz([(500, 100), None])
    ps = PtzState(ptz, poll_hz=100)
    ps._poll_once()       # caches (500, 100)
    ps._poll_once()       # reply lost — cache must survive
    enc, age = ps.latest()
    assert enc == (500, 100)


def test_age_grows_after_last_valid():
    ptz = _make_fake_ptz([(0, 0)])
    ps = PtzState(ptz, poll_hz=100)
    ps._poll_once()
    time.sleep(0.05)
    _, age = ps.latest()
    assert age is not None and age >= 0.04


def test_start_stop_thread():
    ptz = _make_fake_ptz([(0, 0)] * 200)
    ps = PtzState(ptz, poll_hz=50)
    ps.start()
    time.sleep(0.1)
    ps.stop()
    enc, age = ps.latest()
    assert enc is not None
```

### Step 1.2 — Run — must fail on import

```bash
cd orin/wavecam && python3 -m pytest tests/test_ptz_state.py -q
```

Expected: `ModuleNotFoundError: No module named 'wavecam.ptz_state'`.

### Step 1.3 — Implement `ptz_state.py`

```python
"""PtzState — background pan/tilt encoder poller.

Owns a dedicated poll_loop thread that calls inquire_pan_tilt() at POLL_HZ.
Exposes latest() as a non-blocking lock read of the cached snapshot. Never
issues move commands; never blocks the pipeline or API threads.

Socket-lock discipline (matches ptz_visca.py):
  ViscaIP._lock is held for sendto only. The recv loop runs outside the lock.
  PtzState calls ptz.inquire_pan_tilt() which follows this discipline internally.
  Because PtzState is the ONLY caller of inquire_pan_tilt() (the pipeline loop
  does not call it), there is no concurrent recv contention.

Bench-measured constants (fill from Task 0 before shipping):
"""
from __future__ import annotations

import threading
import time
from typing import Optional, Tuple

# ── Bench parameters ─────────────────────────────────────────────────────────
# Fill these from the Task 0 bench run before merging. See plan header.
POLL_HZ: float = 10.0              # bench 2026-06-11: 0.0% loss, p95 81.8ms at 10Hz
REPLY_LATENCY_P95_MS: float = 82.0 # bench 2026-06-11 (under 10Hz velocity traffic)
REPLY_LOSS_PCT: float = 0.0        # bench 2026-06-11: zero loss at 10Hz (2982 sent)
INTERLEAVE_OBSERVED: bool = True    # bench 2026-06-11: 5964 non-pos frames in 300s at 10Hz
# ─────────────────────────────────────────────────────────────────────────────

# Position tolerance for verify-and-resend (encoder counts).
# ±15 counts ≈ ±3.4° at 4.47 counts/deg — coarse enough to survive motor
# backlash, tight enough to catch a failed absolute move.
POINTING_TOLERANCE_ENC: int = 30   # bench 2026-06-11: post-slew hunt wanders ±30 counts

# How long to wait after issuing an absolute command before reading back
# the encoder. Must exceed the camera's settle time.
# Set conservatively here; tune down after bench validation.
VERIFY_DELAY_SEC: float = 0.5


class PtzState:
    """Background encoder-position cache. One instance per pipeline."""

    def __init__(self, ptz, poll_hz: float = POLL_HZ):
        self._ptz = ptz
        self._poll_hz = poll_hz
        self._lock = threading.Lock()
        self._enc: Optional[Tuple[int, int]] = None   # (pan, tilt) counts
        self._ts: Optional[float] = None              # time of last valid reply
        self._thread: Optional[threading.Thread] = None
        self._stop_ev = threading.Event()

    # ── public API (non-blocking, safe from any thread) ──────────────────────

    def latest(self) -> Tuple[Optional[Tuple[int, int]], Optional[float]]:
        """Return (enc, age_sec) where enc=(pan,tilt) or None if no reply yet.
        age_sec is seconds since the last valid reply, or None."""
        with self._lock:
            if self._enc is None:
                return None, None
            return self._enc, time.time() - self._ts

    def start(self) -> None:
        """Start the background poll thread. Idempotent."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_ev.clear()
        self._thread = threading.Thread(
            target=self._poll_loop, name="ptz-poller", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the poll thread to exit and join (blocks ≤ 2 poll periods)."""
        self._stop_ev.set()
        if self._thread:
            self._thread.join(timeout=2.0 / max(0.1, self._poll_hz) * 2)

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ── internal ─────────────────────────────────────────────────────────────

    def _poll_once(self) -> None:
        """Single inquiry cycle. Called by the poll loop and directly in tests."""
        result = self._ptz.inquire_pan_tilt()
        if result is not None:
            with self._lock:
                self._enc = result
                self._ts = time.time()

    def _poll_loop(self) -> None:
        period = 1.0 / max(0.1, self._poll_hz)
        while not self._stop_ev.is_set():
            t0 = time.time()
            try:
                self._poll_once()
            except Exception as e:
                # Log but do not crash — a transient UDP failure must not kill
                # the poller; it will retry next cycle.
                print(f"[ptz_state] poll error: {e}")
            dt = time.time() - t0
            wait = period - dt
            if wait > 0:
                self._stop_ev.wait(wait)
```

### Step 1.4 — Tests pass

```bash
cd orin/wavecam && python3 -m pytest tests/test_ptz_state.py -q
```

Expected: `5 passed`. Full suite unchanged:

```bash
python3 -m pytest -q
```

Expected: all prior tests still green, count grows by 5.

### Step 1.5 — Commit

```bash
git add orin/wavecam/wavecam/ptz_state.py orin/wavecam/tests/test_ptz_state.py
git commit -m "$(cat <<'EOF'
feat: PtzState background encoder poller — additive telemetry, no behavior change

Periodic inquire_pan_tilt() at POLL_HZ on its own thread; lock-guarded
(pan,tilt,age) cache; non-blocking latest() for the pipeline and status
snapshot. Bench constants filled from Task 0. Never issues move commands.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2 — Wire `PtzState` into `pipeline.py`

**Files:**
- Modify: `orin/wavecam/wavecam/pipeline.py`
- Modify: `orin/wavecam/tests/test_pipeline_ptz_state.py` (create)

**Constraint:** No change to vision-servoing logic. `PtzState` starts alongside
the pipeline; its `latest()` is only read for telemetry and (Task 3)
verify-and-resend. The pipeline's `_send_cmd` / `_send_absolute_cmd` paths are
untouched.

### Step 2.1 — Failing tests

```python
# orin/wavecam/tests/test_pipeline_ptz_state.py
"""Verify that Pipeline wires up PtzState and exposes it correctly."""
import types, threading
from unittest.mock import MagicMock
# Import the minimal pipeline fixture pattern from the existing test suite:
# DummyPipeline is defined in test_control_api.py; here we test the real
# Pipeline class with a null PTZ so we can inspect ptz_state after init.
from wavecam.pipeline import Pipeline
from wavecam.ptz_state import PtzState


def _null_cfg():
    """Minimal cfg that satisfies Pipeline.__init__ without a real camera."""
    ptz_cfg = types.SimpleNamespace(
        enabled=False, command_min_interval=0.05,
        stop_resend_interval=0.25, cinematic_zoom_enabled=False,
        zoom_target_frac=0.35, zoom_deadband=0.02, zoom_max_speed=4,
        invert_pan=False, invert_tilt=False, deadzone=0.1,
        max_pan_speed=12, max_tilt_speed=9, min_speed=1, ff_gain=0.2,
        ff_deadzone_mult=1.5,
    )
    return types.SimpleNamespace(
        camera=types.SimpleNamespace(url="", reconnect_interval=5),
        color=types.SimpleNamespace(enabled=False),
        detector=types.SimpleNamespace(enabled=False, every_n=3, box_ttl_sec=0.3),
        fusion=types.SimpleNamespace(
            lock_threshold=0.6, unlock_threshold=0.35, require_person=False,
            match_dist=120, person_aim_x=0.5, person_aim_y=0.5, ema_alpha=0.5,
            lost_grace_sec=0.8, gps_boost=0.2, gps_boost_radius_frac=0.25,
        ),
        ptz=ptz_cfg,
        gps=types.SimpleNamespace(
            lock_frames=5, grace_sec=1.0, stale_threshold_sec=10.0,
            max_pan_speed=4, max_tilt_speed=3, drive_zoom=False,
        ),
        loop=types.SimpleNamespace(target_fps=30, log_every_sec=10),
        web=types.SimpleNamespace(jpeg_quality=80, show_hud=True),
    )


def _null_ptz():
    from wavecam.ptz_visca import NullPtz
    return NullPtz()


def test_pipeline_has_ptz_state_after_init():
    p = Pipeline(_null_cfg(), _null_ptz(), lambda: None)
    assert hasattr(p, "ptz_state"), "Pipeline must have a ptz_state attribute"
    assert isinstance(p.ptz_state, PtzState)


def test_ptz_state_latest_returns_none_before_start():
    p = Pipeline(_null_cfg(), _null_ptz(), lambda: None)
    enc, age = p.ptz_state.latest()
    assert enc is None and age is None


def test_ptz_state_not_started_when_ptz_disabled():
    p = Pipeline(_null_cfg(), _null_ptz(), lambda: None)
    # disabled PTZ — poller exists but its thread must NOT auto-start
    assert not p.ptz_state.is_alive()
```

### Step 2.2 — Run — fails (no `ptz_state` attribute)

```bash
cd orin/wavecam && python3 -m pytest tests/test_pipeline_ptz_state.py -q
```

### Step 2.3 — Add `ptz_state` to `Pipeline.__init__`

In `pipeline.py`, after `self.events = EventRing(maxlen=500)` (~line 94), add:

```python
        # PtzState — background encoder poller. Started in run() only when
        # ptz.enabled is True. Additive telemetry; does not affect the servo.
        from .ptz_state import PtzState
        self.ptz_state = PtzState(self.ptz)
```

In `Pipeline.run()`, after `self.grab.start()` (line 259), add:

```python
        if self.cfg.ptz.enabled:
            self.ptz_state.start()
```

In `Pipeline.stop()` (the existing method at line 452), before `self.grab.stop()`:

```python
        self.ptz_state.stop()
```

### Step 2.4 — Tests pass; full suite green

```bash
python3 -m pytest tests/test_pipeline_ptz_state.py tests/test_ptz_state.py -q
python3 -m pytest -q
```

### Step 2.5 — Commit

```bash
git add orin/wavecam/wavecam/pipeline.py orin/wavecam/tests/test_pipeline_ptz_state.py
git commit -m "$(cat <<'EOF'
feat: wire PtzState into Pipeline — encoder poller starts with ptz.enabled

Additive change only: poller thread starts/stops with the pipeline, never
commands the camera, does not touch the vision-servo path. latest() is
available for telemetry and Task 3's verify-and-resend.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3 — Verify-and-resend after absolute moves

Absolute moves (`pan_tilt_absolute`) are issued by `_send_absolute_cmd` in the
GPS-tracker owner path. After the settle delay, read the encoder back via
`ptz_state.latest()`; if the pointing error exceeds `POINTING_TOLERANCE_ENC`
on either axis, issue one retry and log a `pointing_miss` event. A second miss
on the same move is logged but not retried (avoids oscillation).

**Files:**
- Create: `orin/wavecam/wavecam/pointing_verifier.py`
- Create: `orin/wavecam/tests/test_pointing_verifier.py`
- Modify: `orin/wavecam/wavecam/pipeline.py` (call `_verify_absolute_move`)

### Step 3.1 — Failing tests

```python
# orin/wavecam/tests/test_pointing_verifier.py
"""State-machine tests for verify-and-resend.

The verifier is a stateful object that:
  1. Records the target when an absolute move is issued.
  2. On next _tick() call (called once per pipeline loop after VERIFY_DELAY_SEC),
     reads the encoder from ptz_state.latest().
  3. If error > POINTING_TOLERANCE_ENC on either axis, issues one retry and
     emits a pointing_miss event.
  4. If a second tick still misses, emits pointing_miss again but does NOT retry.
"""
import time
import types
from wavecam.pointing_verifier import PointingVerifier
from wavecam.ptz_state import POINTING_TOLERANCE_ENC, VERIFY_DELAY_SEC


def _mock_ptz():
    calls = []
    obj = types.SimpleNamespace(
        pan_tilt_absolute=lambda pan, tilt, **kw: calls.append(("abs", pan, tilt)),
        _calls=calls,
    )
    return obj


def _mock_ptz_state(enc):
    """Returns a PtzState-compatible object whose latest() always returns enc."""
    return types.SimpleNamespace(latest=lambda: (enc, 0.01))


def _mock_events():
    recorded = []
    return types.SimpleNamespace(
        record=lambda kind, detail: recorded.append((kind, detail)),
        _recorded=recorded,
    )


def test_no_action_when_within_tolerance():
    ptz = _mock_ptz()
    ps = _mock_ptz_state((1000, -100))
    ev = _mock_events()
    v = PointingVerifier(ptz, ps, ev)
    v.record_move(pan_enc=1000, tilt_enc=-100, t=time.time() - VERIFY_DELAY_SEC - 0.1)
    v.tick()
    assert not ptz._calls
    assert not ev._recorded


def test_miss_triggers_retry_and_event():
    ptz = _mock_ptz()
    ps = _mock_ptz_state((500, -100))   # far from target
    ev = _mock_events()
    v = PointingVerifier(ptz, ps, ev)
    v.record_move(pan_enc=1000, tilt_enc=-100, t=time.time() - VERIFY_DELAY_SEC - 0.1)
    v.tick()
    assert len(ptz._calls) == 1
    assert ptz._calls[0] == ("abs", 1000, -100)
    assert any(k == "pointing_miss" for k, _ in ev._recorded)


def test_second_miss_logs_but_does_not_retry():
    ptz = _mock_ptz()
    ps = _mock_ptz_state((500, -100))
    ev = _mock_events()
    v = PointingVerifier(ptz, ps, ev)
    t_issue = time.time() - VERIFY_DELAY_SEC - 0.1
    v.record_move(pan_enc=1000, tilt_enc=-100, t=t_issue)
    v.tick()           # first miss → retry
    ptz._calls.clear()
    v.record_move(pan_enc=1000, tilt_enc=-100, t=time.time() - VERIFY_DELAY_SEC - 0.1)
    v.tick()           # second miss → log only
    assert not ptz._calls
    miss_events = [d for k, d in ev._recorded if k == "pointing_miss"]
    assert len(miss_events) == 2


def test_no_tick_before_settle_time():
    ptz = _mock_ptz()
    ps = _mock_ptz_state((500, -100))
    ev = _mock_events()
    v = PointingVerifier(ptz, ps, ev)
    v.record_move(pan_enc=1000, tilt_enc=-100, t=time.time())   # just issued
    v.tick()   # settle time not elapsed
    assert not ptz._calls
    assert not ev._recorded


def test_stale_encoder_skips_verify():
    """If ptz_state has no encoder data yet, verification is silently skipped."""
    ptz = _mock_ptz()
    ps = types.SimpleNamespace(latest=lambda: (None, None))
    ev = _mock_events()
    v = PointingVerifier(ptz, ps, ev)
    v.record_move(pan_enc=1000, tilt_enc=-100, t=time.time() - VERIFY_DELAY_SEC - 0.1)
    v.tick()
    assert not ptz._calls
    assert not ev._recorded
```

### Step 3.2 — Run — fails on import

```bash
cd orin/wavecam && python3 -m pytest tests/test_pointing_verifier.py -q
```

### Step 3.3 — Implement `pointing_verifier.py`

```python
"""PointingVerifier — verify-and-resend for absolute pan/tilt moves.

Called once per pipeline loop tick. After VERIFY_DELAY_SEC has elapsed since
an absolute move, reads the encoder from PtzState. If the pointing error on
either axis exceeds POINTING_TOLERANCE_ENC, issues one retry and logs a
pointing_miss event. A second failure logs again but does not retry (avoids
oscillation while the camera is still settling or obstructed).
"""
from __future__ import annotations

import time
from typing import Optional, Tuple

from .ptz_state import POINTING_TOLERANCE_ENC, VERIFY_DELAY_SEC


class PointingVerifier:
    def __init__(self, ptz, ptz_state, events):
        self._ptz = ptz
        self._ptz_state = ptz_state
        self._events = events
        self._target: Optional[Tuple[int, int]] = None
        self._issue_t: Optional[float] = None
        self._retry_count: int = 0

    def record_move(self, pan_enc: int, tilt_enc: int, t: float | None = None) -> None:
        """Call immediately after issuing an absolute pan/tilt command."""
        self._target = (pan_enc, tilt_enc)
        self._issue_t = t if t is not None else time.time()
        self._retry_count = 0

    def tick(self) -> None:
        """Call once per pipeline loop. Verifies and retries if conditions are met."""
        if self._target is None or self._issue_t is None:
            return
        if (time.time() - self._issue_t) < VERIFY_DELAY_SEC:
            return  # camera still settling

        enc, age = self._ptz_state.latest()
        if enc is None:
            return  # no encoder data yet — skip silently

        pan_target, tilt_target = self._target
        pan_actual, tilt_actual = enc
        pan_err = abs(pan_actual - pan_target)
        tilt_err = abs(tilt_actual - tilt_target)

        if pan_err <= POINTING_TOLERANCE_ENC and tilt_err <= POINTING_TOLERANCE_ENC:
            self._target = None   # success — clear pending verify
            return

        detail = (f"pan_err={pan_err} tilt_err={tilt_err} "
                  f"target=({pan_target},{tilt_target}) "
                  f"actual=({pan_actual},{tilt_actual}) "
                  f"retry={self._retry_count}")
        self._events.record("pointing_miss", detail)

        if self._retry_count == 0:
            self._ptz.pan_tilt_absolute(pan_target, tilt_target)
            self._retry_count += 1
            self._issue_t = time.time()   # reset settle clock for the retry
        else:
            # Second miss — give up on this move; next GPS command will reissue.
            self._target = None
```

### Step 3.4 — Wire into `pipeline.py`

In `Pipeline.__init__`, after `self.ptz_state = PtzState(self.ptz)`:

```python
        from .pointing_verifier import PointingVerifier
        self._pointing_verifier = PointingVerifier(
            self.ptz, self.ptz_state, self.events
        )
```

In `Pipeline._send_absolute_cmd`, after the `self.ptz.pan_tilt_absolute(...)` call
(~line 198 in the current file):

```python
            self._pointing_verifier.record_move(
                pan_enc=cmd.pan_enc, tilt_enc=cmd.tilt_enc
            )
```

At the end of each loop iteration in `Pipeline.run()`, just before
`self.health.beat("loop")` (~line 427):

```python
            self._pointing_verifier.tick()
```

### Step 3.5 — Health beat for the poller

In `Pipeline.run()`, inside the loop, after `self.health.beat("detector", ...)`:

```python
            enc, enc_age = self.ptz_state.latest()
            self.health.beat("ptz_poller", {
                "alive": self.ptz_state.is_alive(),
                "enc": enc,
                "age_sec": round(enc_age, 3) if enc_age is not None else None,
            })
```

### Step 3.6 — Tests pass; full suite green

```bash
python3 -m pytest tests/test_pointing_verifier.py -q
python3 -m pytest -q
```

### Step 3.7 — Commit

```bash
git add orin/wavecam/wavecam/pointing_verifier.py \
        orin/wavecam/tests/test_pointing_verifier.py \
        orin/wavecam/wavecam/pipeline.py
git commit -m "$(cat <<'EOF'
feat: verify-and-resend after absolute moves + pointing_miss event

After VERIFY_DELAY_SEC, read the encoder back from PtzState. If error
exceeds POINTING_TOLERANCE_ENC on either axis, issue one retry and log
pointing_miss to the event ring. A second failure logs but does not retry
to avoid oscillation. GPS owner path is the only caller; vision-servo is
unaffected.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4 — `/status.ptz` gains encoder fields

`build_ptz()` in `control_api.py` currently returns `owner`, `enabled`,
`pan_tilt_cmd`, `zoom_state`. Add `pan_enc`, `tilt_enc`, `enc_age_sec` drawn
from `pipeline.ptz_state.latest()`. All three are `null` when the poller has no
data yet (camera off, disabled PTZ) — iOS reads them with `decodeIfPresent`.

**Files:**
- Modify: `orin/wavecam/wavecam/control_api.py` (`build_ptz` function)
- Create: `orin/wavecam/tests/test_status_ptz_enc.py`
- Modify: `orin/wavecam/tools/regen_api_snapshot.py` (to pick up `pan_enc` in the
  snapshot — the snapshot is path-only so no content change, but re-run to confirm
  no new paths appeared)

### Step 4.1 — Failing tests

```python
# orin/wavecam/tests/test_status_ptz_enc.py
"""Verify that /status.ptz carries encoder fields from PtzState."""
import types
from fastapi.testclient import TestClient
from test_control_api import DummyPipeline
from wavecam.web import build_app
from wavecam.ptz_state import PtzState


def _client_with_enc(enc_value):
    """Build a test client whose pipeline.ptz_state returns enc_value."""
    pipeline = DummyPipeline()
    # Inject a PtzState-compatible stub
    pipeline.ptz_state = types.SimpleNamespace(
        latest=lambda: (enc_value, 0.05 if enc_value else None)
    )
    return TestClient(build_app(pipeline))


def test_enc_fields_null_when_no_data():
    client = _client_with_enc(None)
    r = client.get("/api/v1/status")
    assert r.status_code == 200
    ptz = r.json()["ptz"]
    assert ptz["pan_enc"] is None
    assert ptz["tilt_enc"] is None
    assert ptz["enc_age_sec"] is None


def test_enc_fields_populated_when_data_available():
    client = _client_with_enc((1234, -567))
    r = client.get("/api/v1/status")
    ptz = r.json()["ptz"]
    assert ptz["pan_enc"] == 1234
    assert ptz["tilt_enc"] == -567
    assert isinstance(ptz["enc_age_sec"], float)
    assert ptz["enc_age_sec"] >= 0.0


def test_existing_ptz_fields_unchanged():
    """Confirm additive-only — existing keys must still be present."""
    client = _client_with_enc(None)
    ptz = client.get("/api/v1/status").json()["ptz"]
    for key in ("owner", "enabled", "pan_tilt_cmd", "zoom_state"):
        assert key in ptz, f"existing field '{key}' must survive the change"
```

### Step 4.2 — Run — fails (enc fields absent)

```bash
cd orin/wavecam && python3 -m pytest tests/test_status_ptz_enc.py -q
```

### Step 4.3 — Extend `build_ptz` in `control_api.py`

Find `build_ptz` (~line 2026). Replace with:

```python
def build_ptz(legacy: dict, pipeline) -> dict:
    cfg_enabled = getattr(pipeline.cfg.ptz, "enabled", False)
    ptz_state = getattr(pipeline, "ptz_state", None)
    if ptz_state is not None:
        enc, enc_age = ptz_state.latest()
    else:
        enc, enc_age = None, None
    return {
        "owner": str(legacy.get("owner", IDLE)),
        "enabled": bool(legacy.get("ptz_enabled", cfg_enabled)),
        "pan_tilt_cmd": legacy.get("cmd"),
        "zoom_state": str(legacy.get("zoom_cmd", "hold")),
        "pan_enc": enc[0] if enc is not None else None,
        "tilt_enc": enc[1] if enc is not None else None,
        "enc_age_sec": round(enc_age, 3) if enc_age is not None else None,
    }
```

Also add `ptz_state` to `DummyPipeline` in `tests/test_control_api.py` so all
existing status tests keep working. Append one line after the class attributes:

```python
        self.ptz_state = types.SimpleNamespace(latest=lambda: (None, None))
```

(Check whether `DummyPipeline` is a dataclass or plain class — append in
`__init__` if it has one; as a class attribute otherwise.)

### Step 4.4 — Tests pass; full suite green

```bash
python3 -m pytest tests/test_status_ptz_enc.py -q
python3 -m pytest -q
```

Confirm no regression in existing status/contract tests.

### Step 4.5 — iOS display note

`/status.ptz` now carries `pan_enc: Int?`, `tilt_enc: Int?`, `enc_age_sec: Double?`.
The iOS `WCPtz` DTO should decode them with `decodeIfPresent` so old backends
(pre-Plan 2) produce `nil` rather than a decode failure. Display is optional —
a two-line addition to the Tune or Connect view is sufficient for field
diagnostics; full estimator UI belongs in Plan 3. No iOS code change is required
to ship this plan; the DTO extension is a follow-on in the Plan 3 iOS task.

### Step 4.6 — Commit

```bash
git add orin/wavecam/wavecam/control_api.py orin/wavecam/tests/test_status_ptz_enc.py \
        orin/wavecam/tests/test_control_api.py
git commit -m "$(cat <<'EOF'
feat: /status.ptz gains pan_enc/tilt_enc/enc_age_sec from PtzState

Additive change: three new nullable fields in the ptz object, null when the
poller has no data. Existing fields and iOS decode contracts unchanged.
The estimator (Plan 3) will consume these fields for bearing observations.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5 — HealthRegistry integration for poller events

`pointing_miss` and a poller-health beat are already wired in Tasks 3 and 3.5.
This task adds a named test that verifies the full path from a simulated pointing
miss to the `/health` response, and confirms the event ring contains the record.

**Files:**
- Create: `orin/wavecam/tests/test_plan2_integration.py`

### Step 5.1 — Integration tests

```python
# orin/wavecam/tests/test_plan2_integration.py
"""End-to-end integration: pointing_miss → events ring + health beat.

These tests drive PointingVerifier and HealthRegistry directly (no HTTP),
confirming the full signal path from a missed absolute move to observable
telemetry. No real camera needed.
"""
import time
import types
from wavecam.pointing_verifier import PointingVerifier
from wavecam.ptz_state import VERIFY_DELAY_SEC, POINTING_TOLERANCE_ENC
from wavecam.health import HealthRegistry
from wavecam.events import EventRing


def _setup():
    ptz_calls = []
    ptz = types.SimpleNamespace(
        pan_tilt_absolute=lambda pan, tilt, **kw: ptz_calls.append((pan, tilt)),
        _calls=ptz_calls,
    )
    health = HealthRegistry()
    events = EventRing(maxlen=100)
    # PtzState stub: encoder far from any reasonable target
    ptz_state_stub = types.SimpleNamespace(latest=lambda: ((0, 0), 0.01))
    verifier = PointingVerifier(ptz, ptz_state_stub, events)
    return ptz, health, events, verifier


def test_pointing_miss_appears_in_event_ring():
    ptz, health, events, verifier = _setup()
    verifier.record_move(pan_enc=2000, tilt_enc=500,
                         t=time.time() - VERIFY_DELAY_SEC - 0.1)
    verifier.tick()
    ring = events.since(0)
    kinds = [e["kind"] for e in ring]
    assert "pointing_miss" in kinds


def test_health_beat_after_poller_poll():
    """Simulate a pipeline loop: beat the health registry for ptz_poller,
    confirm the registry reports it as fresh."""
    _, health, _, _ = _setup()
    enc = (500, -100)
    health.beat("ptz_poller", {"alive": True, "enc": enc, "age_sec": 0.02})
    snap = health.snapshot(stale_after_sec=5.0)
    assert snap["components"]["ptz_poller"]["ok"] is True
    assert snap["components"]["ptz_poller"]["detail"]["enc"] == enc


def test_stale_poller_beat_flips_health_not_ok():
    """If the poller thread dies silently, /health goes not-ok."""
    _, health, _, _ = _setup()
    health.beat("ptz_poller", {"alive": True, "enc": (0, 0), "age_sec": 0.0})
    # Simulate stale beat by back-dating the timestamp
    health._last["ptz_poller"] = (time.time() - 10.0,
                                   health._last["ptz_poller"][1])
    snap = health.snapshot(stale_after_sec=5.0)
    assert snap["components"]["ptz_poller"]["ok"] is False
    assert snap["ok"] is False


def test_pointing_miss_detail_includes_error_magnitudes():
    ptz, _, events, verifier = _setup()
    verifier.record_move(pan_enc=3000, tilt_enc=0,
                         t=time.time() - VERIFY_DELAY_SEC - 0.1)
    verifier.tick()
    miss = next(e for e in events.since(0) if e["kind"] == "pointing_miss")
    assert "pan_err=" in miss["detail"]
    assert "tilt_err=" in miss["detail"]
```

### Step 5.2 — Run — must pass (no new code needed; tests rely on Tasks 1-4)

```bash
cd orin/wavecam && python3 -m pytest tests/test_plan2_integration.py -q
```

Expected: `4 passed`.

### Step 5.3 — Full suite green

```bash
python3 -m pytest -q
```

### Step 5.4 — Commit

```bash
git add orin/wavecam/tests/test_plan2_integration.py
git commit -m "$(cat <<'EOF'
test: Plan 2 integration — pointing_miss → event ring + health beat path verified

Four tests covering the full signal chain without a real camera: miss event
appears in the ring with error magnitudes, poller health beat is fresh/stale
correctly, stale poller makes /health not-ok. No new production code.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6 — Deploy and on-rig verification

**Owner:** Claude-supervised; Zack/agent authorization required to deploy.

- [ ] **Step 6.1 — Full local suite green**

  ```bash
  cd orin/wavecam && python3 -m pytest -q
  ```

  Expected: all tests pass; suite count reflects Tasks 1-5 additions. Review
  `git log --oneline` to confirm all five task commits are present.

- [ ] **Step 6.2 — Dry-run deploy**

  ```bash
  ./orin/wavecam/deploy.sh --dry-run
  ```

  Inspect the rsync output. Confirm `ptz_state.py` and `pointing_verifier.py`
  appear. Confirm `camera_pose.json` and `auth.json` are excluded.

- [ ] **Step 6.3 — Authorized deploy**

  ```bash
  ./orin/wavecam/deploy.sh
  ```

  Expected: `DEPLOY OK: <sha> live`. Record the sha.

- [ ] **Step 6.4 — On-rig spot-checks**

  ```bash
  # Encoder telemetry in status:
  curl -s http://orin:8088/api/v1/status | python3 -c \
    'import json,sys; p=json.load(sys.stdin)["ptz"]; print(p)'
  # Expected: pan_enc/tilt_enc/enc_age_sec present (null if camera off, int if on)

  # Poller shows up in health:
  curl -s http://orin:8088/api/v1/health | python3 -c \
    'import json,sys; h=json.load(sys.stdin); print(h["components"].get("ptz_poller"))'

  # Trigger a GPS absolute move (requires calibrated pose + GPS fix), then:
  curl -s http://orin:8088/api/v1/events | python3 -c \
    'import json,sys; [print(e) for e in json.load(sys.stdin)["events"] if e["kind"]=="pointing_miss"]'
  # Expected: empty (good pointing) or pointing_miss entries with error details

  # KILL-during-inquiry timing safety: kill the service during active polling,
  # verify it restarts cleanly and the poller thread reconnects.
  ssh orin 'sudo systemctl restart wavecam.service'
  sleep 15
  curl -s http://orin:8088/api/v1/health | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["ok"])'
  # Expected: True
  ```

- [ ] **Step 6.5 — Update memory and emit collab status**

  ```bash
  python3 .agent-collab/bin/collab.py emit \
    --from claude --to codex \
    --type status \
    --summary "Plan 2 closed-loop-pointing deployed. PtzState poller live at POLL_HZ Hz. pointing_miss events in /events. /status.ptz carries pan_enc/tilt_enc/enc_age_sec. M3 achieved."
  ```

---

## Execution map

| Order | Task | Prerequisite | Owner |
|---|---|---|---|
| 0 | Bench protocol — fill parameter table | Camera powered | JOINT (Zack + Claude) |
| 1 | `PtzState` class + tests | Task 0 done (constants filled) | SONNET (Claude-gated) |
| 2 | Wire into `pipeline.py` | Task 1 | SONNET |
| 3 | Verify-and-resend + `pointing_miss` | Task 2 | SONNET |
| 4 | `/status.ptz` encoder fields | Task 2 | SONNET |
| 5 | Integration tests | Tasks 3+4 | SONNET |
| 6 | Deploy + on-rig verification | Tasks 1-5, Zack auth | Claude-supervised |

**Parallelizable:** Tasks 3 and 4 are independent after Task 2; they can run in
parallel (separate branches, merge before Task 5).

**M3 criterion:** `curl http://orin:8088/api/v1/status | jq .ptz.pan_enc` returns
an integer (not null) while the camera is powered. That is the estimator's
prereq #1: the camera reports where it actually points.
