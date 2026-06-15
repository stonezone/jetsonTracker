# WaveCam Backend Enhancement Implementation Plan

**Project root:** `/Users/zackjordan/code/jetsonTracker`  
**Scope:** `orin/wavecam/wavecam/*` and `orin/wavecam/tests/*`  
**Goal:** camera follows me automatically and keeps me framed while foil-surfing 50–300 m offshore.

**Anti-vibe constraints applied:** each change is flag-gated so the default path is byte-identical; domain logic stays separate from I/O; tests are proportional to risk; no speculative frameworks.

---

## Recommended overall order

| Phase | Enhancement | Why this order |
|-------|-------------|----------------|
| 1 | **1. GPS-driven zoom** | Already partially wired; lowest risk; tunes a curve rather than changing control flow. |
| 2 | **4. GPS-bearing → fusion cue** | Builds on existing `gps_cue_px` mechanism; independent of zoom. |
| 3 | **2. Base drift revalidation** | Adds a safety guard that later command sources (estimator, GPS zoom) can depend on. |
| 4 | **3. Shadow Kalman → commanded** | Depends on calibrated FOV, base lock, and robust fallback; do after drift guard. |
| 5 | **5. Persistent track ID** | Highest uncertainty (new dependency/tracker behavior); isolate to the end. |

### Dependencies & parallelization

- **Can be parallel:** Items 1, 4, and 2 touch different subsystems (zoom curve, fusion cue, base GPS) and can be developed in parallel once their shared config/snapshot contract is understood.
- **Sequential:** Item 3 should follow Item 2 because the commanded estimator should inherit a validated base-lock state; it also reuses the FOV curve and calibration flow already present.
- **Isolated:** Item 5 is largely confined to `detector.py`/`fusion.py` and can proceed in parallel with 1–4, but should be the last to merge because it introduces an external tracker path.

---

## 1. GPS-driven zoom (`drive_zoom`)

### Goal & acceptance criteria
Make the distance→zoom encoder curve tunable and safely active in `gps_tracker` mode when `gps.drive_zoom=True`.

- `gps.drive_zoom=False` leaves behavior identical to today (no zoom component in absolute GPS commands).
- `gps.drive_zoom=True` produces a zoom encoder target derived from subject distance via a configurable curve.
- Zoom commands are rate-limited, clamped, and gated by the same `calibration_valid` + `base_locked` + `gps_fresh` + not-KILL/not-CALIBRATE rules that already gate GPS pan/tilt.
- Curve parameters are hot-configurable and visible in `/api/v1/config`.

### Key files to read / modify
- Read: `orin/wavecam/wavecam/gps_pointing.py`, `orin/wavecam/wavecam/pipeline.py`, `orin/wavecam/wavecam/config.py`
- Modify: `orin/wavecam/wavecam/config.py`, `orin/wavecam/wavecam/control_config.py`, `orin/wavecam/wavecam/control_utils.py`, `orin/wavecam/wavecam/control_snapshots.py`, `orin/wavecam/wavecam/gps_pointing.py`, `orin/wavecam/wavecam/pipeline.py`
- Add tests: `orin/wavecam/tests/test_gps_drive_zoom.py`

### Step-by-step implementation
1. In `GpsCfg`, add tunable curve fields: `drive_zoom_near_m`, `drive_zoom_far_m`, `drive_zoom_max_frac`, plus `drive_zoom_max_speed` if absolute zoom speed needs its own ceiling.
2. In `gps_pointing.py`, change `compute_target` / `distance_to_zoom_encoder` to accept a `ZoomCurve` built from config instead of the hard-coded defaults.
3. In `pipeline._gps_pointing_cmd`, build `ZoomCurve` from `cfg.gps` when `drive_zoom` is true and pass it to `compute_target`.
4. Ensure `_send_absolute_cmd` clamps `cmd.zoom_enc` to the camera’s valid encoder range and only calls `ptz.zoom_absolute` when `zoom_enc` is not `None`.
5. Add hot-config entries in `control_config.py` and `HOT_CONFIG_KEYS` in `control_utils.py`.
6. Expose the new keys in `build_config_snapshot` (`control_snapshots.py`).
7. Add unit tests covering: disabled→`None`, near→wide, far→tele cap, clamping, and invalid calibration→no command.
8. Add yard checklist and run the existing suite to confirm no regressions.

