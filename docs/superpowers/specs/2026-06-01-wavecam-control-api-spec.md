# WaveCam Control API Spec

Date: 2026-06-01

Status: design/spec only. No implementation, no service cutover, no live Orin mutation.

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

Feasibility: 8/10. Status: unimplemented but fits the existing FastAPI `orin/wavecam/wavecam/web.py` shape. Confidence: 0.85.

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
- KILL requires the token in v1; do not create unauthenticated stop endpoints unless a later physical LAN threat model accepts that risk.
- Supervisor can use a local-only token with broader service-lifecycle permissions.

Feasibility: 8/10. Status: unvalidated. Confidence: 0.8.

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
- Manual movement requests `manual`; autonomous tracking requests `vision_follow` or `gps_tracker`.
- No endpoint may steal ownership without `force=true` and an operator or supervisor role.
- Agents never become a movement owner; they call the same owner-gated endpoints as the app/dashboard when explicitly authorized.

Feasibility: 9/10. Status: existing `PtzOwner` proves the core gate; API schema needs formalization. Confidence: 0.9.

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
| `stale_state` | client wrote against stale revision | 409 |
| `hardware_error` | camera/PTZ/recorder command failed | 502 |
| `service_error` | systemd/supervisor action failed | 502 |

Every refusal should be safe to show in the app without parsing logs.

## Status Model

`GET /status`

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

Feasibility: 9/10. Status: partially matches existing `/status`; needs expansion. Confidence: 0.85.

## Telemetry

`GET /telemetry`

WebSocket upgrade endpoint.

WebSocket stream of status deltas. The full snapshot remains `GET /status`; WebSocket messages are for UI freshness.

Message types:

```json
{"type":"status","revision":1835,"patch":{"tracking":{"fps":24.9}}}
{"type":"event","severity":"warn","code":"gps_stale","message":"GPS target age 5.4s"}
{"type":"command_ack","request_id":"...","ok":true}
```

Rules:

- Clients reconnect and immediately call `GET /status`.
- No command semantics depend on WebSocket delivery.
- Telemetry loss must not disable REST KILL/PTZ.

Feasibility: 8/10. Status: unimplemented. Confidence: 0.8.

## Preview Feed

`GET /preview.mjpeg`

v1 monitor feed. Uses the `/2` substream/annotated preview, not deliverable recording quality.

Rules:

- 640x360 or similar is acceptable for operator framing.
- It must not block the control loop.
- It may drop frames under load.
- It must be disabled or degraded before it starves tracking.

Related future endpoint:

- `GET /preview/webrtc` or `GET /preview/whep` after `mediamtx` exists.

Feasibility: 9/10 for MJPEG v1. Status: existing `/stream.mjpg` already proves the pattern. Confidence: 0.9.

## Safety Endpoints

### `POST /safety/kill`

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

Feasibility: 10/10. Status: existing `/kill` proves basic behavior. Confidence: 0.9.

### `POST /safety/resume`

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
- Client must call `/session/start` or `/tracking/start` separately.

Feasibility: 9/10. Status: existing `/resume` currently re-acquires `testbed`; this should be changed for production semantics. Confidence: 0.8.

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

### `POST /ptz/stop`

Stop pan, tilt, and zoom. Does not clear KILL.

Request:

```json
{
  "source": "ios_native"
}
```

Rules:

- Always allowed for authenticated clients.
- Releases manual owner.
- Does not release autonomous owner unless `force_release=true` is explicitly provided by operator role.

### `POST /ptz/nudge`

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

### `POST /ptz/velocity`

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

### `POST /ptz/zoom`

Request zoom movement or absolute zoom where backend supports it.

Request:

```json
{
  "requested_owner": "manual",
  "mode": "velocity",
  "value": 0.5,
  "source": "ios_native"
}
```

Allowed modes:

- `velocity`: negative wide, positive tele
- `stop`
- `absolute`: future, if backend supports calibrated absolute zoom

Feasibility: 8/10. Status: basic stop/zoom exists; normalized owner-safe API is unimplemented. Confidence: 0.8.

## Config Endpoints

### `GET /config`

Return current effective configuration and schema metadata.

