# WaveCam Backend Enhancement Plan v2 — Deterministic Control Architecture

**Project root:** `/Users/zackjordan/code/jetsonTracker`  
**Scope:** `orin/wavecam/wavecam/*` and `orin/wavecam/tests/*`  
**Goal:** camera follows me automatically and keeps me framed while foil-surfing 50–300 m offshore.

This plan is a **control-architecture-first refactor**. The five feature enhancements from Plan v1 are still the deliverables, but they are now wrapped in a strict single-authority contract so they cannot fight each other.

---

## 0. Core contract (must hold for every frame)

### 0.1 Single-authority rule
At any frame, **exactly one module** may issue PTZ commands. All others are observers, advisors, or candidates only.

### 0.2 Ownership priority (total order)
Highest wins:

1. **KILL** — hard stop, overrides all
2. **CALIBRATE** — manual control, blocks all autonomy
3. **MANUAL** — joystick / API override
4. **ESTIMATOR** — Kalman-filter predicted track (when in `COMMAND` mode)
5. **GPS_TRACKER** — absolute GPS pointing
6. **VISION_FOLLOW** — relative visual servo
7. **IDLE** — hold position

`testbed` is retired as an owner; it becomes an alias for the idle/auto-start path.

### 0.3 Command dispatch rule
Only the resolved owner may produce a `PtzCommand` or `PtzAbsoluteCommand`. Every other module returns `None` or advisory data only.

### 0.4 Absolute-command exclusivity
Only one absolute control source may drive per frame. Today that is `GPS_TRACKER`; `ESTIMATOR` (when enabled) will also produce absolute commands, but the resolver guarantees only one owns at a time.

### 0.5 Safety override stack
- `KILL` → immediate STOP, owner → IDLE.
- `CALIBRATE` → blocks all autonomous starts and manual takeovers.
- Manual nudges → allowed only when owner is IDLE or MANUAL.

### 0.6 Frame budget guard
- Target budget: `system.frame_budget_ms` (default 33 ms for 30 FPS).
- If `dt > budget`:
  1. Skip estimator tick.
  2. Skip tracker (ByteTrack/BoT-SORT) update; reuse last boxes.
  3. Skip fusion cue updates.
  4. Skip non-critical logging.
- Always preserve: last stable PTZ command and KILL handling.

### 0.7 Fusion never controls PTZ
Fusion returns confidence, cues, and ROIs only. It never issues commands.

### 0.8 System output contract (per frame)
Every frame emits:
```python
{
  "owner": str,
  "ptz_command": PtzCommand | PtzAbsoluteCommand | None,
  "fusion_debug": dict,
  "gps_state": dict,
  "estimator_state": dict,
  "timing": {"dt_ms": float, "budget_ms": float, "overrun": bool},
}
```

---

## 1. Control-architecture refactor (prerequisite for all features)

These changes must land **before** the five feature enhancements.

### 1.1 Centralize PTZ command dispatch

**Problem:** `Pipeline`, `PtzDispatcher` (`control_ptz.py`), `CalibrationManager` (`control_calibration.py`), and `PointingVerifier` all call PTZ I/O directly.

**Change:** Introduce `PtzDispatcher` owned by `Pipeline`. All command sources produce a candidate command object; only `Pipeline._dispatch_ptz()` writes to hardware.

**Files:**
- `orin/wavecam/wavecam/ptz_dispatcher.py` (new)
- `orin/wavecam/wavecam/pipeline.py`
- `orin/wavecam/wavecam/control_ptz.py`
- `orin/wavecam/wavecam/control_calibration.py`
- `orin/wavecam/wavecam/pointing_verifier.py`

**Design:**
```python
class PtzCommand:
    source: Literal["kill", "calibrate", "manual", "estimator", "gps_tracker", "vision_follow", "idle"]
    kind: Literal["relative", "absolute", "stop"]
    pan_speed: int | None
    tilt_speed: int | None
    pan_dir: int | None
    tilt_dir: int | None
    pan_enc: int | None
    tilt_enc: int | None
    zoom_enc: int | None
```

- `control_ptz.py` calls `pipeline.request_manual(cmd)` instead of `ptz.pan_tilt(...)`.
- `control_calibration.py` calls `pipeline.request_calibrate()` and `pipeline.request_calibrate_command(cmd)`.
- `PointingVerifier` calls `pipeline.request_verify_resend(cmd)`; the dispatcher checks the move’s author still owns before sending.
- `Pipeline._run()` calls `cmd = self._resolve_and_dispatch()` once per frame.

