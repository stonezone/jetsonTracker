# WaveCam Orin Backend — Read-Only Review with Recommended Fixes

**Date:** 2026-06-13  
**Scope:** `orin/wavecam/wavecam/` plus `run.py`, focused on the `/api/v1` Control API, PTZ ownership/safety, config/hot-config, media, GPS ingest, estimator shadow, and the contract with the iOS app.  
**Status:** No code was modified. This report is read-only and stops for instructions before any fixes are applied.

## Review methodology

- **Authoritative context:** I read the full source of the modules involved (`control_api.py`, `control_snapshots.py`, `control_config.py`, `control_ptz.py`, `control_system.py`, `control_calibration.py`, `pipeline.py`, `ptz_state.py`, `ptz_owner.py`, `auth.py`, `web.py`, `recorder.py`, etc.) and cross-checked the iOS contract by reading `ios/WaveCam/Sources/TuneView.swift` and `ios/WaveCam/Sources/WaveCamClient.swift`. External Context7 retrieval was not available, so the local repo and tests were used as the source of truth.
- **Vibe check:** I ran the anti-vibe-engineering `python_quality_scan.py` over the affected modules. It confirmed the known hotspots (large files, long functions, unused imports) but did not flag new defects beyond what is documented below.
- **Verification:** `cd orin/wavecam && python3 -m pytest tests -x -q` → **418 passed in 6.53s**. API-contract tests (`test_api_contract.py`, `test_ios_contract.py`) also pass.

---

## Findings Summary

| # | Severity | Area | File (line) | One-line |
|---|----------|------|-------------|----------|
| B1 | **High** | Auth / API contract | `control_api.py:307` | `/api/v1/preview.mjpeg` requires `Authorization` header; iOS `URLSessionDataTask` MJPEG reader cannot send headers, so the preview stream fails as soon as auth is enabled. |
| B2 | **High** | Safety / restart | `control_system.py:122-131` | `prepare_for_restart()` stops and releases the PTZ owner but does **not** set KILL or a restart guard; the vision loop can re-acquire `vision_follow`/`gps_tracker` during the restart countdown and move the camera before the process restarts. |
| B3 | **Medium** | iOS API contract | `control_snapshots.py:121` | `supported.tracking_mode` is snake_case; the iOS TuneView feature-gates on `supported.trackingMode` (camelCase), so the tracking-mode card is never shown on iPhone. |
| B4 | **Medium** | PTZ state machine | `control_ptz.py:97-109` | `home_ptz()` leaves PTZ owned by `manual` indefinitely — it schedules a zoom deadman but no manual deadman and never releases the owner. |
| B5 | **Medium** | Calibration / PTZ ownership | `control_calibration.py:718-725` | `validate_calibration_capture()` claims `manual` owner and cannot take over from `calibrate`, yet the new session flow requires `calibrate` owner. `/calibration/heading`, `/tilt`, `/zoom`, `/base-lock` will return `owner_busy` if called while a session is active. |
| B6 | **Medium** | PTZ state machine | `control_ptz.py:39-51` | `claim_manual(takeover=True)` is non-atomic: it stops the camera and releases the autonomous owner *before* requesting `manual`; if the request fails, the system is left idle/stopped with no saved restore. |
| B7 | **Medium** | Config persistence | `control_config.py:130-131` + `control_api.py:651-658` | Hot changes to `web.show_mask`/`web.show_hud` are applied to `pipeline.state`, but persistence reads `cfg.web`, so the overlay toggles do not survive restart. |
| B8 | **Low** | PTZ state machine | `web.py:501-508` | Legacy `/ptz/stop` stops PTZ/zoom and releases the owner but does **not** cancel the manual/zoom deadman timers, leaving stale timers that can fire later. |
| B9 | **Low** | Encoder poller | `ptz_state.py:123-149` | Stale-reply plausibility gate only checks pan rate; a corrupt tilt jump can still poison the cache. |
| B10 | **Low** | Latent / API contract | `pipeline.py:178-193` | `Pipeline.kill(False)` (resume path) unconditionally requests `testbed` owner, overriding any caller that intended a clean idle resume. Not reached by current `/api/v1` routes but is a latent surprise and is asserted by an existing test. |
| B11 | **Low** | Status contract | `control_snapshots.py:180-185` | `safety.kill_reason` and `safety.last_kill_at_unix_ms` are always `null`; the iOS app has fields for them but never receives real values. |
| B12 | **Low** | Resource / media | `recorder.py:59-83` | `Recorder.start()` reports success even if the ffmpeg child exits immediately; there is no check that the process is still alive a moment after spawn. |