### New config keys / hot keys
- `gps.drive_zoom_near_m` (float, default 40.0)
- `gps.drive_zoom_far_m` (float, default 250.0)
- `gps.drive_zoom_max_frac` (float, default 0.85)
- `gps.drive_zoom_max_speed` (int, default 3) — optional, if zoom absolute speed needs a separate GPS cap

### Unit tests to add/modify
- `test_gps_drive_zoom.py`:
  - `drive_zoom=False` → `zoom_enc is None`
  - Default curve: near → 0, far → `max_frac * max_enc`, midpoint → linear
  - Custom curve parameters are honored
  - Out-of-range distances clamp
  - Invalid calibration or missing base → no absolute command
- Extend `test_gps_pointing.py` to assert custom `ZoomCurve` values.

### Yard / hardware verification
1. With `gps.drive_zoom=False`, confirm the camera never receives `zoom_absolute` while GPS-tracking.
2. With `gps.drive_zoom=True`, place subject at ~40 m → verify zoom drives wide; at ~250 m → verify zoom drives tele.
3. Press KILL during a zoom move; verify `zoom("stop")` is sent and the move halts.
4. Enter CALIBRATE mode; verify no GPS absolute commands (zoom or pan/tilt) are issued.

### Safety / invariant checks
- **KILL latch:** `pipeline.kill()` already stops pan/tilt and zoom. Absolute zoom obeys the same killed path.
- **Owner/deadman:** Zoom absolute is only sent when `owner == gps_tracker`; manual owner wins because the pipeline releases the autonomous owner before a manual claim.
- **CALIBRATE lockout:** GPS commands are computed only when `calibration_valid=True`; CALIBRATE sessions are never valid for GPS drive.

### Rollback procedure
1. Hot-set `gps.drive_zoom=false`.
2. Remove any overlay keys `gps.drive_zoom_*` from `config.local.yaml`.
3. Revert code changes and restart the service.
4. If persisted, re-run base/heading calibration.

### Risk / uncertainty
- **Low.** Curve math already exists; this is mostly parameterization and clamping.
- Watch for zoom “hunting” if distance jitter is high; if observed, add a small encoder deadband in a follow-up.

---

## 2. Base drift revalidation

### Goal & acceptance criteria
Detect when the camera/tripod has physically moved away from its latched base position and invalidate GPS authority until recalibrated.

- A fresh base GPS fix is periodically compared to the latched `CameraPose` lat/lon.
- If the distance exceeds a threshold for a minimum number of consecutive samples, `base_locked` becomes false.
- When `base_locked` is false, the arbiter no longer grants `gps_tracker` ownership (camera holds position / stops).
- The condition is surfaced in `/api/v1/status` and `/api/v1/calibration` as a `base_drift_alert`.
- Feature is gated by `gps.base_drift_enabled` and defaults off.

### Key files to read / modify
- Read: `orin/wavecam/wavecam/camera_pose.py`, `orin/wavecam/wavecam/pipeline.py`, `orin/wavecam/wavecam/tracking_arbiter.py`, `orin/wavecam/wavecam/gps_geo.py`
- Modify: `orin/wavecam/wavecam/camera_pose.py`, `orin/wavecam/wavecam/pipeline.py`, `orin/wavecam/wavecam/config.py`, `orin/wavecam/wavecam/control_config.py`, `orin/wavecam/wavecam/control_utils.py`, `orin/wavecam/wavecam/control_snapshots.py`, `orin/wavecam/wavecam/calibration_store.py` (if drift state is persisted)
- Add tests: `orin/wavecam/tests/test_base_drift.py`

### Step-by-step implementation
1. Add a runtime `base_locked: bool = True` field to `CameraPose` (distinct from the persisted `lat/lon` so history is not lost).
2. Add `GpsCfg` keys: `base_drift_enabled`, `base_drift_threshold_m`, `base_drift_interval_sec`, `base_drift_min_consecutive`.
3. In `Pipeline.__init__`, initialize drift counters and a `_base_drift_alert` flag.
4. In the main loop, every `base_drift_interval_sec`, if GPS is enabled and fresh:
   - Read `gps.get_camera_position()`.
   - Compute haversine distance from latched pose lat/lon.
   - If distance > threshold, increment counter; else reset counter.
   - If counter ≥ `base_drift_min_consecutive`, set `pose.base_locked = False`, record an event, and set `_base_drift_alert = True`.
