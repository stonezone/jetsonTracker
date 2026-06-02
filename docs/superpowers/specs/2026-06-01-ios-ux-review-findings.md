# WaveCam iOS UX review — findings + fix plan (2026-06-01)

Full review by Claude (anti-vibe / frontend-design / code-review lenses). Read-only;
no changes made in the review. Zack: fix them all; Claude + Codex split the work.
Proposed owners below — Codex, confirm or swap on the bus.

## 🔴 HIGH

**1. Joystick stops the camera mid-pan when held still.** `PTZView.swift:99` `sendVelocity`
fires only from `DragGesture.onChanged` (movement-only). Holding a fixed deflection sends
no new velocity → backend 800 ms deadman stops the camera while the finger is still down.
Zoom already has `zoomRepeatTask` (PTZView.swift:150) for this exact reason; pan/tilt lacks it.
Fix: repeat the last non-zero pan/tilt velocity every <800 ms while deflected.  **Owner: Codex**

**2. KILL/control POSTs skip the GET URL-failover and fail silently.** `post()`
(WaveCamClient.swift:287) always uses `self.baseURL`, only updated as a side effect of a
successful `getWithFallback` (refresh GET, :250). `kill()`/`resume()` (:181-191) use `try?` →
errors swallowed (no `lastError`, no UI). Wrong network/cold launch → KILL hits the wrong host,
5 s hang, silent failure. Fix: route safety+PTZ POSTs through the candidate fallback; surface
KILL failures loudly (set lastError + a visible alert).  **Owner: Claude** (client transport)

**3. Live HUD shows realistic FAKE telemetry when offline/pre-refresh.** LiveView nil-defaults
are plausible values: `confidence ?? 0.91`, `fps ?? 26.0`, `state ?? "TRACKING"`,
`isRecording ?? true`, `isLocked ?? true`, `distance ?? 148m` (LiveView.swift:52-53, 591-603).
No-data looks like live tracking. Fix: defaults → `—`/`OFFLINE`; gate HUD on `client.connected`.
**Owner: Codex**

**4. No live telemetry — HUD is a one-shot snapshot.** `refresh()` runs only on `.task` +
manual buttons; no periodic poll, `/api/v1/telemetry` WS unused. Video streams live but all
HUD numbers freeze at first load. Fix: client self-drives a ~1 Hz refresh while live (or
consume the telemetry WS) — implement inside WaveCamClient so the views don't change.
**Owner: Claude** (client)

## 🟠 MEDIUM

**5. Dashboard tab hardcoded to Wi-Fi** `http://192.168.1.155:8080` (DashView.swift:6) →
unreachable on the tether. Fix: derive dashboard host from the client's working baseURL
(swap `:8088/api/v1` → `:8080`).  **Owner: Codex**

**6. "Summon Codex" is cosmetic** — `summonDiagnostics()` (AgentView.swift:42) only sets a
local label; no endpoint call. Fix: wire to the gated `/api/v1/agent/summon` endpoint (the one
you're building) + show real status/result.  **Owner: Codex**

**7. Auth token in `UserDefaults` (plaintext)** via `@AppStorage`. Fix: store the bearer token
in Keychain.  **Owner: Claude** (auth-adjacent)

**8. Client configured twice** — `ConnectionView.applySettings` calls `configure()` and writes
`@AppStorage`, which fires `WaveCamApp.onChange → applyStoredSettings → configure()` again
(WaveCamApp.swift:21-24). Harmless but redundant. Fix: single source of truth.  **Owner: Claude**

## 🟡 LOW (anti-vibe / polish)

**9. Dead code:** `ScreenStub` (ContentView.swift:116) unused (grep-confirmed) — delete.  **Owner: Codex**

**10. Duplication:** 3 emergency-stop implementations (`EmergencyStopButton` LiveView,
`PTZEmergencyStopButton` PTZView, TopBar KILL chip). Consolidate to one shared component.  **Owner: Codex**

**11. Fabricated fallback:** AgentView `fallbackState` hardcodes `cloudflared = "degraded"`
for a missing service (AgentView.swift:38) → should be `unknown`.  **Owner: Codex**

## Notes
- Decoder/contract OK (`WCStatus` matches live `/api/v1/status`). Spot-check: `media.status()`
  must always include `recording` (non-optional in `Media`) or the whole decode fails.
- Split rationale: Claude owns WaveCamClient transport/reliability + token security (originally
  authored); Codex owns the view-layer fixes (his active lane) → parallel, minimal collision.
