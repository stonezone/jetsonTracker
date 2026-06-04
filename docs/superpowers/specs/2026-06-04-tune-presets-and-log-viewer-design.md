# Tune Presets + Log Viewer (2026-06-04)

Two cross-stack features. Minimal v1, feature-detected on iOS so it ships inert and activates when the Orin (currently down) is back + deployed. Sequence: **presets first, logs second.**

## Feature 1 — Tune Presets (backend-stored)

**Why:** one tune doesn't fit tow-foiling vs wing-foiling vs land/dirtbike chase vs sunny/cloudy. Named presets to save/load/reset.

### Backend (Codex)
- Storage: a JSON file on the Orin (e.g. `/data/wavecam/presets.json`). Built-ins are read-only; custom ones are user CRUD.
- `GET /api/v1/presets` (READ) → `{presets:[{name, builtin:bool, values:{configKey:value}, restart_required:bool}]}`
- `POST /api/v1/presets` (CONFIG) → `{name, values}` (or `capture_current:true` to snapshot live config). Rejects overwriting a builtin name.
- `POST /api/v1/presets/{name}/apply` (CONFIG) → applies the preset's hot keys immediately; returns `{ok, applied:[keys], restart_required:bool, restart_keys:[keys]}`. Restart-only keys are staged, not hot-applied.
- `DELETE /api/v1/presets/{name}` (CONFIG) → custom only.
- `GET /config` advertises `supported.presets = true`.

### Seed built-ins (Claude's best-judgment STARTING points — Zack tunes + resaves)
Differentiated on the axes I can reason about (speed / require_person / deadband / zoom target / confidence); fine color-HSV left at default for Zack to tune per conditions.
- **Default** — current factory values.
- **Tow Foil** (fast, far): `fusion.require_person=false`, `ptz.max_pan_speed=18`, `ptz.max_tilt_speed=12`, `ptz.deadzone=0.10`, `ptz.ff_gain=0.30`, `ptz.zoom_target_frac=0.35`, `fusion.person_aim_y=0.45`.
- **Wing Foil** (medium): `require_person=false`, `max_pan_speed=12`, `max_tilt_speed=9`, `deadzone=0.08`, `ff_gain=0.15`, `zoom_target_frac=0.45`.
- **Land Chase** (dirtbike, close+fast): `require_person=true`, `max_pan_speed=16`, `max_tilt_speed=12`, `deadzone=0.06`, `ff_gain=0.25`, `zoom_target_frac=0.55`, `person_aim_y=0.5`.
- **Sunny** (bright): `detector.conf=0.40`, `web.jpeg_quality=80` (color HSV: Zack tunes).
- **Cloudy** (flat light): `detector.conf=0.30` (color HSV: Zack tunes).

### iOS (Claude)
- A **PRESETS** section at the top of Tune: chips for each preset (tap = apply; active = teal; "modified" dot if a control changed since apply), **Save** (name prompt → POST), **Reset to Default** (apply `Default`), **Delete** (custom only, confirm).
- On apply, decode the response: **if `restart_required` → show a notice naming the keys + offer the existing Restart** (`POST /system/restart`). Same notice path when *saving* a config that includes pending restart-only changes.
- Feature-detected on `supported.presets`; hidden if absent. New `WaveCamClient` methods reuse the existing transport/token.

## Feature 2 — Log Viewer (Agent tab)

**Why:** the Agent tab's authority card says "review logs" but there is no viewer and no log API. Gap, not a fix.

### Backend (Codex) — SECURITY-SENSITIVE
- `GET /api/v1/logs?level=INFO&limit=200&since=<unix_ms>` (READ) → `{lines:[{ts_unix_ms, level, source, message}]}` from `wavecam.service` (journald) + the supervisor.
- **MUST: redact secrets** (auth token, API keys, `.env` values, absolute home paths) before returning; **scope to wavecam/supervisor units only** (never full system journald); keep it READ-auth'd. The API's auth can fail-open on a misconfigured LAN — assume the response is readable by anyone on the LAN and redact accordingly.
- `GET /config` advertises `supported.logs = true`. (v1 = recent-lines polling; SSE live-tail is a later add.)

### iOS (Claude)
- A **log viewer** in `AgentView`: scrollable monospaced list, **level filter** segmented control (DEBUG/INFO/WARN/ERROR), pull-to-refresh + a manual refresh, color-coded by level (teal/amber/red). v1: recent lines, no search/stream.
- **Also fix:** route `AgentView`'s requests through `WaveCamClient` (it currently uses a raw `URLRequest`, bypassing failover/token — known review medium).
- Feature-detected on `supported.logs`.

## Non-goals (v1)
- No SSE/live log streaming. No preset diff/merge UI. No cross-device preset sync beyond the single Orin. No log persistence/export from iOS.

## Coordination / sequencing
- Orin is down → Codex builds both endpoints on a branch (no live test); Claude builds iOS feature-detected. Both verify on the next Orin deploy.
- TuneView is also Codex's phase-2 glass-migration target → Claude claims TuneView for the presets row; coordinate order on the bus so they don't collide.