5. Change the arbiter input in `pipeline.py` from `base_locked = self.pose.has_base` to `base_locked = self.pose.has_base and self.pose.base_locked`.
6. Skip drift evaluation when `owner.owner == CALIBRATE` or when killed (to avoid false alerts while the rig is intentionally handled).
7. Expose `base_drift_alert` in status and calibration snapshots.
8. Add hot-config keys and snapshot entries.
9. Add tests.

### New config keys / hot keys
- `gps.base_drift_enabled` (bool, default False)
- `gps.base_drift_threshold_m` (float, default 10.0)
- `gps.base_drift_interval_sec` (float, default 5.0)
- `gps.base_drift_min_consecutive` (int, default 3)

### Unit tests to add/modify
- `test_base_drift.py`:
  - No drift → `base_locked` remains true.
  - Small movement under threshold → still true.
  - Movement > threshold for min consecutive samples → `base_locked` false and alert set.
  - `base_drift_enabled=False` → never flags.
  - CALIBRATE owner active → drift ignored.
  - KILL state → GPS authority lost anyway; drift check does not re-enable it.
  - Arbiter uses combined `has_base and base_locked`.

### Yard / hardware verification
1. Calibrate and lock base.
2. With `base_drift_enabled=True` and threshold 5 m, physically move the tripod/camera GPS antenna ~10 m.
3. Within a few seconds, confirm status shows `base_drift_alert=true` and `base_locked=false`; camera stops GPS tracking.
4. Recalibrate location; confirm `base_locked=true` and GPS tracking resumes.

### Safety / invariant checks
- **KILL latch:** KILL already forces idle/stop; drift logic cannot override it.
- **Owner/deadman:** Manual or CALIBRATE owner prevents GPS ownership; drift flag only tightens that.
- **CALIBRATE lockout:** Drift is not evaluated during CALIBRATE so an intentional reposition during calibration does not trigger a spurious alert.

### Rollback procedure
1. Hot-set `gps.base_drift_enabled=false`.
2. If `base_locked` was falsely cleared, recalibrate location via `/api/v1/calibration/location`.
3. Revert code changes and restart.

### Risk / uncertainty
- **Medium-low.** The main risk is GPS jitter causing false alerts; mitigate with threshold + consecutive samples.
- Fresh base GPS age should be checked; stale fixes must not be used for drift decisions.

---

## 3. Enable / wire shadow Kalman estimator as a commanded source

### Goal & acceptance criteria
Allow the existing constant-velocity Kalman estimator to drive the camera when it is initialized and its bearing uncertainty is low, while preserving the current shadow-only mode as the default.

- `estimator.enabled=False` or `estimator.shadow=True` keeps today’s behavior: estimator logs only, never commands.
- `estimator.enabled=True` and `estimator.shadow=False` enables a new “estimator” autonomous owner that issues absolute pan/tilt commands from `predict_output()`.
- Commanding is gated on a maximum bearing standard deviation (`estimator.command_max_bearing_std_deg`).
- If the estimator is uninitialized, high-uncertainty, throws, or vision takes a lock, the pipeline falls back to the existing arbiter (`vision_follow` / `gps_tracker` / `idle`) within one frame.
- KILL and CALIBRATE always prevent estimator commands.

### Key files to read / modify
- Read: `orin/wavecam/wavecam/estimator.py`, `orin/wavecam/wavecam/pipeline.py`, `orin/wavecam/wavecam/ptz_owner.py`, `orin/wavecam/wavecam/config.py`
- Modify: `orin/wavecam/wavecam/config.py`, `orin/wavecam/wavecam/control_config.py`, `orin/wavecam/wavecam/control_utils.py`, `orin/wavecam/wavecam/control_snapshots.py`, `orin/wavecam/wavecam/ptz_owner.py`, `orin/wavecam/wavecam/pipeline.py`
- Add tests: `orin/wavecam/tests/test_estimator_commanded.py`

### Step-by-step implementation
1. Add `command_max_bearing_std_deg` to `EstimatorCfg` (default e.g. 5.0).
2. Add `"estimator"` to `OWNERS` and `AUTONOMOUS` in `ptz_owner.py`.
3. In `pipeline.py`, after the arbiter decision but before sending commands:
   - Compute whether the estimator is allowed to command:
     - `estimator.enabled == True`
     - `estimator.shadow == False`
     - estimator initialized
     - `predict_output()` is not None
     - `out.bearing_std_deg <= cfg.estimator.command_max_bearing_std_deg`
     - not killed/restarting
     - current owner is not `CALIBRATE`
     - vision is not currently locked (or arbiter owner is `gps_tracker`/`idle`)
   - If allowed, release any outgoing `gps_tracker` owner, request `"estimator"`, and send an absolute pan/tilt command built from `out.pan_enc_would` / `out.tilt_enc_would`.
   - If not allowed and `"estimator"` currently owns, release it and fall through to the arbiter decision.