---

## Detailed Findings + Recommended Fixes

### B1 — MJPEG preview stream is unreachable from iOS when auth is enabled

**Where:** `orin/wavecam/wavecam/control_api.py`, `register_status_routes()`, line 307.

```python
@app.get("/api/v1/preview.mjpeg", dependencies=[Depends(require(READ))])
def preview():
    return StreamingResponse(api.frames(), ...)
```

**Problem:** The iOS app uses `URLSessionDataTask` with a custom `URLSessionDataDelegate` to consume the MJPEG stream. `URLSessionDataTask` cannot attach HTTP headers to the request, so there is no way for the iPhone to send `Authorization: Bearer <token>`. As soon as an operator enables auth (`WAVECAM_AUTH_FILE` present), the live preview in the iOS app returns 401 and the feed never starts. The legacy web UI uses `/stream.mjpg` (no auth dependency), so this is specifically an iOS/backend contract gap.

**Risk:** Auth cannot be enabled in production without breaking the iOS preview. The current field workaround is to leave auth disabled.

**Concrete fix:**

1. Extend `auth.py` so `require()` can accept a token from the query string for streaming endpoints. WebSockets already do this in `websocket_authorized()`.

```python
# auth.py

def require(action: str, allow_query_token: bool = False):
    def dependency(request: Request) -> None:
        auth = getattr(request.app.state, "auth", None) or AuthConfig()
        token = bearer_token(request.headers)
        if token is None and allow_query_token:
            token = request.query_params.get("token") or None
        authorize(auth, token, action)
    return dependency
```

2. Change the preview route dependency:

```python
# control_api.py

@app.get("/api/v1/preview.mjpeg", dependencies=[Depends(require(READ, allow_query_token=True))])
def preview():
    return StreamingResponse(api.frames(), media_type="multipart/x-mixed-replace; boundary=frame")
```

3. On the iOS side, append `?token=<token>` to the MJPEG URL when auth is configured. The iOS review already documents the missing bearer header; this backend change gives the app a viable auth path.

4. Add a test in `tests/test_control_api_auth.py` proving a viewer token works via `?token=` on `/api/v1/preview.mjpeg` and that a missing/invalid token still returns 401.

---

### B2 — Restart countdown does not prevent the camera from moving

**Where:** `orin/wavecam/wavecam/control_system.py`, `prepare_for_restart()`, lines 122-131.

```python
def prepare_for_restart(self) -> None:
    self._api.cancel_manual_deadman()
    self._api.cancel_zoom_deadman()
    self._api.reset_restore_owner()
    self.pipeline.ptz.stop()
    self.pipeline.ptz.zoom("stop")
    current_owner = self.pipeline.owner.owner
    if current_owner != IDLE:
        self.pipeline.owner.release(current_owner)
    self.pipeline.state.set_status(state="RESTARTING", cmd="stop")
```

**Problem:** `prepare_for_restart` releases the owner but does **not** set the KILL latch or any other restart guard. The pipeline loop keeps running. On the next frame the TrackingArbiter can decide `vision_follow` or `gps_tracker`, request that owner, and start sending PTZ commands. The camera can therefore move *after* the operator confirmed a restart, until the actual `systemctl restart` kills the process.

**Risk:** Safety issue — a confirmed restart during active tracking can still produce uncommanded motion.

**Concrete fix:**

1. Add a restart guard flag to `Pipeline` and treat it like KILL for PTZ command issuance, but keep `safety.killed` false so the UI distinguishes a restart from an operator KILL.

```python
# pipeline.py, in Pipeline.__init__
self._restarting = False
```

2. Update `prepare_for_restart` to set the guard:

```python
# control_system.py

def prepare_for_restart(self) -> None:
    self._api.cancel_manual_deadman()
    self._api.cancel_zoom_deadman()
    self._api.reset_restore_owner()
    self.pipeline.ptz.stop()
    self.pipeline.ptz.zoom("stop")
    current_owner = self.pipeline.owner.owner
    if current_owner != IDLE:
        self.pipeline.owner.release(current_owner)
    self.pipeline._restarting = True
    self.pipeline.state.set_status(state="RESTARTING", cmd="stop")
```

