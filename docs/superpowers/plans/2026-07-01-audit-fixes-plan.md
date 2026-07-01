# Audit-Fixes Implementation Plan (2026-07-01)

Source: `docs/PROJECT_AUDIT_2026-07-01.html` (57 findings). This plan is written to be
executed by an agent (Sonnet) or human. Work on branch `claude/project-audit-findings-0tqcfz`.
Baseline: 575 backend tests pass (`python3 -m pytest -q` from repo root), mypy gate green.

Rules: run the backend suite after every wave; stage files explicitly (never `git add -A`);
"committed != deployed" — nothing here touches the live rig until Zack/Codex deploys.

## Status legend
- [DONE-2026-07-01] applied on this branch by Claude
- [MAC] needs a Mac: `cd ios/WaveCam && xcodegen generate && ./build-device.sh` must succeed before install
- [RIG] needs the bench/rig (flash firmware, live verify, deploy)
- [LATER] tracked, intentionally not in this pass

## Wave 1 — Tracking core (orin/wavecam/wavecam/)
- C1 pipeline.py:613 `frame is None` branch: when `cfg.ptz.enabled` and owner is autonomous,
  send `ptz.stop()` + zoom stop once (guard with a flag so it isn't spammed at 10 Hz),
  reset `_last_cmd_key`. Test: fake grabber returning None while a velocity cmd was active.
- H5 fusion.py:214 compute `coasting` BEFORE the unlock-threshold check; keep `_locked` while
  `(now-_last_seen) <= lost_grace_sec` with no candidate; only unlock instantly when a candidate
  exists below `unlock_threshold`; don't wipe `_ema` during grace. Tests: COASTING reachable,
  single-blank-frame keeps lock, grace expiry unlocks.
- H6 pipeline.py:558 + gps_pointing.py: `lead_s = min(fix.age_sec + gps.lead_margin_s, gps.lead_cap_s)`;
  new hot keys `gps.lead_margin_s` (default 0.65) and `gps.lead_cap_s` (default 4.0) in GpsCfg,
  HOT_CONFIG_KEYS, /config snapshot. Keep the >=0.1 m/s speed gate.
- H7 pipeline.py:437 absolute-move gate: send only `if changed` or every >=2.5 s keepalive;
  `record_move()` must not reset `_issue_t` for an unchanged target (pointing_verifier.py).
- H8 controller.py: FOV gain-scheduling — `compute(..., hfov_deg)`; scale speed by `hfov/hfov_ref`
  (ref = widest), deadzone denominated in degrees (`deadzone_deg`, default = old 0.05*wide-FOV);
  pipeline passes hfov from `_fov_at_zoom(latest_zoom)` with staleness fallback to wide.
- M1 pipeline estimator tick: skip `update_gps` when `fix.ts` unchanged (`_last_gps_fix_ts`).
- M2 pipeline: stop calling `calibration_status()` per frame; cache `(valid, confirmed)` and
  refresh at <=1 Hz (time-gated), still thread-safe.
- M3 pipeline.py:311 skip `update_vision` when zoom cache is stale (match update_vision_range gate).
- M4 estimator.py:429 `_scalar_update`: full `(I-Kh)P` via the existing 4x4 helpers (or Joseph form);
  keep symmetry (P = (P+P^T)/2 after update).
- M5 box_ttl: default 0.2 s in config + config.orin.servo.yaml:58; skip TTL-cached person boxes
  when a non-stop PTZ command was sent within the TTL window (pipeline knows `_last_cmd_key`).
- M6 fusion.py: clear `_last_track_id` in the `not coasting` reset branch.
- M7 pipeline/web: count MJPEG clients (web.py increments/decrements on stream open/close via
  SharedState); skip annotate+imencode when 0 clients AND not recording overlay; preallocate tint buffer.
- L1 pointing_verifier.py:72: ignore encoder snapshots with `age is None or age > 1.0`.
- L9 pipeline `_gps_cue`: apply the same <0.5 s encoder / ZOOM_FRESH_SEC gates; fall back to center cue.
- L10 pipeline `_send_cmd/_send_zoom/_send_absolute_cmd`: early-return when killed.
- L12 estimator: `update_vision_range` must call `range_from_bbox_height`; delete unused
  `TrackingArbiter.max_gps_age_sec`.
- M22 new tests/test_capture.py with a fake cv2.VideoCapture: open-fail, frame-freeze
  (frames counter stalls), reconnect_sec honored; plus C1 regression test.

## Wave 2 — GPS ingest, calibration, config (backend, non-pipeline)
- M8 gps_direct_lora.py: honor optional `"spd_ok"`/`"crs_ok"` JSON fields (absent => assume valid,
  backward compatible); when false, null course / zero speed before building NormalizedFix.
- M9 gps_stub.py NormalizedFix: add `h_acc_m: float | None`; parse `hacc_cm/100`; pipeline gates:
  above `gps.max_h_acc_m` (new hot key, default 15.0) withhold gps drive authority and inflate
  `bearing_uncertainty_deg` proportionally for the cue.
- M10 control_calibration.py:147: report `has_base` and real `pose.base_locked` separately
  (keep old key emitting the honest value; document shape change in /calibration).
- M11 control_calibration.py:374,442: average alt only over samples that reported alt_m
  (use `is not None`, not `or 0.0`); fall back 0.0 only when none did.
- M12 control_calibration.py:737: select the heading-uncertainty model by `method` string only.
- M13 config.py: add `show_mask: bool = True` to WebCfg; pipeline inits `state.show_mask` from cfg.
- M14 control_utils.py HOT_CONFIG_KEYS + control_snapshots.py current.estimator: add
  estimator.use_vision_range / subject_height_m / r_range_frac (presets then accept them).
- M21 config.py:103 + config.yaml + config.orin.yaml: default model `yolo11n.pt`; fix comments.
- L2 gps_direct_lora.py `_open_serial`: after N(=5) consecutive failures on the configured path,
  glob `/dev/ttyACM*` and try candidates.
- L3 control_snapshots.py:321 import bearing/haversine from gps_geo; run.py: unknown gps.source
  => raise/log-fatal instead of silently building MeshtasticGps; config.orin.servo.yaml: fix stale
  comment, add explicit `source: direct_lora`, drop `remote_id`.

## Wave 3 — Control API / agent plumbing (after waves 1-2 merge)
- C2 control_api.py: refuse `/agent/arm` + `/agent/chat` when `auth.enabled` is false unless
  `agent.allow_unauthenticated: true` (new cfg, default false on servo yaml, true on desktop
  config.yaml so the testbed keeps working); boot log states auth state. auth.py: fail CLOSED
  (raise) when WAVECAM_AUTH_FILE is set but unreadable/invalid. [was "security appendix"]
- H1 agent_session.py: run provider CLI via Popen in its own process group, keep handle;
  `agent_kill()` (control_system.py) SIGKILLs the group; add /system/restart to forbidden list.
- M15 agent_session.py: per-provider lock around resume/read/write of `_session_ids`
  (or reduce the chat semaphore to 1).
- M16 control_api.py kill path: run `media.stop_for_safety()` on a daemon thread.
- M19(backend) /status: add `agent: {armed, ttl_remaining_s}` block.
- L5 web.py:603 legacy /tune: call the same persist path as /config/hot; make /config/hot's
  `persist` field a no-op accepted key (documented) instead of a refusal.
- L6 control_system.py agent refusals: use `api.refusal(...)` (4xx) shapes; fix the system-prompt
  sentence that claims all refusals are 200s.
- L7 control_config.py revision check + control_system.py restart scheduling: perform
  check+apply/schedule under `api._lock`.
- L4 control_api.py: gate /sensors/phone ingest as CONFIG. [was appendix]
- L8 [LATER] short-lived stream token for ?token= on MJPEG (only matters once auth is on).

## Wave 4 — Ops / CI / firmware / docs
- H2 ops/watchdog.sh: treat missing `gps_reader` component as OK (fixes false restart loop);
  skip restarts while `/api/v1/status` reports `safety.killed=true`; [RIG][LATER] persist KILL
  latch across service restarts (backend change, needs bench verify before deploy).
- H3 ops/watchdog.sh: optional WAVECAM_WATCHDOG_TOKEN Authorization header; `--fail` + ok-check
  on record/stop; distinguish 401 from unreachable.
- H4 deploy.sh: post-deploy fail unless `/health` shows `components.loop.ok` true AND
  `capture.detail.fps > 0`; watchdog `loop_dead_twice` rule (gated on not killed). [RIG] final verify.
- H9 firmware base/main.cpp: emit instantaneous `lat_raw/lon_raw` (or `"raw":{lat,lon}`) alongside
  the settle mean; base_drift.py consumes raw when present. [RIG] flash + bench verify.
- M8(fw) base/main.cpp: emit `"spd_ok"`/`"crs_ok"` from PKT_FLAG_*. [RIG] flash.
- H14 .github/workflows: add ios-build job (macos-latest, xcodegen + xcodebuild
  CODE_SIGNING_ALLOWED=NO, paths ios/**) and firmware job (platformio pio run -e tracker -e base,
  paths firmware/direct-lora/**).
- M23 mypy.ini: add ptz_visca.py, controller.py to strict files (fix any fallout).
- M24 CLAUDE.md: note the three .claude/*.md pointers are machine-local (not in repo).
- L16 .gitignore: replace blanket `*.md` ban with `!firmware/**/*.md`, `!ios/**/*.md` re-includes.
- L17 README.md: fix stale footer ("LoRa GPS future work"), add CALIBRATE/estimator/agent features.

## Wave 5 — iOS (all [MAC]: must build via xcodegen+xcodebuild before device install)
- H10 WaveCamClient.swift: decode ok/code/message on ALL post() callers (kill, resume, record,
  configHot, setAgentArm, summonAgent, systemRestart, savePreset) via applyControlResponse;
  kill(): treat ok==false like catch (clear killInFlight/optimisticKilled, set lastCommandError).
- H11 post(): per-request timeout param; 120 s for agent/chat + agent/summon.
- H12 PTZManualController: throttle sendVelocity/updateZoom (send if >=100 ms since last, else
  coalesce latest; always send final stop).
- H13 Sources-Watch/WatchClient.swift: allow .timedOut/.networkConnectionLost failover for
  kill/resume POSTs; show "STOP NOT CONFIRMED" state on failure.
- M18 WCConfig: tolerant init(from:) in an extension, decodeIfPresent ?? default for sub-objects.
- M19(client) reconcile agentArmed from /status `agent` block (decodeIfPresent — feature-detected).
- M20 AgentView: dismiss fullScreenCover on client.effectiveKilled.
- L13 AgentView: cancel the summon poll Task in onDisappear.
- L14 WaveCamClient: markConnected only after the 200-check (getWithFallback/post/delete).
- L15 SessionLogView/client: dedupe events on append (t <= cursor) and cap array at 500.

## Deferred / tracked [LATER]
- H2 KILL-latch disk persistence (backend + bench verify).
- L8 stream-scoped tokens.
- L11 VISCA inquiry transaction lock (ptz_state docstring already flags it; touch with bench access).
- Post-deploy LOCKED-state fps check automation beyond H4's idle check (needs a live target).
- Deploy + on-rig verification of every backend change in this plan (Codex/Zack lane).