4. Reuse the existing rate-limit pattern (de-duplicate identical absolute commands), but keep estimator moves independent of the GPS pointing verifier (estimator generates fresh commands each frame; resend logic is unnecessary).
5. Zoom: do not drive zoom from the estimator unless `gps.drive_zoom` is also enabled; if it is, optionally map `out.dist_m` through the zoom curve.
6. Continue running `_estimator_shadow_tick` unchanged so logging remains available regardless of command mode.
7. Expose `estimator.shadow`, `estimator.enabled`, and `estimator.command_max_bearing_std_deg` in snapshots/hot-keys.
8. Add tests covering command path, fallback path, KILL/CALIBRATE gating.

### New config keys / hot keys
- `estimator.command_max_bearing_std_deg` (float, default 5.0)

Existing keys reused:
- `estimator.enabled`
- `estimator.shadow`

### Unit tests to add/modify
- `test_estimator_commanded.py`:
  - `shadow=True` → no `pan_tilt_absolute` calls from estimator.
  - `shadow=False`, initialized, low std → owner becomes `"estimator"` and absolute command matches `pan_enc_would`/`tilt_enc_would`.
  - High std → fallback to arbiter; estimator owner released.
  - KILL latched → no estimator command.
  - CALIBRATE owner → estimator cannot claim ownership.
  - Vision lock → `vision_follow` keeps ownership.
  - Estimator exception → fallback, loop continues, shadow logging may continue.

### Yard / hardware verification
1. Ensure FOV curve is populated and calibration is valid.
2. Enable `estimator.enabled=true`, keep `estimator.shadow=true` (default); verify shadow JSONL logs contain plausible `pan_enc_would`.
3. Hot-set `estimator.shadow=false`; verify the camera now follows the estimator’s absolute commands.
4. Occlude the subject or move erratically until `bearing_std_deg` exceeds the threshold; verify the rig falls back to GPS or idle.
5. Press KILL; verify absolute motion stops immediately.

### Safety / invariant checks
- **KILL latch:** Command branch must re-check `state.killed` / `owner.killed` immediately before sending; KILL sets owner to idle.
- **Owner/deadman:** Estimator is an autonomous owner; manual requests require takeover as usual.
- **CALIBRATE lockout:** If `owner.owner == CALIBRATE`, estimator command branch is skipped.

### Rollback procedure
1. Hot-set `estimator.shadow=true` (or `estimator.enabled=false`).
2. Restart service if code changes are reverted.
3. Verify `/api/v1/status.shadow_mode` returns to expected state.

### Risk / uncertainty
- **Medium.** The estimator is a new control source; poor calibration/FOV can cause wrong bearings. The fallback and std-dev gate limit exposure.
- Need to ensure the estimator command path does not fight with `gps_tracker`; release the outgoing owner before claiming `"estimator"`.

---

## 4. P2 GPS-bearing → fusion confidence injection

### Goal & acceptance criteria
Boost color-blob confidence near the GPS-predicted subject bearing so color-only acquisition works at distance even when the camera is not currently GPS-pointed.

- New flag `fusion.gps_bearing_cue_enabled` (default False) keeps current behavior.
- When enabled and GPS is fresh/calibrated, the pipeline computes a pixel cue from:
  - latched base → remote target bearing,
  - current pan encoder → current camera bearing,
  - current zoom/FOV → pixel offset.
- The resulting `gps_cue_px` is passed to `Fusion.update`, giving blobs near the predicted bearing the existing `gps_boost`.
- If encoder or FOV data is stale, the cue falls back to frame center (the existing center-cue behavior) or is disabled.

### Key files to read / modify
- Read: `orin/wavecam/wavecam/fusion.py`, `orin/wavecam/wavecam/pipeline.py`, `orin/wavecam/wavecam/gps_geo.py`, `orin/wavecam/wavecam/estimator.py` (FOV helper), `orin/wavecam/wavecam/gps_pointing.py`
- Modify: `orin/wavecam/wavecam/config.py`, `orin/wavecam/wavecam/control_config.py`, `orin/wavecam/wavecam/control_utils.py`, `orin/wavecam/wavecam/control_snapshots.py`, `orin/wavecam/wavecam/pipeline.py`
- Add tests: `orin/wavecam/tests/test_gps_bearing_cue.py`