3. In `Pipeline._run`, combine the guard with the killed check:

```python
# pipeline.py, inside _run
if self.state.killed or getattr(self, "_restarting", False):
    cmd = STOP_CMD
    abs_cmd = None
    self._send_cmd(cmd)
    self._send_zoom("stop")
    zoom_cmd = "hold"
    self._arbiter_state = "restarting" if self._restarting else "killed"
else:
    ... existing tracking logic ...
```

4. The guard is cleared only by process restart, so there is no risk of the old process resuming tracking after the restart timer fires.

5. Add a test that calls `prepare_for_restart`, runs a few pipeline iterations, and asserts no pan/tilt/zoom commands are issued and the owner stays idle.

---

### B3 — iOS tracking-mode UI is hidden because of a key-case mismatch

**Where:** `orin/wavecam/wavecam/control_snapshots.py`, `build_config_snapshot()`, line 121.

```python
"supported": {
    ...
    "tracking_mode": True,
    ...
}
```

**Problem:** The iOS `TuneView` feature-gates the tracking-mode card on `supported.trackingMode` (camelCase). The backend advertises `supported.tracking_mode` (snake_case). The web UI uses the snake_case key, so changing it would break the web console.

**Risk:** The iPhone operator can never see or change `tracking.mode` from the app, even though the backend fully supports it.

**Concrete fix:**

Add a camelCase alias without removing the existing snake_case key.

```python
# control_snapshots.py, build_config_snapshot supported dict
"supported": {
    ...
    "tracking_mode": True,
    "trackingMode": True,   # iOS client contract
    ...
}
```

Add an assertion in `tests/test_ios_contract.py` (or a new backend test) that both keys are present.

---

### B4 — `home_ptz()` leaves the PTZ owned by `manual` forever

**Where:** `orin/wavecam/wavecam/control_ptz.py`, `home_ptz()`, lines 97-109.

```python
def home_ptz(self) -> None:
    with self._lock:
        self.cancel_manual_deadman()
        self.cancel_zoom_deadman()
        self._manual_pan_tilt_active = False
        self.pipeline.ptz.stop()
        self.pipeline.ptz.zoom("stop")
        self.pipeline.ptz.home()
        self.pipeline.ptz.zoom("wide", int(...))
        self.schedule_zoom_deadman(HOME_ZOOM_WIDE_DEADMAN_MS)
```

**Problem:** The endpoint that calls this (`/api/v1/ptz/home`) first claims `manual` owner. `home_ptz` then cancels the manual deadman and schedules only a zoom deadman (4 s). After the zoom deadman expires, the PTZ owner remains `manual`. If autonomous tracking was running before home, it will not resume automatically; if the operator expects the camera to return to auto after homing, it will not.

**Risk:** Operator confusion / feature regression; the home command becomes a sticky manual trap.

**Concrete fix:**

Schedule a manual deadman with a timeout longer than the worst-case home slew so the owner is released automatically.

```python
# control_ptz.py

HOME_ZOOM_WIDE_DEADMAN_MS = 4000
HOME_PAN_TILT_DEADMAN_MS = 8000   # new constant

def home_ptz(self) -> None:
    with self._lock:
        self.cancel_manual_deadman()
        self.cancel_zoom_deadman()
        self._manual_pan_tilt_active = False
        self.pipeline.ptz.stop()
        self.pipeline.ptz.zoom("stop")
        self.pipeline.ptz.home()
        self.pipeline.ptz.zoom("wide", int(...))
        self.schedule_zoom_deadman(HOME_ZOOM_WIDE_DEADMAN_MS)
        self.schedule_manual_deadman(HOME_PAN_TILT_DEADMAN_MS)
```

`manual_deadman_expired` already stops PTZ/zoom and releases the owner (restoring autonomous if saved), so this single change makes the home command self-cleaning.

Add a test asserting that after the home deadman expires, the owner returns to idle (or the previous autonomous owner if takeover was used).

---

### B5 — Calibration capture endpoints conflict with the new session flow

**Where:** `orin/wavecam/wavecam/control_calibration.py`, `validate_calibration_capture()`, lines 718-725.

