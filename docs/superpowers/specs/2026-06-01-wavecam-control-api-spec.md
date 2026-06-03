# WaveCam Control API Spec

Date: 2026-06-01

Updated: 2026-06-03

Status: implemented in `orin/wavecam/wavecam/control_api.py` beside the legacy Orin web console. This document now tracks current `/api/v1` runtime behavior plus future surfaces still not built.

## Purpose

The Control API is the single seam between operator surfaces and the deterministic WaveCam core.

Clients:

- iOS/iPadOS app native overlays
- Orin web dashboard
- deterministic `wavecam-supervisor.service`
- on-demand Codex diagnostics

Non-clients:

- real-time servo internals
- direct VISCA movement from any app, web page, supervisor, or agent

Feasibility: 9/10. Status: implemented for safety, status, preview, PTZ, media, hot config, restart, and agent summon; session/calibration/export endpoints remain future work. Confidence: 0.9.

## Design Rules

1. The Orin is the authority.
2. The API exposes high-level intents, not raw motor access.
3. KILL is always accepted, sticky, and lowest-latency.
4. `PtzOwner` remains the owner gate for all camera movement.
5. Hot tuning uses live mutable state and must not restart WaveCam.
6. Structural config changes require validation and an explicit restart gate.
7. Every write endpoint returns the resulting status or a machine-readable refusal.
8. The API must be boring enough for the dashboard, iOS app, supervisor, and Codex to share without special cases.

## API Shape

Base URL examples:

- Local Orin: `http://192.168.55.1:8088/api/v1`
- Camera LAN / beach LAN: `http://<orin-ip>:8088/api/v1`

Transport:

- REST for commands
- WebSocket for telemetry push
- MJPEG for v1 monitor feed
- Optional WebRTC/WHEP later through `mediamtx`, not v1

Auth:

- v1: LAN bearer token in `Authorization: Bearer <token>`
- Token stored on Orin in a root/user-readable config file, never committed.
- Auth is config-gated and disabled by default to preserve bring-up/dev behavior when no auth file is configured.
- When auth is enabled, KILL requires a `safety`-capable token in v1; do not create unauthenticated stop endpoints unless a later physical LAN threat model accepts that risk.
- Supervisor can use a local-only token with broader service-lifecycle permissions.
- Legacy mutation routes (`/kill`, `/resume`, `/ptz/*`, `/tune`) also require roles when auth is enabled.

Feasibility: 9/10. Status: implemented and covered by backend tests. Confidence: 0.9.

## Ownership Model

PTZ ownership is a first-class API concept. Every command response echoes the current PTZ owner, KILL latch, and state revision so clients can render the real authority state instead of guessing from button state.

Owner values v1:

| Owner | Meaning |
|---|---|
| `idle` | no active movement owner |
| `manual` | operator joystick/nudge owns PTZ |
| `vision_follow` | vision tracker owns PTZ |
| `gps_tracker` | GPS acquisition/reacquisition owns PTZ |
| `testbed` | current testbed runner owns PTZ until replaced |

Rules:

- KILL bypasses ownership and forces `owner=idle`.
- RESUME clears KILL but does not restore a previous owner.
- Manual pan/tilt requests `manual`; current Start Auto requests autonomous owner `testbed`.
- Manual pan/tilt cannot steal active autonomous ownership unless the request explicitly sets `takeover=true`.
- Manual zoom during autonomous tracking does not steal pan/tilt ownership; it suppresses Cinematic Zoom briefly and uses a zoom deadman stop.
- Agents never become a movement owner; they call the same owner-gated endpoints as the app/dashboard when explicitly authorized.

Feasibility: 9/10. Status: implemented around `PtzOwner`; production owner names may still be renamed later. Confidence: 0.9.

## Common Response Model

Successful command response:

```json
{
  "ok": true,
  "request_id": "20260601T130000.123Z-abc123",
  "status": {
    "state": "TRACKING",
    "ptz": {
      "owner": "vision_follow"
    },
    "killed": false
  }
}
```

Refusal response:

```json
{
  "ok": false,
  "code": "killed",
  "message": "KILL is latched; resume before movement commands.",
  "status": {
    "state": "KILLED",
    "ptz": {
      "owner": "idle"
    },
    "killed": true
  }
}
```

Error code set:

| Code | Meaning | HTTP |
|---|---|---:|
| `unauthorized` | missing/invalid token | 401 |
| `forbidden` | token lacks role for action | 403 |
| `invalid_request` | schema/range validation failed | 422 |
| `killed` | KILL latch blocks action | 409 |
| `owner_busy` | another owner holds PTZ; no auto-steal | 409 |
| `not_ready` | required subsystem not ready | 409 |
| `revision_conflict` | client wrote against stale revision | 409 |
| `hardware_error` | camera/PTZ/recorder command failed | 502 |
| `service_error` | systemd/supervisor action failed | 502 |

Every refusal should be safe to show in the app without parsing logs.

## Status Model

`GET /api/v1/status`

Returns the complete operator state snapshot.

```json
{
  "revision": 1834,
  "time_unix_ms": 1780309200123,
  "session": {
    "state": "TRACKING",
    "mode": "vision_gps",
    "started_at_unix_ms": 1780308900000
  },
  "safety": {
    "killed": false,
    "kill_reason": null,
    "last_kill_at_unix_ms": null
  },
  "ptz": {
    "owner": "vision_follow",
    "enabled": true,
    "pan_tilt_cmd": "p4/t0",
    "zoom_state": "hold"
  },
  "tracking": {
    "locked": true,
    "state": "LOCKED",
    "confidence": 0.78,
    "fps": 24.7,
    "has_color": true,
    "has_person": true,
    "matched": true
  },
  "gps": {
    "source": "lora",
    "target_age_sec": 0.9,
    "base_age_sec": 120.0,
    "distance_m": 184.2,
    "bearing_deg": 247.1,
    "stale": false
  },
  "media": {
    "recording": true,
    "segment_name": "20260601-123000.mp4",
    "free_gb": 377.8
  },
  "services": {
    "wavecam": "running",
    "gps_server": "running",
    "dashboard": "running",
    "cloudflared": "running",
    "supervisor": "running"
  },
  "network": {
    "camera_lan": true,
    "uplink": true,
    "cloudflare": true
  }
}
```

Feasibility: 9/10. Status: implemented as `/api/v1/status`; legacy `/status` still exists for the web console. Confidence: 0.9.

## Telemetry

`WS /api/v1/telemetry`

WebSocket upgrade endpoint.

WebSocket stream of status snapshots. The full snapshot remains `GET /api/v1/status`; WebSocket messages are for UI freshness.

Message types:

```json
{"type":"status","revision":1835,"status":{"tracking":{"fps":24.9}}}
```

Rules:

- Clients reconnect and immediately call `GET /api/v1/status`.
- No command semantics depend on WebSocket delivery.
- Telemetry loss must not disable REST KILL/PTZ.

Feasibility: 8/10. Status: implemented as full snapshot push, not delta/event streaming. Confidence: 0.85.

## Preview Feed

`GET /api/v1/preview.mjpeg`

v1 monitor feed. Uses the `/2` substream/annotated preview, not deliverable recording quality.

Rules:

- 640x360 or similar is acceptable for operator framing.
- It must not block the control loop.
- It may drop frames under load.
- It must be disabled or degraded before it starves tracking.

Related future endpoint:

- `GET /preview/webrtc` or `GET /preview/whep` after `mediamtx` exists.

Feasibility: 9/10 for MJPEG v1. Status: implemented; legacy `/stream.mjpg` remains. Confidence: 0.9.

## Safety Endpoints

### `POST /api/v1/safety/kill`

Always stop pan, tilt, zoom, drop ownership to idle, latch KILL.

Request:

```json
{
  "reason": "operator",
  "source": "ios_native"
}
```

Rules:

- Idempotent.
- Must be the highest-priority write endpoint.
- Must not wait for recorder, GPS, model, or dashboard state.
- Should return after stop commands have been issued, not after long confirmation.

Feasibility: 10/10. Status: implemented; also stops active recording through the media adapter. Confidence: 0.95.

### `POST /api/v1/safety/resume`

Clear KILL latch. Does not automatically start tracking unless explicitly requested.

Request:

```json
{
  "source": "ios_native"
}
```

Rules:

- Clears KILL.
- Owner remains `idle`.
- Client must explicitly call Start Auto through `/api/v1/ptz/auto` to resume autonomous tracking.

Feasibility: 9/10. Status: implemented for `/api/v1`; legacy `/resume` remains for the web console. Confidence: 0.9.

## Session Endpoints

### `POST /session/start`

Start an operator session.

Request:

```json
{
  "mode": "vision_gps",
  "record": true,
  "stream": false,
  "profile": "foil-default"
}
```

Allowed modes:

- `vision_only`
- `vision_gps`
- `calibration`
- `manual`

Rules:

- Refuse while killed.
- Refuse if camera feed is not connected.
- Optional: refuse if calibration is missing for GPS modes.
- If `record=true`, start recorder before tracking.

Feasibility: 7/10. Status: needs session state layer. Confidence: 0.75.

### `POST /session/stop`

Stop tracking and optionally recording/streaming.

Request:

```json
{
  "stop_recording": true,
  "stop_streaming": true,
  "park_camera": false
}
```

Rules:

- Must send PTZ stop.
- Should not clear KILL if KILL is active.
- If `park_camera=true`, refuse while KILL is active unless park is implemented as a known-safe preset and approved.

Feasibility: 8/10. Status: unimplemented as session abstraction. Confidence: 0.8.

## Tracking Endpoints

### `POST /tracking/start`

Request autonomous tracking ownership.

Request:

```json
{
  "owner": "vision_follow",
  "mode": "vision_gps"
}
```

Rules:

- Refuse while killed.
- Refuse if PTZ owner is busy.
- Owner must be one of `vision_follow`, `gps_tracker`, or future validated autonomous owners.

### `POST /tracking/stop`

Release autonomous ownership and send PTZ stop.

Request:

```json
{
  "owner": "vision_follow"
}
```

Rules:

- Only current owner may release itself.
- Operator may force release with elevated role.

Feasibility: 8/10. Status: PtzOwner exists; production owner names need alignment. Confidence: 0.85.

## PTZ Endpoints

### `POST /api/v1/ptz/stop`

Stop pan, tilt, and zoom. Does not clear KILL.

Request:

```json
{
  "hold": true,
  "source": "ios_native"
}
```

Rules:

- Always allowed for authenticated clients.
- Stops pan, tilt, and zoom.
- With default `hold=true`, holds owner as `manual` so autonomous tracking cannot restart movement until Start Auto.
- With `hold=false`, releases manual owner if manual owns PTZ.

### `POST /api/v1/ptz/auto`

Request autonomous tracking ownership.

Rules:

- Refuse while killed.
- Cancels manual and zoom deadmen.
- Sends pan/tilt stop and zoom stop before starting owner `testbed`.
- Releases the current owner before requesting `testbed`.

### Future: `POST /ptz/nudge`

Small manual nudge in degrees or normalized units.

Request:

```json
{
  "requested_owner": "manual",
  "pan_deg": -1.0,
  "tilt_deg": 0.0,
  "speed": 0.4,
  "source": "ios_native"
}
```

Rules:

- Refuse while killed.
- Request manual owner.
- Clamp pan/tilt/speed to configured limits.
- Release owner after nudge completes or after deadman timeout.
- Status: not implemented in current `/api/v1`; use `/api/v1/ptz/velocity` for joystick/deadman control.

### `POST /api/v1/ptz/velocity`

Manual joystick/deadman control.

Request:

```json
{
  "requested_owner": "manual",
  "pan": -0.35,
  "tilt": 0.0,
  "zoom": 0.0,
  "deadman_ms": 800,
  "source": "ios_native"
}
```

Rules:

- Refuse while killed.
- Request manual owner.
- Values are normalized `-1.0..1.0`.
- If no fresh velocity command arrives before `deadman_ms`, send stop and release manual owner.
- This is the preferred iOS joystick endpoint.

### `POST /api/v1/ptz/zoom`

Request zoom movement or absolute zoom where backend supports it.

Request:

```json
{
  "requested_owner": "manual",
  "mode": "velocity",
  "value": 0.5,
  "deadman_ms": 800,
  "source": "ios_native"
}
```

Allowed modes:

- `velocity`: negative wide, positive tele
- `absolute`: future, if backend supports calibrated absolute zoom

Rules:

- Refuse while killed.
- Only `requested_owner=manual` and `mode=velocity` are accepted in v1.
- When no autonomous owner holds PTZ, manual zoom claims manual owner and schedules a deadman for nonzero zoom.
- When autonomous owner `testbed` holds PTZ, manual zoom does not steal pan/tilt ownership; it suppresses Cinematic Zoom and schedules a zoom-only deadman stop.

Feasibility: 9/10. Status: implemented. Confidence: 0.9.

## Config Endpoints