### Step-by-step implementation
1. Add `fusion.gps_bearing_cue_enabled` to `FusionCfg` (default False).
2. Add a pure helper (no I/O) in `pipeline.py` or a small module to:
   - Compute bearing from latched base to the remote fix using `gps_geo.bearing_deg`.
   - Get current pan bearing from `ptz_state.latest()` via `pose.pan_encoder_to_bearing()`.
   - Compute bearing error, normalize to ±180°.
   - Look up FOV from the calibration store’s `fov_curve` at current zoom encoder.
   - Convert error to pixel offset: `cue_x = frame_w/2 + error_deg / hfov_deg * frame_w`.
   - Clamp `cue_x` to `[0, frame_w]`; `cue_y = frame_h/2`; radius from `gps_boost_radius_frac`.
3. In the main loop, compute this cue whenever `gps_bearing_cue_enabled=True`, GPS is fresh, base is locked, and pose is calibrated. Use it in place of the simple center cue when the estimator-bearing path is active. When disabled, keep the existing center cue that is only emitted while `gps_tracker` owns.
4. Add hot-config key and snapshot entries.
5. Add unit tests with mocked encoder/FOV/fix to assert cue pixel position and boost behavior.

### New config keys / hot keys
- `fusion.gps_bearing_cue_enabled` (bool, default False)

Existing keys reused:
- `fusion.gps_boost`
- `fusion.gps_boost_radius_frac`

### Unit tests to add/modify
- `test_gps_bearing_cue.py`:
  - Disabled → cue logic returns `None` unless `gps_tracker` owns (existing center-cue path).
  - Target 10° to the right of current bearing, wide FOV → `cue_x` shifted right.
  - Blob near projected cue locks; same blob far from center cue would not lock without bearing cue.
  - Stale encoder → fallback to center or `None`.
  - Killed state → no cue injection.

### Yard / hardware verification
1. Disable bearing cue; point the camera manually away from the GPS target; confirm color-only does not lock at distance.
2. Enable `fusion.gps_bearing_cue_enabled`; point camera to a non-target area; confirm lock when a color blob appears near the predicted bearing line.
3. With `gps_tracker` owning, verify behavior matches the existing center cue (bearing cue should coincide with frame center).
4. Press KILL; confirm no cue-induced lock can drive PTZ.

### Safety / invariant checks
- **KILL latch:** Cue is only passed to `Fusion.update`; if killed, PTZ commands are forced to STOP after fusion, so a cue cannot move the camera.
- **Owner/deadman:** The cue is a sensory hint; it does not affect PTZ ownership directly.
- **CALIBRATE lockout:** Cue injection can be suppressed when `owner.owner == CALIBRATE` to avoid fusing operator-positioned imagery.

### Rollback procedure
1. Hot-set `fusion.gps_bearing_cue_enabled=false`.
2. Revert code and restart.

### Risk / uncertainty
- **Low-medium.** Error sources: pan encoder lag, FOV curve accuracy, bearing prediction lag. Fallback to center cue limits the worst case.
- The existing `gps_boost` cap (0.95) prevents runaway confidence.

---

## 5. Persistent track ID (ByteTrack / BoT-SORT)

### Goal & acceptance criteria
Integrate an optional multi-object tracker so target identity persists across frames and reduces swaps when multiple people line up.

- `detector.tracker=None` (default) leaves behavior identical to today.
- `detector.tracker="bytetrack.yaml"` or `"botsort.yaml"` enables Ultralytics tracking.
- Each `PersonBox` gains an optional `track_id`.
- `Fusion` prefers the person box whose `track_id` matches the previously selected track; fallback to existing EMA continuity when IDs are absent or lost.
- If the tracker fails to load or crashes at runtime, the pipeline falls back to plain `model.predict`.