```python
def validate_calibration_capture(self, req) -> JSONResponse | None:
    if self.pipeline.owner.killed:
        return ...
    if req.requested_owner != "manual":
        return ...
    if not self._api.claim_manual(takeover=req.takeover):
        return self._api.refusal("owner_busy", "Another PTZ owner holds the camera.")
    return None
```

**Problem:** This guard is called by the standalone capture routes `/api/v1/calibration/heading`, `/tilt`, `/zoom`, and `/base-lock`. It tries to claim `manual` owner. `PtzDispatcher.claim_manual` only takes over from autonomous owners (`vision_follow`, `gps_tracker`, `testbed`); it cannot take over from `calibrate`. The new calibration session flow (`/api/v1/calibration/session/start`) explicitly sets owner to `calibrate` and requires it. Therefore, if an operator starts a session and then uses any capture endpoint, the request returns `owner_busy`.

**Risk:** The two calibration APIs are mutually exclusive in practice. The iOS app likely uses one or the other; if it mixes them, calibration becomes impossible. Even when used standalone, the capture endpoints leave owner as `manual` after success.

**Concrete fix:**

Decide the intended contract. The cleanest option is to make the capture endpoints standalone and self-releasing:

1. In `validate_calibration_capture`, capture the current owner and release it back after the capture, regardless of whether it was autonomous or `calibrate`.
2. Do **not** leave the owner as `manual` after a successful capture.

Pseudo-code for the capture flow in `control_api.py`:

```python
# control_api.py calibration routes

@api.post("/api/v1/calibration/heading", dependencies=[Depends(require(PTZ))])
def calibration_heading(req: HeadingCalibrationRequest):
    refusal = api.validate_calibration_capture(req)
    if refusal is not None:
        return refusal
    try:
        api.capture_calibration("heading", {...})
    finally:
        api.release_manual_owner(restore_autonomous=True)
    api.bump_revision()
    return api.calibration_ok()
```

3. If the session flow is the future, consider deprecating the standalone capture endpoints or making them session-aware (operate under the `calibrate` owner without `claim_manual`).

4. Add tests covering both standalone capture and session-flow capture, ensuring they do not deadlock on owner.

---

### B6 — `claim_manual(takeover=True)` is not atomic

**Where:** `orin/wavecam/wavecam/control_ptz.py`, `claim_manual()`, lines 39-51.

```python
def claim_manual(self, takeover: bool = False) -> bool:
    with self._lock:
        if self.pipeline.owner.request("manual"):
            return True
        current_owner = self.pipeline.owner.owner
        if not takeover or current_owner not in AUTONOMOUS:
            return False
        self.pipeline.ptz.stop()
        self.pipeline.ptz.zoom("stop")
        if not self.pipeline.owner.release(current_owner):
            return False
        self._restore_owner_after_manual = current_owner
        return self.pipeline.owner.request("manual")
```

**Problem:** If `takeover=True` and the current owner is autonomous, the method stops PTZ/zoom, releases the autonomous owner, saves the restore owner, and then requests `manual`. If the final request fails (e.g., KILL latched concurrently, or another caller grabbed the owner), the system is left idle and stopped, and the saved restore owner is never used.

**Risk:** Race condition during manual takeover can strand PTZ in idle/stopped state.

**Concrete fix:**

Restore the previous autonomous owner if the manual request fails after release. This at least prevents stranding.

```python
# control_ptz.py

def claim_manual(self, takeover: bool = False) -> bool:
    with self._lock:
        if self.pipeline.owner.request("manual"):
            return True
        current_owner = self.pipeline.owner.owner
        if not takeover or current_owner not in AUTONOMOUS:
            return False
        self.pipeline.ptz.stop()
        self.pipeline.ptz.zoom("stop")
        if not self.pipeline.owner.release(current_owner):
            return False
        self._restore_owner_after_manual = current_owner
        if self.pipeline.owner.request("manual"):
            return True
        # Takeover failed after we released the previous owner — try to restore.
        if not self.pipeline.owner.killed:
            self.pipeline.owner.request(current_owner)
        return False
```

A stronger fix would be to make the "release + request" sequence atomic inside `PtzOwner`, but that requires changing the owner lock semantics. The restore-on-failure change is the minimal safe improvement.

Add a concurrency test that latches KILL between release and request, asserting the previous owner is restored (or the system remains safely idle/killed).

---

### B7 — `web.show_mask` / `web.show_hud` hot changes do not persist

