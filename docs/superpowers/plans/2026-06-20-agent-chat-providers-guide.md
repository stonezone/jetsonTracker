# Agent Chat UX + Multi-Provider + Guide Ordering — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** Make the agent chat usable in the field (full-screen, dismissible keyboard, survives tab-switch), summon DeepSeek/GLM/Kimi through the same `claude -p` harness, reorder Tune + guide into one pipeline order, and ship VIS-1 (doc-only) + GPS-1 (coast-on-no-fix).

**Architecture:** iOS chat state moves into `WaveCamClient` (survives view teardown); a new `AgentChatView` is the full-screen surface. The rig agent (`agent_session.chat`) gains a `provider` arg that injects per-provider `ANTHROPIC_*` env into the `claude` subprocess (mirrors the operator's shell aliases). TuneView card order + the guide's knob section are rewritten to one canonical pipeline order. GPS gains `coast_on_no_fix_sec`.

**Tech Stack:** Swift/SwiftUI (iOS), Python/FastAPI + pytest (backend), HTML (guide).

## Global Constraints
- Backend = Codex's lane → claim the agent-collab bus before editing `orin/wavecam/`; deploy only via `orin/wavecam/deploy.sh`; commit to `claude/ios-agent-oauth-knf7ui` (NOT main); production deploy needs an in-turn go.
- Supervise-only + KILL-human-only hold throughout. Vendor keys never enter the build session.
- iOS: portrait + landscape parity; verify on the BUILT app; decoders use `.convertFromSnakeCase`, no snake CodingKeys; tolerant `init(from:)` in extensions.
- All backend tests offline (inject the subprocess runner); mypy gate green.

---

## Phase 1 — Agent chat UX (iOS only)

### Task 1.1: Move chat state into WaveCamClient
**Files:** Modify `ios/WaveCam/Sources/WaveCamClient.swift`
**Produces:** on the `@Observable WaveCamClient`: `var agentChatLog: [WCAgentChatLine]`, `var agentChatSending: Bool`, `var agentChatProvider: AgentProvider`; `func sendAgentChatTurn(_ text: String)` (appends user line, runs a client-owned `Task` that is NOT tied to any view lifecycle, appends the reply or a ⚠️ line, clears sending). `WCAgentChatLine { id, role(.you/.claude), text }` moved here from AgentView.

- [ ] Move `AgentChatLine` → `WCAgentChatLine` (Identifiable) into WaveCamClient.swift.
- [ ] Add the three stored props + `sendAgentChatTurn` (reuse existing `sendAgentChat`; guard on `agentChatSending`; `!client.killed` guard on `agentArmed` update).
- [ ] Build: `cd ios/WaveCam && ./build-device.sh build` → `** BUILD SUCCEEDED **`.
- [ ] Commit: `git add ios/WaveCam/Sources/WaveCamClient.swift && git commit -m "feat(ios): move agent chat state into WaveCamClient (survives tab switch)"`

### Task 1.2: AgentChatView full-screen surface
**Files:** Create `ios/WaveCam/Sources/AgentChatView.swift`; Modify `AgentView.swift` (entry point + drop the inline card)
**Consumes:** the client chat state from 1.1.

- [ ] Create `AgentChatView`: scrolling `ScrollViewReader` message list bound to `client.agentChatLog`; input bar pinned bottom; `@FocusState private var inputFocused`; `.scrollDismissesKeyboard(.interactively)`; tap-background `inputFocused = false`; `.onSubmit` sends; provider `Picker` in a top bar; an always-visible KILL button (calls `client.kill()`); the arm toggle (forced off + disabled when `client.killed`).
- [ ] In `AgentView`: replace the inline `AgentChatCard` with a prominent "Open chat" button → `.fullScreenCover`/navigation to `AgentChatView`. Summon quick-action injects its diagnostic prompt via `client.sendAgentChatTurn(...)` into the same log. Delete the now-dead `AgentChatCard`, `AgentChatLine`, `sendChat`, `setArm`, `chatTask`, `onDisappear{chatTask?.cancel()}` from AgentView.
- [ ] `cd ios/WaveCam && xcodegen generate` (new Source file) then `./build-device.sh build` → SUCCEEDED.
- [ ] Commit: `git add -A ios/WaveCam/Sources/AgentChatView.swift ios/WaveCam/Sources/AgentView.swift ios/WaveCam/project.yml && git commit -m "feat(ios): dedicated full-screen agent chat (keyboard dismiss, KILL pinned)"`
      (stage the .pbxproj too if xcodegen regenerated it and it's tracked)

### Task 1.3: Install + device-verify Phase 1
- [ ] `cd ios/WaveCam && ./build-device.sh` → Installed build N.
- [ ] On device: open chat, send a message → reply renders; swipe keyboard down → log visible; switch tabs and back → conversation intact, in-flight reply still lands; KILL reachable; rotate portrait↔landscape.
- [ ] No commit (verification only); note the build number.

---

## Phase 2 — Multi-provider harness (backend + iOS)

### Task 2.1: Provider endpoint map + per-provider env injection
**Files:** Modify `orin/wavecam/wavecam/agent_session.py`; Test `orin/wavecam/tests/test_agent_session.py`
**Produces:** module `PROVIDER_ENDPOINTS = {"deepseek": ("https://api.deepseek.com/anthropic", "deepseek_api_key", "deepseek-v4-flash"), "glm": ("https://api.z.ai/api/anthropic", "glm_api_key", "glm-4.7"), "kimi": ("https://api.moonshot.ai/anthropic", "moonshot_api_key", "kimi-k2.7-code")}`; `AgentSession.chat(message, status_text, armed=False, provider="claude_code")`; per-provider `session_id` via `self._session_ids: dict[str,str]`.

- [ ] **Failing test** `test_chat_vendor_provider_injects_env`:
```python
def test_chat_vendor_provider_injects_env(tmp_path):
    keys = tmp_path / "k.json"
    keys.write_text(json.dumps({"deepseek_api_key": "DS_KEY"}))
    cap = {}
    def fake_run(argv, env, stdin_text, timeout):
        cap["env"] = env
        return json.dumps({"result": "ok", "session_id": "S"})
    sess = AgentSession(keys_path=str(keys), run=fake_run)
    sess.chat("hi", status_text="", provider="deepseek")
    assert cap["env"]["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert cap["env"]["ANTHROPIC_AUTH_TOKEN"] == "DS_KEY"
    assert cap["env"].get("ANTHROPIC_MODEL", "").startswith("deepseek")
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in cap["env"]   # vendor path doesn't use the OAuth token
```
- [ ] **Failing test** `test_chat_unconfigured_provider_errors`:
```python
def test_chat_unconfigured_provider_errors(tmp_path):
    keys = tmp_path / "k.json"; keys.write_text("{}")
    sess = AgentSession(keys_path=str(keys), run=lambda *a: "{}")
    with pytest.raises(RuntimeError, match="provider_unconfigured|api_key"):
        sess.chat("hi", status_text="", provider="glm")
```
- [ ] **Failing test** `test_session_id_keyed_per_provider`: two providers don't share `--resume`.
- [ ] Run → FAIL.
- [ ] Implement: branch in `chat()` — `claude_code` keeps today's `CLAUDE_CODE_OAUTH_TOKEN` env; a provider in `PROVIDER_ENDPOINTS` injects `ANTHROPIC_BASE_URL/ANTHROPIC_AUTH_TOKEN/ANTHROPIC_MODEL` from the `<key_field>` (raise `RuntimeError("provider_unconfigured: <p>")` if missing); store/lookup `session_id` in `self._session_ids[provider]`.
- [ ] Run → PASS; `pytest -q`; `mypy`.
- [ ] Commit: `git add wavecam/agent_session.py tests/test_agent_session.py && git commit -m "feat(agent): vendor providers (deepseek/glm/kimi) via per-provider env injection"`

### Task 2.2: Endpoint + supported list + drop legacy claude provider
**Files:** Modify `agent_session.py` (remove `_consult_claude` legacy if separate), `advisor.py` (PROVIDERS), `control_system.py` (pass provider through `request_agent_chat`), `control_api.py` (AgentChatRequest gains `provider`; `supported.agent_providers`), `control_snapshots.py`; Test `tests/test_control_api.py`.
**Produces:** `request_agent_chat(message, provider)`; `/config` `supported.agent_providers: ["claude_code","deepseek","glm","kimi"]` (only those with keys present, claude_code always); `AgentChatRequest{message, provider}`.

- [ ] **Failing test** `test_supported_lists_agent_providers` + `test_agent_chat_passes_provider` (stub chat, assert provider threads through).
- [ ] Run → FAIL.
- [ ] Implement: thread `provider` through; build `agent_providers` from configured keys; remove the legacy `claude` Messages-API provider + `_consult_claude`/`_claude_refresh` (keep `claude_code`, `codex` if present, vendors). Regenerate the API route snapshot if routes changed (`python3 tools/regen_api_snapshot.py`).
- [ ] Run → PASS; `pytest -q`; `mypy`.
- [ ] Commit: `git add -A orin/wavecam && git commit -m "feat(agent): provider in /agent/chat + supported.agent_providers; drop legacy Claude API path"`

### Task 2.3: iOS provider picker
**Files:** Modify `AgentView.swift`/`AgentChatView.swift` (`AgentProvider` enum), `WaveCamClient.swift` (send provider; `supported.agentProviders`).
- [ ] `AgentProvider`: `claudeCode`(default), `deepseek`, `glm`, `kimi`. **Delete `.claude` (API) case.** Labels: Claude / DeepSeek / GLM / Kimi.
- [ ] `sendAgentChat` posts `provider`; picker feature-detects `supported.agentProviders`.
- [ ] `xcodegen generate` if needed; `./build-device.sh build` → SUCCEEDED.
- [ ] Commit: `git add -A ios/WaveCam/Sources && git commit -m "feat(ios): provider picker (claude_code/deepseek/glm/kimi); drop Claude API case"`

### Task 2.4: Deploy + verify (gated)
- [ ] `cd orin/wavecam && pytest -q && mypy` green.
- [ ] **Explicit operator go** → `./deploy.sh` → `DEPLOY OK`.
- [ ] Verify: `/config` shows `supported.agent_providers` incl. claude_code; a `claude_code` chat still round-trips; a vendor provider without a key returns `provider_unconfigured` (not a crash). fps>0 LOCKED.
- [ ] (Vendor keys provisioned by operator later → then live per-provider summon.)

---

## Phase 3 — Tune reorder + guide rewrite

### Task 3.1: Reorder TuneView to canonical pipeline order
**Files:** Modify `ios/WaveCam/Sources/TuneView.swift`
**Canonical card order:** TARGET → DETECTION → LOCK → COLOR → MOTION → CINEMATIC ZOOM → TRACKING → GPS TRACKING → DISPLAY → SERVICE/PRESETS. (Keys per the spec's numbered list.)
- [ ] Reorder the card/control blocks to match; fold "DETECTION ADVANCED" into DETECTION+LOCK; move COLOR up under LOCK (out from after GPS). Pull `detector.every_n` into DETECTION; `fusion.lock/unlock/match_dist` into LOCK; `web.show_*`/`jpeg_quality` into DISPLAY. Feature-detect unchanged.
- [ ] `./build-device.sh build` → SUCCEEDED; on-device sanity (portrait+landscape).
- [ ] Commit: `git add ios/WaveCam/Sources/TuneView.swift && git commit -m "refactor(ios): Tune cards in pipeline order (target→detection→lock→color→motion→…)"`

### Task 3.2: Rewrite guide knob section 1:1 + staleness audit
**Files:** Modify `docs/WaveCam_Guide.html`
- [ ] Rewrite the "Every knob" section so its groups/sequence mirror the app cards from 3.1 exactly (same names, same order). Each knob keeps its what/adjust/see copy.
- [ ] Staleness audit across the guide: yolov8n→yolo11n; remove/curtail the level step (removed 2026-06-17); add the DISABLE-PTZ "Autonomous tracking" toggle; mention the ASK CLAUDE panel; fix any retired control. Verify `ctrl-key` values still match real config keys.
- [ ] Commit: `git add docs/WaveCam_Guide.html && git commit -m "docs(guide): knob section 1:1 with app order + staleness fixes (yolo11n, disable-ptz, no-level)"`

### Task 3.3: Verify guide↔app
- [ ] `cd orin/wavecam && ./deploy.sh` (guide is served from the rig) — **gated on operator go** (or defer; guide is static).
- [ ] On device: open the guide for Tune, scroll guide + app side by side → 1:1; spot-check 3 knobs (incl. `detector.every_n`) land where the guide says.

---

## Phase 4 — VIS-1 (doc) + GPS-1 (coast-on-no-fix)

### Task 4.1: VIS-1 doc-only note
**Files:** Modify `orin/wavecam/wavecam/fusion.py` (comment), `docs/WaveCam_Guide.html` (the orange-house section).
- [ ] Add a one-line comment at the GPS-boost site: GPS-cued color acquisition is intentional for solo operation (deferred review decision; revisit at wing/tow session modes). Mirror one sentence in the guide.
- [ ] Commit: `git add -A orin/wavecam/wavecam/fusion.py docs/WaveCam_Guide.html && git commit -m "docs(fusion): note GPS-cued color acquisition is intentional (VIS-1 deferred)"`

### Task 4.2: GPS-1 coast-on-no-fix
**Files:** Modify `orin/wavecam/wavecam/config.py` (`GpsCfg.coast_on_no_fix_sec: float = 2.0` + `_KNOWN_SECTIONS` already has gps), `gps_direct_lora.py`, `control_utils.py` (hot key), `control_config.py` (apply); Test `tests/test_gps_direct_lora.py`.
- [ ] **Update the contract tests:** `test_remote_no_fix_clears_subject_snapshot` → assert the fix is RETAINED immediately after an honest no-fix, and CLEARED only after `coast_on_no_fix_sec` elapses (drive the clock). Keep `test_corrupt_fix_line_retains_last_good`.
- [ ] Run → FAIL.
- [ ] Implement: `_handle_remote_line` honest-no-fix path retains `_latest` and records the no-fix timestamp; `get_fix` (or the handler) clears `_latest` only once `now - _last_fix_ok_ts > coast_on_no_fix_sec`. Add `coast_on_no_fix_sec` to GpsCfg + the hot-key list + apply path.
- [ ] Run → PASS; `pytest -q`; `mypy`.
- [ ] Commit: `git add -A orin/wavecam && git commit -m "feat(gps): coast on honest no-fix for gps.coast_on_no_fix_sec (default 2s) — GPS-1"`

### Task 4.3: Deploy + verify (gated)
- [ ] `pytest -q && mypy` green → **operator go** → `./deploy.sh` → `DEPLOY OK`; fps>0 LOCKED; `/config` shows `gps.coast_on_no_fix_sec`.

---

## Self-Review
- **Spec coverage:** P1 chat UX (1.1–1.3) ✓; P2 providers + remove-legacy + iOS picker + deploy (2.1–2.4) ✓; P3 reorder + guide + verify (3.1–3.3) ✓; P4 VIS-1 doc + GPS-1 coast (4.1–4.3) ✓.
- **Placeholders:** backend tasks carry concrete test code; iOS UI tasks are outcome+device-verified (exploratory SwiftUI isn't unit-tested here, consistent with the repo).
- **Type consistency:** `WCAgentChatLine`, `sendAgentChatTurn`, `AgentSession.chat(...,provider=)`, `PROVIDER_ENDPOINTS`, `supported.agent_providers`/`agentProviders`, `coast_on_no_fix_sec` used consistently across tasks.
