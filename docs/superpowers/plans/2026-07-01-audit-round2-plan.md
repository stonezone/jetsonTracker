# Audit Round-2 Plan — re-review of the Round-1 fixes (2026-07-01)

After the 57-finding audit (`docs/PROJECT_AUDIT_2026-07-01.html`) was fixed across 5 waves
(commits `9391c79`, `370ffcd`, `8b22d30`, `5dc9168`, `b4757d5`), a second adversarial review of
those diffs was run (5 per-wave verification agents + a fresh-eyes sweep). It confirmed the vast
majority of Round-1 fixes are correct, but found **22 new/residual issues** — regressions the fixes
introduced, half-fixes, and cross-wave interactions. This plan captures every one with a concrete
fix, targeted for **Sonnet-5 agents** to execute. Branch: `claude/project-audit-findings-0tqcfz`.

Baseline at plan time: **690 backend tests pass, mypy clean.** "Committed != deployed."

## Round-1 verification summary (what the re-review confirmed)
- **Wave 1 (tracking):** C1,H5,H6,M1,M2,M3,M4,M6,M7,L1,L9,L10,L12,M22 = FIXED. H7,H8,M5 = PARTIAL (see R1,R2,R4).
- **Wave 2 (GPS/config):** M10,M11,M12,M13,M14,M21,L3,box_ttl = FIXED. M8,M9 = PARTIAL (R7,R8); H9,L2 introduced regressions (R6,R9).
- **Wave 3 (API/agent):** H1,L4,L5,L6 = FIXED. M15,M16 fixed-with-caveats (R11,R12,R14); L7 introduced a deadlock (R10); C2 fixed-in-code but missing config half (deploy note).
- **Wave 4 (ops/fw/docs):** H2,H3,H4,H9-fw,M8-fw,H14,M24,L17 = FIXED. M23 partial (controller.py not added); L16 residual (orin/wavecam/*.md still ignored). Cross-wave: R2b (loop-beat) below.
- **Wave 5 (iOS):** H10,H11,H13,M18,M19,L14,L15 = FIXED. H12,M20 fixed-with-caveats; L13 introduced a regression (R16).

## Execution model — 4 parallel Sonnet-5 agents, disjoint file ownership
Each agent owns a non-overlapping set of files so they can run concurrently in the shared tree.
The ONE cross-agent dependency (R6) is handled defensively (getattr fallback). Rules for every agent:
- Read each file FULLY before editing; match existing style; comments only for non-obvious constraints.
- Do NOT run the full suite (a parallel agent is editing other files). Run ONLY your own new tests
  (`test_audit_r2_<area>_*.py`) + targeted existing tests for your files. The coordinator runs the
  authoritative full `pytest` + `mypy` after all agents land.
- Add regression tests for each fix. Do NOT git commit. Stay strictly in your file list.

---

## Agent A — pipeline & servo
**Files:** `orin/wavecam/wavecam/pipeline.py`, `controller.py`, `fusion.py`, `ptz_visca.py`, `config.yaml`

- **R1 — HIGH — H6 defeats H7 for a moving subject.** `pipeline.py:658-667` recomputes `lead_s` from
  `fix.age_sec` every frame (age is recomputed each `get_fix()`), so the absolute target creeps 1-4
  counts/frame → 22-30 "changed" sends/sec → `_send_absolute_cmd` spams `pan_tilt_absolute` at frame
  rate (re-triggering the motion profile mid-settle) and `record_move` resets `_issue_t` every frame,
  re-starving the PointingVerifier for any subject moving ≥~0.5 m/s.
  **Fix:** freeze `lead_s` per fix — dedupe on `fix.ts` (compute lead once when a new fix arrives,
  like M1's `_last_gps_fix_ts`) OR quantize `lead_s` to 0.5 s steps; AND in `_send_absolute_cmd` treat
  `|Δpan|,|Δtilt| ≤ POINTING_TOLERANCE_ENC` as "unchanged"; restore `command_min_interval` as a floor
  on changed sends. Test with a moving-subject sim: changed-sends/sec must drop to ≤~2 and the verifier
  must be able to fire.
- **R2 — HIGH — H8 deadzone is unbounded; servo goes dead at full tele.** `controller.py:104`
  `dz = cfg.deadzone / fov_scale`: at 3.4°/55° tele, rig `deadzone:0.05` → dz_norm=0.81 (no correction
  until 81% to frame edge); checked-in `0.08` → dz_norm=1.29 > 1 (servo NEVER moves at tele). This
  traded hunting for no-centering at exactly the long-range zooms the project exists for.
  **Fix:** cap the scaled deadzone, e.g. `dz = min(cfg.deadzone / fov_scale, 0.25)`. Add a test at tele
  asserting a mid-frame-edge target produces a non-zero pan command.
- **R3 — MEDIUM — `Pipeline._stop` shadows `threading.Thread._stop`.** `pipeline.py:216`
  `self._stop = threading.Event()` on a `Thread` subclass; `run.py` `shutdown_pipeline` → `pipe.join()`
  raises `TypeError: 'Event' object is not callable`, so every clean SIGTERM/SIGINT shutdown dies with a
  traceback (skips the clean-exit path). This is the exact bug M22 fixed in capture.py.
  **Fix:** rename `_stop` → `_stop_evt` throughout pipeline.py (mirror `capture.py`). Test `join()` after stop.
- **R4 — MEDIUM — M5 stale-box skip misses absolute & manual pans.** `pipeline.py:776-786` only checks
  the velocity `_last_cmd_key`; GPS absolute slews (`:503`) and manual PTZ never set it, so stale person
  boxes are still reused in image coords during the GPS→vision handoff slew the finding named.
  **Fix:** add `_last_abs_cmd_time` (set in `_send_absolute_cmd`) and the manual-send timestamp to the
  `_panning` predicate; a box captured before the most recent non-stop motion of ANY kind is skipped.
- **R5 — MEDIUM — `ViscaIP._send` has no exception guard.** `ptz_visca.py:37-39` bare `sendto`; an
  `OSError` (ENETUNREACH/EHOSTUNREACH when the camera LAN drops — the same event that causes an RTSP
  dropout) propagates through any `ptz.*` call in the vision loop (including C1's `_stop_for_no_video`
  and the kill-path `ptz.stop()`), exits `_run()`, kills the pipeline thread → zombie rig.
  **Fix:** wrap `_send` in `try/except OSError` (log/record once, swallow) so a transient network error
  can't kill the loop. Test that a raising socket doesn't propagate.
- **R6-A (coordinated) — pipeline side of the H9 regression fix.** In `_update_base_drift`
  (`pipeline.py:612`) call the raw camera position, defensively:
  `get_cam = getattr(self.gps, "get_camera_position_raw", None) or getattr(self.gps, "get_camera_position", None)`.
  Agent B adds `get_camera_position_raw()`; the fallback keeps this correct regardless of merge order.
- **Note N1** — `config.yaml:62` still ships `box_ttl_sec: 0.6` (desktop only). Set to `0.2` to match
  the code default and the servo yaml.

## Agent B — GPS ingest
**Files:** `orin/wavecam/wavecam/gps_direct_lora.py`, `gps_stub.py`

- **R6-B — HIGH — H9 degraded CALIBRATE base position (shared-seam regression).** Wave 2 re-pointed the
  shared `get_camera_position()` at the raw instantaneous fix, but 6+ consumers (calibration base-lock
  `control_calibration.py:868-870`, `lock_location` live-base `:345`, `control_api.py:561,950,968`,
  `control_snapshots.py:343`, the no-base GPS-pointing fallback `pipeline.py:642`) genuinely needed the
  firmware settle mean; raw is single-shot, ~2-3 m scatter, and bypasses the `stable` gate → a pre-settle
  base-lock corrupts every GPS slew (3 m ≈ 1-2° bearing at 150 m).
  **Fix:** add `get_camera_position_raw()` (returns `_cam_raw`, fix-gated) and `get_camera_age_raw()`;
  **restore** `get_camera_position()`/`get_camera_age()` to return the settled mean (revert the Wave-2
  change so all calibration/pointing/snapshot consumers get the mean back automatically). Only the drift
  monitor (Agent A's R6-A line) uses the raw variant. Tests: mean vs raw seams return the right series.
- **R7 — MEDIUM — M9 h-acc gate is inert in its target scenario.** `gps_direct_lora.py:300-301` maps
  firmware `hacc_cm=0` (memset-0 when fix/hdop briefly invalid — the post-wipeout reacquisition case) to
  `h_acc_m=0.0`, which passes the `≤max_h_acc_m` gate as *perfect* accuracy, so GPS drive is never withheld.
  **Fix:** `h_acc_m = hacc_cm/100.0 if hacc_cm > 0 else None` (None = unknown; the gate already treats
  None as "don't withhold", so this at least stops the false-perfect; consider also gating None when sats<4).
- **R8 — LOW/MED — M8 invalid course still reported as due-north.** `gps_direct_lora.py:296` maps
  `crs_ok=False` to `course=0.0`; `NormalizedFix.course` is non-Optional and `pipeline.py:651` feeds it to
  `GeoPoint.course_deg`, so `predict_lead` leads the subject due north when course is invalid but speed
  isn't (slow drift, common).
  **Fix:** make `NormalizedFix.course: Optional[float]`, set `None` when `crs_ok` is false
  (`predict_lead` already returns unchanged on `course_deg is None`); update the dataclass in `gps_stub.py`.
- **R9 — LOW/MED — L2 serial glob can latch the wrong ACM device forever.** `_candidate_paths` will open
  any `/dev/ttyACM*` (Arduino/Nucleo/debug probe) after 5 failures; a device that opens but emits nothing
  keeps `readline()` returning `b""` with no exception, so the reader never re-scans and never finds the
  real Wio when it re-enumerates.
  **Fix:** after opening a glob-discovered (non-configured) port, require ≥1 parseable base/seq JSONL line
  within ~10 s before accepting it; else close and keep cycling. (A `/dev/serial/by-id/*Seeed*` preference
  is a nice-to-have but the validation is the required fix.)

## Agent C — API, agent, recorder
**Files:** `orin/wavecam/wavecam/control_system.py`, `control_ptz.py`, `agent_session.py`,
`recorder.py`, `control_media.py`, `control_api.py`, `control_calibration.py`

- **R10 — HIGH — ABBA deadlock can wedge the whole API incl. `/safety/kill`.** L7's restart fix
  (`control_system.py:80-93`) holds `adapter._lock` across `prepare_for_restart()` → `cancel_manual_deadman()`
  → ptz `_lock` (A→B), while the deadman expiry callbacks (`control_ptz.py:275-296`) hold ptz `_lock` then
  call `_bump_revision` → `adapter._lock` (B→A). Reproduced deterministically: a `/system/restart` posted
  as a deadman expires deadlocks permanently; `/status`, `/config/hot`, calibration, and `/safety/kill`
  (via the same RLock) all hang → KILL unreachable. The same nesting pre-exists in
  `control_calibration.py:240-255`.
  **Fix:** in `zoom_deadman_expired`/`manual_deadman_expired` (control_ptz.py), compute the decision under
  the ptz `_lock` but move `self._bump_revision()` to AFTER releasing it. Apply the same reorder to the
  calibration session-start instance. Add a lock-ordering regression test (two threads, the reproduced scenario).
- **R11 — MEDIUM — H1 agent timeout path can hang forever holding `_session_lock`.**
  `agent_session.py:162-165`: after `TimeoutExpired`, an unbounded `proc.communicate()` under the session
  lock; if a grandchild escaped the process group and holds the stdout pipe, every future agent chat blocks
  forever and consumes both semaphore slots.
  **Fix:** `proc.communicate(timeout=10)`, then a second `killpg` + `proc.wait(timeout=…)` fallback before
  giving up; never block unbounded under the lock.
- **R12 — LOW/MED — KILL response now reports `recording: true` for ~5 s.** M16's async teardown means the
  `/safety/kill` status snapshot still shows recording active while the daemon thread tears ffmpeg down.
  **Fix:** overlay `recording: false` (or a `stopping: true` flag) into the media block of the kill
  response so a client can't read stale "still recording."
- **R13 — LOW — `Recorder` is lock-free; async `stop()` races `start()`/`status()`.** `recorder.py:56-137`:
  KILL→quick RESUME→`media/start` within the ≤5 s teardown window lets the daemon thread's `self._proc = None`
  drop the NEW recording's handle → orphan ffmpeg, recorder reports not-recording, a second start spawns a
  second ffmpeg.
  **Fix:** add a `threading.Lock` around start/stop/status, and have `stop()` clear `_proc` only if it's
  still the exact proc it terminated (compare identity).
- **R14 — LOW — M15 lock is global, not per-provider.** `agent_session.py:208` one lock serializes turns
  across providers; a stuck 120 s claude_code turn blocks a deepseek turn.
  **Fix:** per-provider lock dict keyed by provider (or, minimally, correct the "per-provider" comments/commit
  wording to "global" and accept it). Prefer the per-provider dict.
- **R22 — MEDIUM — recorder `start()` reports success when ffmpeg dies instantly.** `recorder.start()`
  returns `{"ok": True, "started": False, "error": …}` when ffmpeg exits immediately (bad codec/full disk);
  `control_media` passes `ok:true` through and the error string is surfaced nowhere (iOS shows success, no
  recording).
  **Fix:** when `started is False`, return `ok:false` with the error (or set a clear `recording:false` +
  `error` the client can show). Keep the media endpoint's envelope shape. Test the instant-death path.
- **Note N2** — `AGENT_FORBIDDEN_PATHS` (`agent_session.py:56-60`) is referenced nowhere (dead constant);
  `validate_hot_config_request` is now a vestigial always-None call. Either wire the forbidden-paths check
  into the armed-turn path or drop the constant + fix the commit-message overstatement (low priority).

## Agent D — iOS
**Files:** `ios/WaveCam/Sources/*.swift`, `ios/WaveCam/Sources-Watch/*.swift`
(Cannot compile here — conservative, syntax-perfect Swift; re-read edited regions for brace/optional balance.)

- **R16 — MEDIUM — L13 regression: Summon button wedges at "Requesting..." forever.** `AgentView.swift:113`
  + `:66-70`: presenting the chat cover (or switching tab) fires `onDisappear` → cancels `summonTask`; the
  `guard !Task.isCancelled else { return }` leaves `requestState == .requesting`, which disables the Summon
  button permanently (only flipping the Tools segment resets it).
  **Fix:** set `requestState = .idle` in the cancellation guards and/or in `onDisappear` after cancelling.
- **R17 — MEDIUM — 120 s un-failoverable hang for agent/chat & summon on the tether re-probe.**
  `candidateOrder()` lets any request consume the 15 s tether probe; a blackholed tether fails as `.timedOut`
  (not write-failover-allowed), so a chat POST that draws the probe hangs the full 120 s then fails with no
  Wi-Fi retry.
  **Fix:** restrict the tether re-probe to the status poll (a `probeAllowed` flag on the candidate), or pin
  long-timeout POSTs (agent/chat, summon) to the currently-active route.
- **R18 — MEDIUM (watch safety UX) — no STOP retry while "offline".** `WatchStatusView.swift`: after a failed
  STOP the offline screen shows the banner but no STOP button, so the operator can't retry until a GET poll
  succeeds (even though the POST path, 5 s/2 routes, might succeed where the 3 s GET failed).
  **Fix:** add a retry-STOP button under the banner in the offline branch; add a "SENDING STOP…" in-progress state.
- **R19 — LOW — `stopNotConfirmed` can persist after the situation is resolved elsewhere** (killed+resumed
  from the phone, or handled physically). **Fix:** tap-to-dismiss on the banner (fail-safe direction).
- **R20 — LOW — `applyControlResponse` applies `response.status` with no revision check** (`:2159-2161`): a
  slow command response finishing after a newer poll briefly regresses displayed status; wave 5 extended this
  to 8 more paths. **Fix:** accept the piggybacked status only if `status.revision >= current`.
- **R21 — LOW/cosmetic — `ttlRemainingS` is a dead field** (backend key is `ttl_sec`, `agent_session.py:121`);
  the L15 "inclusive since" comment is factually wrong (backend filter is exclusive). Also `applyControlResponse`
  doesn't reconcile `agentArmed`, so an optimistic arm toggle can flicker for ≤1 s under a stale poll.
  **Fix:** populate `ttlRemainingS` from `ttl_sec` (or drop it); fix the comment; reconcile `agentArmed` in
  `applyControlResponse` (or skip the reconcile there and rely on `refresh()`).

---

## Deferred / notes (NOT for the Sonnet agents this round)
- **C2 deploy-config gap (security-adjacent — owner decision):** no yaml has an `agent:` section, so on
  deploy the ASK-CLAUDE web chat + iOS agent tab will 401 until `agent.allow_unauthenticated: true` (or an
  auth file) is set on the rig. Functional break of a feature, but tangled with auth posture — leave to Zack.
- **M23 residual:** add `controller.py` to the mypy strict `files` list (fix any fallout) — separate small task.
- **L16 residual:** `orin/wavecam/*.md` is still gitignored; add `!orin/**/*.md` if wanted.
- **Calibration base-lock freshness gate** and **`gps.dev_path` dead-config for direct_lora** — small
  robustness items; can fold into a later pass.
- **R2b (Wave-1↔Wave-4 interaction):** the NO_VIDEO branch skips the `loop` health beat, so the new
  `loop_dead_twice` watchdog rule could restart a healthy service during a camera outage. FIX RECOMMENDED but
  it spans pipeline.py (Agent A) + watchdog.sh — assign to Agent A as R2b: beat `"loop"` in the NO_VIDEO
  branch (the loop thread is alive; a genuinely dead loop still stops all beats). Added to Agent A scope.

## Verification gates (coordinator, after all agents land)
1. Full `python3 -m pytest -q` from repo root — must stay green and ≥ 690 + new tests.
2. `cd orin/wavecam && python3 -m mypy --config-file mypy.ini` — clean.
3. Spot-verify R1 (changed-sends/sec drops), R2 (tele servo moves), R6 (calibration gets the mean), R10
   (no deadlock) against the code.
4. Commit per-agent (explicit paths, signed), push. iOS + firmware-touching items still need [MAC]/[RIG]
   verification before "live."