**Where:** `orin/wavecam/wavecam/control_config.py`, `apply_hot_key()`, lines 130-131; plus `control_api.py` hot-config persistence logic, lines 651-658.

```python
"web.show_mask": lambda: set_bool(self.pipeline.state, "show_mask", value, dry_run=dry_run),
"web.show_hud": lambda: set_bool(self.pipeline.state, "show_hud", value, dry_run=dry_run),
```

```python
for dotted in req.patch:
    section, attr = dotted.split(".", 1)
    coerced[dotted] = getattr(getattr(api.pipeline.cfg, section), attr)
```

**Problem:** The two web overlay toggles are applied to `pipeline.state`, not `cfg.web`. The persist path reads `api.pipeline.cfg.web.show_mask`, which is the original config value. After a restart, the overlay toggles revert.

**Risk:** Operator toggles in the UI/iOS do not survive restart.

**Concrete fix:**

Option A — apply to both state and cfg (preferred, keeps all config in one place):

```python
# control_config.py, inside apply_hot_key setters

"web.show_mask": lambda: self._set_web_bool("show_mask", value, dry_run=dry_run),
"web.show_hud": lambda: self._set_web_bool("show_hud", value, dry_run=dry_run),
```

Add a helper:

```python
def _set_web_bool(self, attr: str, value: Any, dry_run: bool = False) -> str | None:
    err = set_bool(self.pipeline.state, attr, value, dry_run=dry_run)
    if err is not None:
        return err
    return set_bool(self.pipeline.cfg.web, attr, value, dry_run=dry_run)
```

Option B — special-case the persistence path (smaller change):

```python
# control_api.py, hot config persistence loop
for dotted in req.patch:
    section, attr = dotted.split(".", 1)
    if dotted == "web.show_mask":
        coerced[dotted] = api.pipeline.state.show_mask
    elif dotted == "web.show_hud":
        coerced[dotted] = api.pipeline.state.show_hud
    else:
        coerced[dotted] = getattr(getattr(api.pipeline.cfg, section), attr)
```

Option A is cleaner because the config snapshot will also reflect the live value from `cfg.web`. Add a test in `tests/test_config_persist.py` that round-trips `web.show_mask=false` through `config.local.yaml`.

---

### B8 — Legacy `/ptz/stop` leaves deadman timers running

**Where:** `orin/wavecam/wavecam/web.py`, `ptz_stop()`, lines 501-508.

```python
@app.post("/ptz/stop", dependencies=[Depends(require(PTZ))])
def ptz_stop():
    pipeline.ptz.stop()
    pipeline.ptz.zoom("stop")
    pipeline.owner.release(pipeline.owner.owner)
    return {"ok": True, "owner": pipeline.owner.owner}
```

**Problem:** The legacy stop endpoint releases the owner but does not cancel the manual or zoom deadman timers. A timer that was scheduled before the stop can fire later, stopping PTZ/zoom again and releasing an owner that may no longer be `manual`.

**Risk:** Benign in most cases (stop-again is safe), but it is a state inconsistency that can cause surprising status transitions or double-stop commands.

**Concrete fix:**

Cancel the deadmen before releasing the owner:

```python
# web.py

@app.post("/ptz/stop", dependencies=[Depends(require(PTZ))])
def ptz_stop():
    api = app.state.control_api
    api.cancel_manual_deadman()
    api.cancel_zoom_deadman()
    pipeline.ptz.stop()
    pipeline.ptz.zoom("stop")
    pipeline.owner.release(pipeline.owner.owner)
    return {"ok": True, "owner": pipeline.owner.owner}
```

Add a test that schedules a manual deadman, calls `/ptz/stop`, and asserts the deadman callback does not fire (or fires harmlessly without issuing new commands).

---

### B9 — `PtzState` plausibility gate ignores tilt jumps

**Where:** `orin/wavecam/wavecam/ptz_state.py`, `_poll_once()`, lines 136-147.

```python
rate = abs(result[0] - self._enc[0]) / dt
if rate > MAX_SLEW_COUNTS_PER_SEC:
    ...
```

**Problem:** The stale/corrupt-frame defense only compares the **pan** encoder delta. A corrupt frame with a large tilt jump (but plausible pan) will be accepted and written to the cache.

**Risk:** Tilt cache can be poisoned by a single bad VISCA reply.

**Concrete fix:**