### Key files to read / modify
- Read: `orin/wavecam/wavecam/detector.py`, `orin/wavecam/wavecam/fusion.py`, `orin/wavecam/wavecam/config.py`, `orin/wavecam/requirements.txt`
- Modify: `orin/wavecam/wavecam/config.py`, `orin/wavecam/wavecam/detector.py`, `orin/wavecam/wavecam/fusion.py`, `orin/wavecam/wavecam/control_config.py`, `orin/wavecam/wavecam/control_utils.py`, `orin/wavecam/wavecam/control_snapshots.py`, optionally `orin/wavecam/wavecam/overlay.py`
- Add: `orin/wavecam/wavecam/tracker.py` (thin wrapper, optional)
- Add tests: `orin/wavecam/tests/test_tracker.py`

### Step-by-step implementation
1. Add `tracker: str | None = None` to `DetectorCfg`.
2. Add `track_id: int | None = None` to `PersonBox`.
3. Create a small `PersonTracker` wrapper that:
   - Accepts the YOLO model and config.
   - Calls `model.track(..., tracker=cfg.tracker, persist=True, verbose=False)` when a tracker is configured.
   - Maps returned boxes to `PersonBox` with `track_id` extracted from `boxes.id`.
   - Falls back to `model.predict` if the tracker string is empty/None or if any exception occurs.
4. Wire `PersonDetector.detect` to use the wrapper when `cfg.tracker` is set; otherwise keep the existing `model.predict` path.
5. In `Fusion._select`, store the `track_id` of the chosen candidate each frame (`self._last_track_id`). When choosing among candidates, prefer the one with `track_id == self._last_track_id` (if present and tracked); otherwise use existing continuity logic.
6. Ensure color-blob-only frames do not invalidate the stored track ID; it is only updated when a person box is selected.
7. Add tracker config string to snapshots and hot-config keys (restart required because model path changes at load time, or allow hot swap if the wrapper can reinitialize).
8. Optionally render `track_id` in `overlay.py`.
9. Add tests with mocked YOLO results containing IDs.

### New config keys / hot keys
- `detector.tracker` (str | None, default None; accepted values `"bytetrack.yaml"`, `"botsort.yaml"`)

### Unit tests to add/modify
- `test_tracker.py`:
  - No tracker configured → all `track_id` are `None`.
  - Tracker configured → `track_id` populated from mocked results.
  - Tracker exception → fallback to predict, no crash.
  - Fusion prefers same `track_id` over a nearer unmatched person.
  - EMA continuity still works when tracker is disabled.

### Yard / hardware verification
1. With tracker disabled, confirm existing multi-object behavior.
2. Enable ByteTrack; walk two people across the frame; confirm the same individual keeps the same ID and the camera stays on the initially locked subject.
3. Repeat with BoT-SORT and compare stability.
4. Disable tracker mid-session; confirm immediate fallback to old behavior.
5. Press KILL; verify tracker hints cannot override the stop.

### Safety / invariant checks
- **KILL latch:** Tracker only affects which box fusion selects; the final command still goes through the killed/owner gates.
- **Owner/deadman:** No change to ownership rules.
- **CALIBRATE lockout:** No change; calibration captures are unaffected.

### Rollback procedure
1. Set `detector.tracker=null` in config overlay.
2. Restart service (detector model is typically created once).
3. Revert code changes.

### Risk / uncertainty
- **Highest.** Tracker dependencies (`lapx`, tracker YAML files, Jetson compatibility) may not be present or may behave differently on aarch64.
- ByteTrack/BoT-SORT can lose IDs during occlusion or rapid motion; the EMA fallback must remain the primary continuity mechanism.
- Performance impact: `model.track` may be slower than `model.predict`; measure loop FPS before/after.

---

## Cross-cutting testing & verification checklist

- Run the full existing suite: `pytest orin/wavecam/tests` from the repo root.
- For each new flag, verify default value keeps existing behavior (byte-identical paths where promised).
- Verify every new hot-config key round-trips through `/api/v1/config/hot` and appears in `/api/v1/config`.
- Verify KILL stops all autonomous motion for every new command path.
- Verify CALIBRATE owner blocks all new autonomous command paths.
- Verify rollback can be performed via hot-config alone for each feature before reverting code.

---

## Final notes for the implementing agent

- Do **not** introduce a generic “planner” or “strategy” abstraction layer. Keep changes inside the existing modules and follow their patterns (`_send_*` helpers, `PtzOwner` request/release, `EventRing` recording, hot-config via `ConfigManager`).
- Every behavioral change must be behind a config flag that defaults to the current behavior.
- Add tests before or alongside code; no feature is considered complete without a failing→passing test in the yard.
- If a feature requires an external dependency (tracker), make the dependency optional and fail open to the existing path.