### 1.2 Replace flat ownership with priority resolver

**Problem:** `PtzOwner` rejects auto-steal but has no priority order.

**Change:** Add numeric priority and a single resolver function.

**Files:**
- `orin/wavecam/wavecam/ptz_owner.py`

**Design:**
```python
class OwnerPriority:
    KILL = 100
    CALIBRATE = 80
    MANUAL = 60
    ESTIMATOR = 40
    GPS_TRACKER = 30
    VISION_FOLLOW = 20
    IDLE = 0

OWNERS = {"idle", "manual", "vision_follow", "gps_tracker", "estimator", "calibrate"}
AUTONOMOUS = {"vision_follow", "gps_tracker", "estimator"}
```

- `resolve_owner(candidates: list[str]) -> str` returns the highest-priority candidate.
- `PtzOwner.request(owner)` is replaced by `PtzOwner.resolve(candidates)` which updates `_owner` atomically.
- Remove `testbed` from `OWNERS`; use `IDLE` for auto-start and let the resolver pick `VISION_FOLLOW` or `GPS_TRACKER`.

### 1.3 Frame budget guard

**Problem:** No frame-budget measurement or degradation ladder.

**Change:** Add `system.frame_budget_ms` config and a budget check in the main loop.

**Files:**
- `orin/wavecam/wavecam/config.py`
- `orin/wavecam/wavecam/pipeline.py`

**Design:**
```python
frame_budget_ms = float(getattr(self.cfg.system, "frame_budget_ms", 33.0))
dt_ms = (time.time() - t0) * 1000.0
overrun = dt_ms > frame_budget_ms
if overrun:
    self._estimator_skip = True
    self._tracker_skip = True
    self._fusion_cue_skip = True
```

- Skip flags are reset each frame.
- KILL handling is always executed before any skip logic.
- Log overrun events to `events` for field diagnostics.

### 1.4 Formal per-frame output contract

**Change:** Create a `FrameOutput` dataclass and populate it before `set_status()`.

**Files:**
- `orin/wavecam/wavecam/pipeline.py`
- `orin/wavecam/wavecam/control_snapshots.py`

**Design:**
```python
@dataclass
class FrameOutput:
    owner: str
    ptz_command: PtzCommand | None
    fusion_debug: dict
    gps_state: dict
    estimator_state: dict
    timing: dict
```

- Populate `estimator_state` from the shadow/propose tick.
- Populate `gps_state` from the GPS reader snapshot.
- Expose all fields in `/api/v1/status` via `control_snapshots.py`.

### 1.5 Fix CALIBRATE > MANUAL priority

**Problem:** `control_calibration.py` allows `takeover=true` which lets manual preempt CALIBRATE.

**Change:** Remove `takeover` from calibration endpoints; manual is only allowed when owner is IDLE or MANUAL.

**Files:**
- `orin/wavecam/wavecam/control_calibration.py`
- `orin/wavecam/wavecam/control_ptz.py`

---

## 2. Feature 1 — GPS-driven zoom (`drive_zoom`)

### Goal
Make distance→zoom encoder curve active in `gps_tracker` mode when `gps.drive_zoom=True`.

### v2 constraint
Only `GPS_TRACKER` (the resolved owner) may issue the zoom absolute command. No other source issues zoom while GPS owns.

### Files
- `orin/wavecam/wavecam/config.py`
- `orin/wavecam/wavecam/gps_pointing.py`
- `orin/wavecam/wavecam/pipeline.py`
- `orin/wavecam/wavecam/control_config.py`
- `orin/wavecam/wavecam/control_utils.py`
- `orin/wavecam/wavecam/control_snapshots.py`

### Steps
1. Add `GpsCfg` keys: `drive_zoom_near_m`, `drive_zoom_far_m`, `drive_zoom_max_frac`, `drive_zoom_max_speed`.
2. Replace hard-coded zoom in `gps_pointing.py` with a `ZoomCurve` dataclass built from config.
3. In `Pipeline._gps_pointing_cmd()`, build the curve and include `zoom_enc` in `PtzAbsoluteCommand`.
4. In `PtzDispatcher`, ensure zoom absolute is only sent when owner is `GPS_TRACKER`.
5. Add hot-config keys and snapshot entries.
6. Add `tests/test_gps_drive_zoom.py`.