Check both axes and store the outlier as a full `(pan, tilt)` tuple.

```python
# ptz_state.py, inside _poll_once
if self._enc is not None and self._ts is not None:
    dt = max(1e-3, now - self._ts)
    pan_rate = abs(result[0] - self._enc[0]) / dt
    tilt_rate = abs(result[1] - self._enc[1]) / dt
    if pan_rate > MAX_SLEW_COUNTS_PER_SEC or tilt_rate > MAX_SLEW_COUNTS_PER_SEC:
        cand = getattr(self, "_outlier", None)
        if (cand is not None
                and abs(result[0] - cand[0]) <= MAX_SLEW_COUNTS_PER_SEC * dt
                and abs(result[1] - cand[1]) <= MAX_SLEW_COUNTS_PER_SEC * dt):
            self._outlier = None          # two agree: real big move
        else:
            self._outlier = result        # hold back; wait for confirmation
            return
    else:
        self._outlier = None
self._enc = result
self._ts = now
```

Add a test in `tests/test_ptz_state.py` for a tilt-only outlier and a two-frame agreeing big tilt move.

---

### B10 — `Pipeline.kill(False)` unconditionally requests `testbed`

**Where:** `orin/wavecam/wavecam/pipeline.py`, `kill()`, lines 178-193.

```python
def kill(self, on: bool = True):
    ...
    else:
        self.state.set_status(killed=False, state="SEARCHING")
        self.owner.resume()
        if self.cfg.ptz.enabled:
            self.owner.request("testbed")  # re-acquire on RESUME
```

**Problem:** The resume branch of `Pipeline.kill` immediately requests the `testbed` owner. This is at odds with `resume_without_autostart()`, which intentionally leaves the owner idle. The current `/api/v1/safety/resume` endpoint uses `resume_without_autostart()`, so this path is not triggered by the public API today. However, it is asserted by `tests/test_pipeline_kill.py::test_resume_clears_killed_status_without_waiting_for_next_frame`.

**Risk:** Future code or tests that call `pipeline.kill(False)` will silently re-enable testbed tracking.

**Concrete fix:**

1. Remove the unconditional `self.owner.request("testbed")` from `kill(False)`.
2. Keep testbed acquisition only at startup in `Pipeline._run` (it already requests testbed at boot when not `start_paused`).
3. Update `tests/test_pipeline_kill.py::test_resume_clears_killed_status_without_waiting_for_next_frame` to assert `pipe.owner.owner == IDLE` instead of `== "testbed"`.

After the change, `kill(False)` simply clears the killed latch and leaves the owner idle, which matches `resume_without_autostart()` semantics.

---

### B11 — Kill reason and timestamp are never populated

**Where:** `orin/wavecam/wavecam/control_snapshots.py`, `build_safety()`, lines 180-185.

```python
def build_safety(legacy: dict) -> dict:
    return {
        "killed": bool(legacy.get("killed", False)),
        "kill_reason": None,
        "last_kill_at_unix_ms": None,
    }
```

**Problem:** `kill_reason` and `last_kill_at_unix_ms` are hard-coded to `None`. The iOS app has UI fields for this information but never receives it.

**Risk:** Reduced situational awareness; the operator cannot see who/why KILL was triggered.

**Concrete fix:**

1. Track the last kill metadata in `Pipeline` (or `SharedState`).

```python
# pipeline.py, inside kill(on=True)
if on:
    ...
    self._last_kill = {"reason": reason, "at_unix_ms": int(time.time() * 1000)}
```

2. Surface it in the status snapshot:

```python
# control_snapshots.py

def build_safety(legacy: dict, pipeline=None) -> dict:
    last_kill = getattr(pipeline, "_last_kill", None) or {}
    return {
        "killed": bool(legacy.get("killed", False)),
        "kill_reason": last_kill.get("reason"),
        "last_kill_at_unix_ms": last_kill.get("at_unix_ms"),
    }
```

3. Update `build_status_snapshot` to pass `pipeline` into `build_safety`.

4. Add a test verifying that a POST to `/api/v1/safety/kill` with a reason populates these fields in the next status snapshot.

---

### B12 — Recorder reports success even if ffmpeg dies immediately

**Where:** `orin/wavecam/wavecam/recorder.py`, `start()`, lines 59-83.