### `GET /api/v1/config`

Return current effective configuration and schema metadata.

### `POST /api/v1/config/hot`

Apply live-safe tuning without restart.

Request:

```json
{
  "revision": 1835,
  "patch": {
    "ptz.deadzone": 0.08,
    "ptz.max_pan_speed": 10,
    "fusion.lock_threshold": 0.60,
    "fusion.unlock_threshold": 0.35,
    "color.min_area": 60,
    "web.show_mask": true
  }
}
```

Rules:

- Only allow known hot keys.
- Validate ranges.
- Validate the whole batch before mutating live state.
- A failed batch returns refusal without changing revision or live config.
- If `revision` is supplied and stale, return `409 revision_conflict` without mutation.
- `persist=true` is not supported in v1 and returns `422 invalid_request` without mutation.
- Must not restart WaveCam.
- YAML persistence remains a manual config-file path.

Hot keys v1:

| Key | Range | Restart |
|---|---:|---|
| `ptz.deadzone` | `0.02..0.30` | no |
| `ptz.max_pan_speed` | `1..24` | no |
| `ptz.max_tilt_speed` | `1..20` | no |
| `ptz.min_speed` | `1..8` | no |
| `ptz.command_min_interval` | `0.01..0.50` | no |
| `ptz.ff_gain` | `0.0..1.0` | no |
| `ptz.ff_deadzone_mult` | `1.0..4.0` | no |
| `ptz.invert_pan` | bool | no |
| `ptz.invert_tilt` | bool | no |
| `ptz.cinematic_zoom_enabled` | bool | no |
| `ptz.zoom_target_frac` | `0.2..0.8` | no |
| `ptz.zoom_deadband` | `0.01..0.30` | no |
| `ptz.zoom_max_speed` | `1..7` | no |
| `fusion.lock_threshold` | `0.05..0.95` | no |
| `fusion.unlock_threshold` | `0.05..0.95` | no |
| `fusion.require_person` | bool | no |
| `fusion.match_dist` | `20..500` | no |
| `fusion.person_aim_x` | `0.0..1.0` | no |
| `fusion.person_aim_y` | `0.0..1.0` | no |
| `color.preset` | supported preset name | no |
| `color.min_area` | `1..500000` | no |
| `color.max_area` | `100..1000000` | no |
| `color.morph_kernel` | `1..31` | no |
| `detector.conf` | `0.05..0.95` | no |
| `detector.imgsz` | `160..1280` | no |
| `detector.person_class` | `0..79` | no |
| `detector.every_n` | `1..30` | no |
| `detector.box_ttl_sec` | `0.1..5.0` | no |
| `web.show_mask` | bool | no |
| `web.jpeg_quality` | `30..95` | no |

### `POST /config/staged`

Stage structural config that requires restart.

Examples:

- camera source
- detector model path
- detector engine path
- camera AI off-path
- web bind port

Rules:

- Validate schema.
- Write staged config.
- Do not restart automatically unless `apply=restart` and operator confirms.
- Supervisor should show `restart_required=true`.

Feasibility: 9/10 for hot config; 6/10 for staged config. Status: hot config is implemented; staged config is not. Confidence: 0.9.

## Calibration Endpoints

### `GET /calibration/status`

Return base lock, heading, tilt, zoom/FOV calibration, and last dry-run result.

### `POST /calibration/base-lock`

Lock or refresh camera/base GPS position.

### `POST /calibration/heading-landmark`

Save heading offset from a known landmark.

### `POST /calibration/dry-run`

Point to a target solution without entering full tracking.

Rules:

- Dry-run must obey KILL and sun keep-out.
- Calibration writes must be versioned and reversible.
- Bad GPS accuracy must refuse base-lock.

Feasibility: 6/10. Status: calibration design exists; endpoint implementation pending GPS/LoRa integration. Confidence: 0.7.

## Media Endpoints

### `GET /api/v1/media/status`

Return recorder status, current segment name, latest segment list, total recorded size, and free disk space when the recorder is configured.

### `POST /api/v1/media/record/start`

Start local recording of RTSP `/1` with remux/copy.

### `POST /api/v1/media/record/stop`

Stop local recording.

### Future: `GET /api/v1/media/segments`

List recorded segments.

### Future: `POST /api/v1/media/export`

Copy selected segments to removable media when SD boot dependency is removed.

Rules:

- Recording is guaranteed local capture.
- Livestream is best-effort and must not block recording or tracking.
- No Orin NVENC assumptions.

Feasibility: 8/10. Status: recorder status/start/stop implemented; segment listing/export still future. Confidence: 0.85.

## Supervisor Endpoints

These are privileged. The main app should show their state but not expose destructive actions casually.

### `GET /supervisor/status`

Return supervisor health, restart counters, last action, last refusal.

### `POST /supervisor/service`

Request service lifecycle action.

Request:

```json
{
  "service": "wavecam.service",
  "action": "restart",
  "reason": "operator_changed_structural_config"
}
```

Rules:

- Refuse restart while camera is moving unless KILL is active or operator confirms.
- Apply KILL before restart if configured.
- Rate-limit restart loops.
- Only deterministic supervisor may call systemd.

Feasibility: 7/10. Status: design only. Confidence: 0.75.

## Roles

| Role | Intended client | Can KILL | Can PTZ | Can config hot | Can service restart |
|---|---|:--:|:--:|:--:|:--:|
| `operator` | iOS app/dashboard | yes | yes | yes | confirm-gated |
| `viewer` | read-only dashboard | no | no | no | no |
| `supervisor` | local supervisor | yes | no direct PTZ | yes | yes |
| `agent` | on-demand Codex | no direct | only through operator-gated API | propose/apply with gate | propose/trigger with gate |

Agent movement rule:

- Codex must not send raw VISCA.
- If agent control is enabled later, it still uses normal Control API endpoints and remains blocked by KILL, owner model, and role permissions.

Feasibility: 9/10. Status: role gate implemented when auth config is enabled. Confidence: 0.9.

## Current Implementation State

Legacy console routes still present:

- `/`
- `/stream.mjpg`
- `/status`
- `/kill`
- `/resume`
- `/ptz/stop`
- `/ptz/zin`
- `/ptz/zout`
- `/ptz/zstop`
- `/tune`

When auth is enabled, legacy mutation routes require the same role gate as `/api/v1`. Read-only legacy `/status` and `/stream.mjpg` remain bring-up surfaces.

Implemented `/api/v1` routes:

- `GET /api/v1/status`
- `GET /api/v1/preview.mjpeg`
- `WS /api/v1/telemetry`
- `POST /api/v1/safety/kill`
- `POST /api/v1/safety/resume`
- `POST /api/v1/ptz/stop`
- `POST /api/v1/ptz/auto`
- `POST /api/v1/ptz/velocity`
- `POST /api/v1/ptz/zoom`
- `GET /api/v1/media/status`
- `POST /api/v1/media/record/start`
- `POST /api/v1/media/record/stop`
- `GET /api/v1/config`
- `POST /api/v1/config/hot`
- `POST /api/v1/system/restart`
- `POST /api/v1/agent/summon`

Future/not implemented:

- `/api/v1/session/*`
- `/api/v1/tracking/*`
- `/api/v1/ptz/nudge`
- `/api/v1/calibration/*`
- recorder segment listing/export endpoints beyond start/stop/status

## Test Plan

Unit tests:

- request schema validation
- role permission matrix
- owner busy refusals
- KILL blocks movement
- hot config allowlist, range validation, atomic batch failure, stale revision rejection, and `persist=true` rejection

Integration tests:

- start app with mock PTZ
- call KILL, RESUME, PTZ, media, config hot, restart, and agent summon endpoints
- verify status revision increments
- verify telemetry reconnect behavior

Live gates:

- KILL from iOS native path
- KILL from dashboard path
- PTZ velocity and zoom deadmen
- service stop sends PTZ stop
- no camera jump on restart

## Open Questions

1. Should KILL require auth on a private beach LAN, or should there be a local unauthenticated emergency stop endpoint? Recommendation: require auth v1; revisit after threat model.
2. Should `/safety/resume` merely clear KILL or restore the previous owner? Recommendation: clear only; require explicit tracking restart.
3. Should supervisor service actions live in the same FastAPI app or in a local-only supervisor API? Recommendation: same operator-facing read model, but systemd writes executed by deterministic supervisor.

## Recommendation

Build `/api/v1` alongside the existing WaveCam testbed routes. Start with status, KILL, resume, and preview. Do not expose service restart or agent actions until `wavecam.service` shutdown behavior is live-verified.

This gives the iOS app, dashboard, supervisor, and Codex one shared interface without moving any safety-critical authority out of the deterministic WaveCam core.