### Tests
- Disabled → `zoom_enc is None`.
- Near/far curve mapping.
- Clamping and calibration gating.
- KILL stops zoom mid-move.

---

## 3. Feature 2 — Base drift revalidation

### Goal
Detect tripod movement and invalidate GPS authority until recalibrated.

### v2 constraint
Drift detection runs as an observer; it changes `base_locked` state, which feeds into the resolver. It never issues PTZ commands.

### Files
- `orin/wavecam/wavecam/camera_pose.py`
- `orin/wavecam/wavecam/pipeline.py`
- `orin/wavecam/wavecam/config.py`
- `orin/wavecam/wavecam/control_config.py`
- `orin/wavecam/wavecam/control_snapshots.py`

### Steps
1. Add runtime `base_locked: bool` to `CameraPose` (separate from persisted `has_base`).
2. Add `GpsCfg` keys: `base_drift_enabled`, `base_drift_threshold_m`, `base_drift_interval_sec`, `base_drift_min_consecutive`, `base_drift_min_trend_m`.
3. Implement `BaseDriftMonitor` (pure helper):
   - Track last N distances from latched base to fresh base fix.
   - Trigger only if `mean_distance > threshold` AND `linear_trend > min_trend_m` over N samples.
4. Run the monitor in `Pipeline._run()` every `base_drift_interval_sec`.
5. On trigger, set `pose.base_locked = False` and record `base_drift_alert` event.
6. The resolver uses `base_locked = pose.has_base and pose.base_locked`.
7. Add hot-config keys and snapshot entries.
8. Add `tests/test_base_drift.py`.

### Tests
- No drift → stays locked.
- Single jump under threshold → stays locked.
- Sustained movement above threshold + rising trend → unlocked + alert.
- `base_drift_enabled=False` → never flags.
- CALIBRATE owner active → drift ignored.

---

## 4. Feature 3 — Commanded shadow Kalman estimator

### Goal
Allow the estimator to drive PTZ when initialized and low-uncertainty.

### v2 constraint
Estimator is an autonomous owner with three modes: `SHADOW`, `PROPOSE`, `COMMAND`. Transition to `COMMAND` requires stable confidence for N frames. The resolver enforces priority: `ESTIMATOR > GPS_TRACKER > VISION_FOLLOW`.

### Files
- `orin/wavecam/wavecam/estimator.py`
- `orin/wavecam/wavecam/config.py`
- `orin/wavecam/wavecam/pipeline.py`
- `orin/wavecam/wavecam/ptz_owner.py`
- `orin/wavecam/wavecam/control_config.py`

### Steps
1. Add `EstimatorMode` enum: `SHADOW`, `PROPOSE`, `COMMAND`.
2. Replace `estimator.shadow: bool` with `estimator.mode: str` in config (backward-compatible: `shadow=True` → `SHADOW`, `shadow=False` → `PROPOSE`).
3. Add `estimator.command_max_bearing_std_deg` and `estimator.command_stable_frames`.
4. In `TargetEstimator`:
   - Track `propose` history (N frames of low `bearing_std_deg`).
   - Only become `COMMAND`-eligible after stable frames.
   - Expose `mode`, `stable_frames`, `bearing_std_deg`, `pan_enc_would`, `tilt_enc_would`.
5. In `Pipeline._run()`:
   - Always run shadow tick (if enabled).
   - If mode is `PROPOSE` or `COMMAND`, add `"estimator"` to the candidate list when eligible.
   - Resolver picks `ESTIMATOR` over `GPS_TRACKER` if eligible.
   - If resolver returns `ESTIMATOR`, dispatch an absolute command from `pan_enc_would` / `tilt_enc_would`.
6. Zoom: reuse `gps.drive_zoom` curve if `GPS_TRACKER` would have owned, otherwise hold.
7. Add `tests/test_estimator_commanded.py`.

### Tests
- `SHADOW` → no `pan_tilt_absolute` calls.
- `PROPOSE` → no commands; stable frames counted.
- `COMMAND` + low std → owner becomes `ESTIMATOR`, absolute command issued.
- High std → falls back to `GPS_TRACKER`/`VISION_FOLLOW`/`IDLE`.
- KILL/CALIBRATE → no estimator commands.