**Problem:** `start()` spawns ffmpeg and immediately returns `started: True`. It never verifies that the process is still alive. If ffmpeg fails to start (bad path, missing binary, RTSP unreachable), the caller thinks recording is active until the next status poll.

**Risk:** False-positive recording status; operator may think a session is being captured when it is not.

**Concrete fix:**

Wait briefly after spawn and verify the process did not exit.

```python
# recorder.py, inside start() after self._proc = self._popen(cmd)

import time
start_wait = 0.25
time.sleep(start_wait)
if self._proc.poll() is not None:
    self._proc = None
    self._active_segment_pattern = None
    self._active_segment_prefix = None
    raise MediaUnavailable("ffmpeg exited immediately; check RTSP source and ffmpeg path.")
```

If catching the exit code is useful, store stderr. Since the current `_default_popen` sends stderr to `DEVNULL`, consider making the recorder popen injectable (it already is via the `popen` argument) and update tests with a fake process.

Add a test with a `popen` factory that returns an already-exited process, asserting `MediaUnavailable` is raised.

---

## Additional observations (not bugs, but worth tracking)

1. **Auth is fail-open.** `auth.py:69-75` returns `AuthConfig(enabled=False)` if the auth file is missing or unreadable. This is documented and intentional for field reliability, but it means a deleted or permission-denied auth file silently disables all API security. Consider logging a warning when the file is expected but missing.

2. **`/stream.mjpg` (legacy web UI) has no auth dependency.** This is consistent with the comment that the web console is the bring-up surface, but it means the video feed is always accessible even when `/api/v1/preview.mjpeg` requires a token. Decide whether this asymmetry is acceptable for production.

3. **Large modules.** `control_api.py` (~1,039 lines), `control_calibration.py` (~787 lines), `pipeline.py` (~732 lines), and `control_config.py` (~329 lines) remain large. The recent refactor moved behavior into helpers, but further splitting would improve maintainability. The Python quality scan already flags these.

4. **Unused imports.** `control_api.py` imports `build_gps`, `gps_fix_snapshot`, `map_axis`, `zoom_speed`, `RESTART_REQUIRED_KEYS`, and `uuid` but does not use them directly. `control_snapshots.py` imports `Any` but does not use it. These are harmless cleanup items.

5. **Estimator shadow writes to `/data/shadow`.** The pipeline guards shadow-writer failures well, but a production deployment must ensure `/data/shadow` is writable; otherwise the estimator is silently disabled at the G2 gate.

---

## Recommended next steps

1. Confirm whether auth is intended to be enabled in the field. If yes, fix **B1** first (MJPEG query-token auth) because it blocks the iOS preview.
2. Fix **B2** before any production restart-from-UI flow is used, because it is a safety issue.
3. Fix **B3** (tracking-mode key) so the iOS TuneView matches the backend.
4. Triage **B4**–**B6** (PTZ owner / calibration state-machine issues) together; they all involve owner-handoff correctness.
5. Fix **B7** if overlay persistence is expected by users.
6. Address **B8**–**B12** as lower-priority cleanup/reliability work.

---

*Report generated by Kimi Code CLI. No source files were modified.*


---

## Post-fix verification — 2026-06-13

**Commit checked:** `ca86830` (main) — *fix(backend): address 11 validated review findings from backend audit*

**Test result:** `python3 -m pytest tests -q` → **418 passed** in 7.08s.

**Status of original findings:**

| Finding | Status | Notes |
|---------|--------|-------|
| B1 | ✅ Resolved | `/api/v1/preview.mjpeg` now uses `require(READ, allow_query_token=True)`; iOS can append `?token=`. |
| B2 | ✅ Resolved | `prepare_for_restart()` now sets `pipeline._restarting = True`; the pipeline loop issues `STOP_CMD` while restarting. |
| B3 | ❌ Invalidated | iOS uses `JSONDecoder.KeyDecodingStrategy.convertFromSnakeCase`, so `supported.tracking_mode` correctly maps to `supported.trackingMode`. No backend change needed. |
| B4 | ✅ Resolved | `home_ptz()` now schedules both zoom and manual deadmen so the owner is auto-released. |
| B5 | ✅ Resolved | Standalone captures now accept the `calibrate` owner and restore it after capture (commit `4a25265`). |
| B6 | ✅ Resolved | `claim_manual(takeover=True)` restores the previous autonomous owner if the manual request fails after release. |
| B7 | ✅ Resolved | `web.show_mask` / `web.show_hud` hot changes now persist to `cfg.web`. |
| B8 | ✅ Resolved | Legacy `/ptz/stop` cancels manual/zoom deadmen before releasing the owner. |
| B9 | ✅ Resolved | `PtzState` plausibility gate now checks both pan and tilt rates. |
| B10 | ✅ Resolved | `Pipeline.kill(False)` now leaves the owner idle so the arbiter re-decides. |
| B11 | ✅ Resolved | `safety.kill_reason` and `safety.last_kill_at_unix_ms` are populated from `pipeline._last_kill`. |
| B12 | ✅ Resolved | `Recorder.start()` now checks that ffmpeg is still alive shortly after spawn. |