### `POST /config/hot`

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
- Update live state.
- Persist to config only if `persist=true` is explicitly passed.
- Must not restart WaveCam.

Hot keys v1:

| Key | Range | Restart |
|---|---:|---|
| `ptz.deadzone` | `0.02..0.30` | no |
| `ptz.max_pan_speed` | `1..24` | no |
| `ptz.max_tilt_speed` | `1..20` | no |
| `ptz.invert_pan` | bool | no |
| `ptz.invert_tilt` | bool | no |
| `fusion.lock_threshold` | `0.05..0.95` | no |
| `fusion.unlock_threshold` | `0.05..0.95` | no |
| `color.min_area` | `20..4000` | no |
| `web.show_mask` | bool | no |

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

Feasibility: 8/10 for hot config; 6/10 for staged config. Status: existing `/tune` proves hot path. Confidence: 0.85.

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

### `POST /media/record/start`

Start local recording of RTSP `/1` with remux/copy.

### `POST /media/record/stop`

Stop local recording.

### `GET /media/segments`

List recorded segments.

### `POST /media/export`

Copy selected segments to removable media when SD boot dependency is removed.

Rules:

- Recording is guaranteed local capture.
- Livestream is best-effort and must not block recording or tracking.
- No Orin NVENC assumptions.

Feasibility: 7/10. Status: recorder design exists; service integration pending. Confidence: 0.75.

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

Feasibility: 8/10. Status: auth unimplemented. Confidence: 0.8.

## Implementation Phasing

### Phase 0: Map Existing Endpoints

Existing:

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

Do not break these until the new API is verified. Add `/api/v1/*` alongside them.

### Phase 1: Safety + Status

- `GET /api/v1/status`
- `POST /api/v1/safety/kill`
- `POST /api/v1/safety/resume`
- `GET /api/v1/preview.mjpeg`

Validation:

- Existing dashboard still works.
- KILL from new endpoint latches.
- MJPEG feed does not starve loop.

### Phase 2: PTZ + Hot Config

- `POST /api/v1/ptz/stop`
- `POST /api/v1/ptz/velocity`
- `POST /api/v1/ptz/nudge`
- `POST /api/v1/ptz/zoom`
- `POST /api/v1/config/hot`

Validation:

- Manual deadman stops.
- Hot config does not restart.
- Owner model refuses auto-steal.

### Phase 3: Session + Supervisor

- `POST /api/v1/session/start`
- `POST /api/v1/session/stop`
- `GET /api/v1/supervisor/status`
- service lifecycle via deterministic supervisor only

Validation:

- `wavecam.service` stop path is proven before restart endpoints are enabled.
- Restart does not cause camera jump.

### Phase 4: Calibration + Media

- calibration wizard endpoints
- recorder/segments/export endpoints

Validation:

- Calibration writes are reversible.
- Recorder never blocks tracking.

## Test Plan

Unit tests:

- request schema validation
- role permission matrix
- owner busy refusals
- KILL blocks movement
- hot config allowlist and range validation

Integration tests:

- start app with mock PTZ
- call KILL, RESUME, PTZ, config hot endpoints
- verify status revision increments
- verify telemetry reconnect behavior

Live gates:

- KILL from iOS native path
- KILL from dashboard path
- PTZ velocity deadman
- service stop sends PTZ stop
- no camera jump on restart

## Open Questions

1. Should KILL require auth on a private beach LAN, or should there be a local unauthenticated emergency stop endpoint? Recommendation: require auth v1; revisit after threat model.
2. Should `/safety/resume` merely clear KILL or restore the previous owner? Recommendation: clear only; require explicit tracking restart.
3. Should supervisor service actions live in the same FastAPI app or in a local-only supervisor API? Recommendation: same operator-facing read model, but systemd writes executed by deterministic supervisor.

## Recommendation

Build `/api/v1` alongside the existing WaveCam testbed routes. Start with status, KILL, resume, and preview. Do not expose service restart or agent actions until `wavecam.service` shutdown behavior is live-verified.

This gives the iOS app, dashboard, supervisor, and Codex one shared interface without moving any safety-critical authority out of the deterministic WaveCam core.