---

## 5. Feature 4 — P2 GPS-bearing → fusion confidence injection

### Goal
Boost color-blob confidence near the GPS-predicted bearing so color-only can acquire at distance.

### v2 constraint
Fusion cue is a probabilistic region, not a direct PTZ command. It is only a bias signal.

### Files
- `orin/wavecam/wavecam/fusion.py`
- `orin/wavecam/wavecam/pipeline.py`
- `orin/wavecam/wavecam/gps_geo.py`
- `orin/wavecam/wavecam/camera_pose.py`
- `orin/wavecam/wavecam/config.py`

### Steps
1. Add `fusion.gps_bearing_cue_enabled` (default False).
2. Add pure helper `compute_bearing_cue()`:
   - Inputs: base→target bearing, current pan encoder bearing, FOV curve at current zoom, frame size, GPS uncertainty.
   - Output: `gps_cue_px = (cx, cy, radius_px)` where `cx` is shifted by bearing error and `radius_px` scales with uncertainty.
3. In `Pipeline._run()`, compute the cue when enabled and GPS is fresh/calibrated/base-locked.
4. Pass `gps_cue_px` to `Fusion.update()`.
5. Existing fusion boost logic already applies `gps_boost` to blobs inside radius; reuse it.
6. Add `tests/test_gps_bearing_cue.py`.

### Tests
- Disabled → no cue unless `gps_tracker` owns (existing center-cue path).
- Target 10° right → cue shifted right.
- Blob near projected cue locks; same blob far from center cue would not.
- High GPS uncertainty → larger radius.
- KILL → cue does not move camera.

---

## 6. Feature 5 — Persistent track ID (ByteTrack / BoT-SORT)

### Goal
Reduce target swaps when multiple people/orange objects are in frame.

### v2 constraint
Tracker only affects which box `Fusion` selects. It never issues PTZ commands.

### Files
- `orin/wavecam/wavecam/detector.py`
- `orin/wavecam/wavecam/fusion.py`
- `orin/wavecam/wavecam/config.py`
- `orin/wavecam/wavecam/control_config.py`

### Steps
1. Add `detector.tracker: str | None` (default None; values `"bytetrack.yaml"`, `"botsort.yaml"`).
2. Add `track_id: int | None` to `PersonBox`.
3. Create thin `PersonTracker` wrapper:
   - Calls `model.track(..., tracker=cfg.tracker, persist=True, verbose=False)`.
   - Extracts `boxes.id` into `track_id`.
   - Falls back to `model.predict` on any exception or if `tracker` is None.
4. In `Fusion._select`, prefer box with `track_id == self._last_track_id`; fallback to existing EMA continuity.
5. Update only when a person box is selected; color-only frames do not invalidate stored ID.
6. Add `tests/test_tracker.py`.
7. Add tracker dependency (`lapx`, etc.) as optional; fail open.

### Tests
- No tracker → all `track_id` None.
- Tracker configured → IDs populated.
- Tracker exception → fallback, no crash.
- Fusion prefers same ID over nearer unmatched person.
- EMA continuity still works when tracker disabled.

---

## 7. Implementation order

### Phase A — Architecture refactor (must come first)
1. Centralize PTZ dispatch (`PtzDispatcher`).
2. Add priority resolver to `PtzOwner`.
3. Add frame budget guard.
4. Formalize per-frame `FrameOutput` contract.
5. Fix CALIBRATE > MANUAL priority.
6. Run full test suite; verify no regressions.

### Phase B — Independent feature work
- B1: GPS-driven zoom (Feature 1)
- B2: Base drift revalidation (Feature 2)
- B3: GPS-bearing fusion cue (Feature 4)
- B4: Persistent track ID (Feature 5)

These four can proceed in parallel after Phase A.

### Phase C — Estimator command (after B2)
- C1: Estimator SHADOW/PROPOSE/COMMAND modes.
- C2: Wire estimator as autonomous owner with priority > GPS.
- C3: Yard validate with FOV curve and drift guard in place.

---

## 8. Testing strategy

