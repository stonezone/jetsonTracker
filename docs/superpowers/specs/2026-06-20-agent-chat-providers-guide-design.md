# WaveCam — Agent Chat UX + Multi-Provider Harness + Guide/App Ordering (design spec)

**Date:** 2026-06-20
**Status:** approved design, pre-implementation
**Origin:** field test of build 525 (a1 chat unusable), operator request for multi-model harness (a2),
guide-vs-app ordering audit (b), and two deferred review findings (VIS-1, GPS-1).

## Context (grounded in the live system)

- **a1 — "chat fails":** the **backend is healthy** — a live `POST /agent/chat` on the rig returned a
  reply in 5.2s (HTTP 200). The failure is iOS-side + perceptual: (1) the keyboard can't be dismissed
  and covers the chat log; (2) chat state is View `@State` with `onDisappear { chatTask?.cancel() }`, so
  switching tabs tears down the conversation and kills the in-flight request; (3) the "response in
  progress" message is the *separate* Summon button's advisor single-flight (`advisor.py:338`), not the
  chat — two unrelated agent UIs crammed in one tab.
- **a2 — multi-provider:** the operator's shell aliases (`deepclaude`/`glmcode`/`kimiclaude`) run the
  `claude` CLI with `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` pointed at each vendor's
  Anthropic-compatible endpoint, scoped to that invocation. The rig agent **already** shells `claude -p`
  (`agent_session.chat`), so this is a clean env-injection extension. DeepSeek/GLM/Kimi use **static API
  keys** (not OAuth — they don't expire like the subscription token); only `claude_code` refreshes itself.
- **b — guide vs app order:** the guide groups knobs by concept (one 15-knob "Tracking Trigger" block);
  the iOS Tune tab splits the same knobs across 5 cards (TARGET, DETECTION, DETECTION ADVANCED, COLOR,
  TRACKING) with `every_n`/lock/unlock/color-area dumped at the bottom after GPS. Reading the guide
  top-to-bottom does not match scrolling the app. The guide is browser-served from the rig (`/guide`),
  opened per-tab from the app via an anchor — single HTML source, no in-app guide view.

## Decisions (locked)
- **Chat surface:** dedicated full-screen chat (Agent tab becomes a real chat screen; KILL always visible).
- **Providers:** `claude_code` (OAuth, default) + `deepseek` + `glm` + `kimi` (vendor keys).
- **Key provisioning:** build plumbing now; providers dormant until the operator adds keys to the rig's
  `agent_keys.json` (clear "no key" error until then). No secrets touch the build session.
- **Remove** the legacy `claude` Messages-API provider + the iOS "Claude (API)" picker case.
- **VIS-1:** leave as-is (GPS-cued orange acquiring is correct for solo foiling; revisit at wing/tow modes).
- **GPS-1:** coast briefly — new `gps.coast_on_no_fix_sec` (default 2.0s).

## Non-goals
- No in-app native guide renderer (browser stays the guide surface).
- No streaming token-by-token chat (request/response per turn; a turn already returns in ~5–30s).
- No change to the armed-tool-use safety model (KILL-human-only, arm gate, audit all hold).

---

## Phase 1 — Agent chat UX (iOS only; ship first)

**Goal:** a usable conversation in the field — readable replies, dismissible keyboard, survives tab switches.

**Components**
- **`WaveCamClient` gains the chat model** (moves out of the View so it persists): `@Published`/observed
  `agentChatLog: [WCAgentChatLine]`, `agentChatSending: Bool`, `agentChatProvider`. `sendAgentChat`
  appends to this log and runs the request as a client-owned `Task` (NOT cancelled on view disappear).
- **`AgentChatView.swift` (new):** full-screen chat — scrolling message list (`ScrollViewReader`
  auto-scroll), input bar pinned above the keyboard, `@FocusState` + `.scrollDismissesKeyboard(.interactively)`
  + tap-background-to-dismiss + return-to-send, provider picker in the nav bar, **KILL button always
  visible** (fixed, not scrolled). Arm toggle here too.
- **`AgentView` (the tab)** keeps health/services/logs + a prominent "Open chat" entry; Summon becomes a
  quick-action that injects a diagnostic prompt into the *same* chat thread (one mental model).

**Error handling:** a turn that fails appends a visible ⚠️ line and clears `sending` (it does today, but
the message is now actually visible). KILL mid-turn: existing `!killed` guard on the armed flag holds.

**Tests/verify:** build + install; send a message → reply renders with keyboard dismissed; switch tabs and
back → conversation intact; KILL reachable; portrait + landscape on-device.

## Phase 2 — Multi-provider harness (backend + iOS)

**Goal:** summon DeepSeek / GLM / Kimi / Claude through the same `claude -p` harness, model chosen per turn.

**Backend (`agent_session.py`, `advisor.py`/keys):**
- `PROVIDER_ENDPOINTS` map: `{deepseek: (base_url, key_field, model), glm: (...), kimi: (...)}` mirroring the
  shell aliases (deepseek → `api.deepseek.com/anthropic`, glm → `api.z.ai/api/anthropic`, kimi →
  `api.moonshot.ai/anthropic`).
- `AgentSession.chat(message, status_text, armed, provider="claude_code")`: for `claude_code`, inject
  `CLAUDE_CODE_OAUTH_TOKEN` (today's path). For a vendor provider, inject `ANTHROPIC_BASE_URL` +
  `ANTHROPIC_AUTH_TOKEN` (from `agent_keys.json`'s `<provider>_api_key`) + `ANTHROPIC_MODEL` into the child
  env instead — same CLI, same stdin prompt, same arm-gated tools.
- Missing key → a clear `provider_unconfigured` error (no crash); the provider is "dormant".
- **`--resume` session_id is per-provider** (a Claude session can't resume under DeepSeek) — key the
  stored session_id by provider, or reset on provider switch.
- Remove `_consult_claude` (legacy Messages-API) + its refresh path; `summon`/`report` keep working via
  `claude_code`.

**Endpoint/iOS:** `/agent/chat` accepts an optional `provider`; `/config` `supported.agent_providers`
lists the configured set. iOS provider picker = those providers; **delete the `.claude` (API) case** from
`AgentProvider` (keep `claudeCode` default).

**Tests:** per-provider env injection (offline, stub the runner — assert base_url/token/model in env for
each); missing-key → `provider_unconfigured`; session_id keyed by provider; registry/supported list.

## Phase 3 — Tune reorder + guide rewrite (iOS + docs)

**Goal:** the app's Tune controls and the guide read in the same logical, top-to-bottom pipeline order.

**Canonical order (detect → fuse/lock → move → frame → autonomy → GPS → display → service):**
1. **TARGET** — color.preset, detector.person_class, fusion.person_aim_y
2. **DETECTION** — detector.conf, detector.every_n, detector.box_ttl_sec, fusion.require_person
3. **LOCK** — fusion.lock_threshold, fusion.unlock_threshold, fusion.match_dist
4. **COLOR** — color.min_area, color.max_area, color.morph_kernel
5. **MOTION** — ptz.max_pan_speed, ptz.max_tilt_speed, ptz.deadzone, ptz.ff_gain, (ff_deadzone_mult,
   min_speed, command_min_interval, invert_pan/tilt where exposed)
6. **CINEMATIC ZOOM** — ptz.cinematic_zoom_enabled, ptz.zoom_target_frac (+ zoom_deadband/max if exposed)
7. **TRACKING** — tracking.enabled, tracking.mode
8. **GPS TRACKING** — fusion.gps_boost, gps.stale_threshold_sec, gps.grace_sec, gps.drive_zoom
9. **DISPLAY/DEBUG** — web.show_mask, web.show_hud, web.jpeg_quality
10. **SERVICE / PRESETS** — last.

**iOS (`TuneView.swift`):** reorder the cards/controls to the above; fold "DETECTION ADVANCED" into
DETECTION+LOCK, surface COLOR up under LOCK (not after GPS). Feature-detect unchanged. Keep both
orientations.

**Guide (`docs/WaveCam_Guide.html`):** rewrite the "Every knob" section to mirror the order 1:1 with the
app's cards (same card names, same sequence) so scrolling the guide == scrolling the app. **Also audit the
rest of the guide for staleness:** yolo11n (not yolov8n), the removed level step, the DISABLE-PTZ
"Autonomous tracking" toggle, the ASK CLAUDE panel, any retired control. Fix what's wrong.

**Verify:** on-device read-through — open the guide for the Tune tab, scroll both, confirm 1:1; spot-check
3 knobs land where the guide says.

## Phase 4 — VIS-1 / GPS-1

- **VIS-1:** no code change. Add a one-line note in the fusion module + guide that GPS-cued color
  acquisition is intentional for solo operation (documents the deferred decision).
- **GPS-1:** add `GpsCfg.coast_on_no_fix_sec: float = 2.0`. In `gps_direct_lora._handle_remote_line`, an
  honest no-fix retains the last fix for up to `coast_on_no_fix_sec` (then clears), instead of clearing
  instantly — the existing `drive_stale_sec` age-gate still drops a truly stale fix. Preserves the
  corrupt-vs-honest distinction (corrupt still retains via its own branch). **Update the two contract
  tests** to assert the new coast behavior (honest no-fix retained < coast window, cleared after).
  Config wired into `_KNOWN_SECTIONS`; hot key.

## Lane / process
- Phase 1 + the iOS halves of 2/3 = Claude's lane (build to device).
- Phases 2/3-guide/4 touch backend = Codex's lane → claim the agent-collab bus before editing; deploy via
  `deploy.sh`; commit to `claude/ios-agent-oauth-knf7ui`; production deploy needs an in-turn go.
- Supervise-only + KILL-human-only invariants hold throughout.

## Verification (per phase)
- P1: on-device chat usable (keyboard, persistence, KILL).
- P2: offline provider tests green; live summon per provider once keys provisioned (deferred).
- P3: guide↔app 1:1 on-device; backend tests green.
- P4: GPS coast tests green; deploy; never "live" until fps>0 while LOCKED.