---

### B13 — Calibration capture during a session drops the `calibrate` owner ✅ RESOLVED

**Where:** `orin/wavecam/wavecam/control_calibration.py`, `validate_calibration_capture()` lines 718-737; plus `orin/wavecam/wavecam/control_ptz.py`, `release_manual_owner()` lines 59-72.

**Problem:** Commit `ca86830` added special handling so standalone capture endpoints can take over from an active `calibrate` session:

```python
if current == "calibrate" and req.takeover:
    if not self.pipeline.owner.release("calibrate"):
        return self._api.refusal("owner_busy", "Cannot release calibrate owner.")
    self._api._ptz._restore_owner_after_manual = "calibrate"
    if not self.pipeline.owner.request("manual"):
        self.pipeline.owner.request("calibrate")  # restore
        return self._api.refusal("owner_busy", "Cannot claim manual for capture.")
    return None
```

The capture routes in `control_api.py` wrap the capture in `try/finally: api.release_manual_owner(restore_autonomous=True)`. However, `release_manual_owner()` clears `_restore_owner_after_manual` and does **not** restore a saved non-autonomous owner:

```python
def release_manual_owner(self, restore_autonomous: bool = True) -> None:
    with self._lock:
        released = self.pipeline.owner.release("manual")
        self._restore_owner_after_manual = None
        ...
        if released and restore_autonomous and not self.pipeline.owner.killed:
            # Let the arbiter naturally acquire in the next frame
            pass
```

Because `calibrate` is not an autonomous owner, the saved `"calibrate"` value is discarded. After a successful `/api/v1/calibration/heading` (or tilt/zoom/base-lock) call during a session, the owner ends up `idle` instead of `calibrate`. The session is effectively destroyed even though `calibration.active` may still be true in the snapshot.

**Risk:** An operator using the iOS calibration session flow can capture a heading/tilt sample and then find the camera no longer owns `calibrate`. Subsequent session-only operations (e.g., validation steps that require `calibrate` owner) may fail or behave unexpectedly.

**Concrete fix:**

Make `release_manual_owner` restore any saved owner, not just autonomous ones. The simplest safe change is:

```python
# control_ptz.py

def release_manual_owner(self, restore_autonomous: bool = True) -> None:
    with self._lock:
        saved = self._restore_owner_after_manual
        released = self.pipeline.owner.release("manual")
        self._restore_owner_after_manual = None
        self._manual_pan_tilt_active = False
        if hasattr(self.pipeline, "arbiter"):
            self.pipeline.arbiter.reset_vision_state()
        if released and not self.pipeline.owner.killed:
            if saved is not None:
                # Restore a specifically saved owner (e.g., calibrate session).
                self.pipeline.owner.request(saved)
            elif restore_autonomous:
                # Let the arbiter naturally acquire in the next frame.
                pass
```

This preserves the existing autonomous-resume behavior while also restoring a `calibrate` session after a standalone capture.

**Test to add:** In `tests/test_control_api.py`, extend the calibration session tests to call a capture endpoint (with `takeover=true`) while `owner == "calibrate"`, then assert the owner returns to `"calibrate"` after the capture succeeds.

---

**Resolution:** Fixed in commit `4a25265` by Codex/Claude. `release_manual_owner` now restores the saved `calibrate` owner while still requiring autonomous owners (`vision_follow`, `gps_tracker`) to re-earn ownership via the arbiter. Regression test added: session + heading capture + tilt capture → owner stays `calibrate`. `python3 -m pytest tests -q` → **419 passed**.

---

*Updated by Kimi Code CLI. No source files were modified.*