### Unit tests (before/parallel to code)
- `tests/test_ptz_owner_priority.py` — priority resolver, KILL wins, CALIBRATE blocks manual/autonomous.
- `tests/test_ptz_dispatcher.py` — only resolved owner’s command is sent; verifier resend blocked on ownership change.
- `tests/test_frame_budget.py` — overrun skips estimator/tracker/fusion cue; KILL always handled.
- `tests/test_gps_drive_zoom.py`
- `tests/test_base_drift.py`
- `tests/test_gps_bearing_cue.py`
- `tests/test_tracker.py`
- `tests/test_estimator_commanded.py`

### Integration / yard tests
- KILL stops all motion from every new command path.
- CALIBRATE blocks all autonomous and manual takeovers.
- Manual joystick overrides autonomous owners and returns to idle on release.
- `drive_zoom` curve maps distance to zoom encoder.
- Base drift triggers after sustained movement.
- Estimator transitions SHADOW → PROPOSE → COMMAND only with stable confidence.

### Regression
- `pytest orin/wavecam/tests` — must pass 419+ tests.
- `python3 -m compileall -q orin/wavecam/wavecam`
- `git diff --check`

---

## 9. Rollback guarantees

Every feature supports hot-disable within one frame:
- `gps.drive_zoom=false`
- `gps.base_drift_enabled=false`
- `fusion.gps_bearing_cue_enabled=false`
- `estimator.mode="SHADOW"`
- `detector.tracker=null`

Restart required only for tracker backend swap (detector model is created once).

---

## 10. Files that will change

### New files
- `orin/wavecam/wavecam/ptz_dispatcher.py`
- `orin/wavecam/wavecam/base_drift.py`
- `orin/wavecam/tests/test_ptz_owner_priority.py`
- `orin/wavecam/tests/test_ptz_dispatcher.py`
- `orin/wavecam/tests/test_frame_budget.py`
- `orin/wavecam/tests/test_gps_drive_zoom.py`
- `orin/wavecam/tests/test_base_drift.py`
- `orin/wavecam/tests/test_gps_bearing_cue.py`
- `orin/wavecam/tests/test_tracker.py`
- `orin/wavecam/tests/test_estimator_commanded.py`

### Modified files
- `orin/wavecam/wavecam/ptz_owner.py`
- `orin/wavecam/wavecam/pipeline.py`
- `orin/wavecam/wavecam/control_ptz.py`
- `orin/wavecam/wavecam/control_calibration.py`
- `orin/wavecam/wavecam/pointing_verifier.py`
- `orin/wavecam/wavecam/config.py`
- `orin/wavecam/wavecam/control_config.py`
- `orin/wavecam/wavecam/control_utils.py`
- `orin/wavecam/wavecam/control_snapshots.py`
- `orin/wavecam/wavecam/gps_pointing.py`
- `orin/wavecam/wavecam/camera_pose.py`
- `orin/wavecam/wavecam/estimator.py`
- `orin/wavecam/wavecam/fusion.py`
- `orin/wavecam/wavecam/detector.py`

---

## 11. Risk summary

| Risk | Mitigation |
|------|------------|
| Central dispatcher introduces bugs in manual/calibration paths | Extensive tests for `control_ptz.py` and `control_calibration.py`; yard test every control surface. |
| Priority resolver changes existing handoff timing | Keep hysteresis/grace in `TrackingArbiter`; resolver only picks from candidates. |
| Estimator command causes oscillation | SHADOW/PROPOSE/COMMAND modes + bearing-std gate + stable-frame counter. |
| Frame budget guard drops critical work | KILL handling always runs first; estimator/tracker are the first to skip. |
| GPS bearing cue is noisy | Uncertainty-scaled radius; fallback to center cue when uncertainty is high. |
| ByteTrack/BoT-SORT fails on Jetson | Optional dependency; exception fallback to plain YOLO. |

---

## 12. Anti-vibe notes for implementer

- Do not introduce a generic “strategy” or “planner” abstraction. Keep changes in existing modules.
- Every behavioral change is flag-gated and defaults to current behavior.
- Prefer pure functions for cue/drift/zoom math; keep I/O in `Pipeline`/`PtzDispatcher`.
- Delete dead code (e.g., `testbed` owner if truly unused) rather than leaving it.
- Add tests before or with code; no feature is complete without a failing→passing test.
- Re-read `ptz_owner.py`, `pipeline.py`, and the relevant feature file before editing each feature.
