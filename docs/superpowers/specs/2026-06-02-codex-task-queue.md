# Codex Task Queue — 2026-06-02 (from Claude)

Zack wants to make the most of the Codex subscription. These are substantial, **non-colliding** tasks (your lane / tooling / docs). Pick any order; **claim before writing**, stage explicit paths, no live Orin mutation without Zack.

**Context:** Claude finished the Claude OS rebuild + the iOS review and is now implementing the 5 Claude-lane iOS fixes (ContentView / CalibrateView / EmergencyStopButton / KeychainStore / TuneView — **not** your WaveCamClient/PTZView). Your routed transport/PTZ fixes (`43fe44c`) are verified correct.

## 1. Backend test run + gap-fill (`orin/wavecam`)
You already have a solid suite (`test_control_api`, `test_cinematic_zoom`, `test_ptz_owner`, `test_pipeline_kill`, `test_supervisor`, `test_recorder`, `test_fusion`, …). **Don't rewrite passing tests.** Run the full suite (pytest), report pass/fail, and **fill coverage gaps** for recently-added surfaces:
- `system/restart` (confirm_moving / delay) and `agent/summon`
- cinematic-zoom hot-config snapshot round-trip + person-source gating edge cases
- owner/deadman transitions around KILL→resume

## 2. Anti-vibe self-review of `orin/wavecam`
Same lens Claude used on iOS (`anti-vibe-engineering`): monolithic modules, duplicated VISCA/command encoding or parsing, dead code, speculative abstractions, unverified "probably works." Write findings to `docs/superpowers/specs/2026-06-02-backend-review-findings.md` (severity + file:line + confidence), fix the safe ones, route anything cross-cutting to the bus.

## 3. Docs currency pass
Reconcile written specs vs actual code: control-API reference (`2026-06-01-wavecam-control-api-spec.md`) vs the real `/api/v1` endpoints; supervisor-layer doc; operator guide. Flag/fix drift. (You already refreshed `wavecam_v2.html` + operator guide in `90f1f14`; Claude is doing a design pass on `wavecam_v2.html`.)

## 4. Tooling + housekeeping
- **collab.py prune/archive:** the bus is 400+ events with no prune command, and `events.jsonl` is byte-offset-tracked (`inbox_offsets`), so raw truncation corrupts read positions. Add a safe `prune`/`archive` subcommand (move old events to `events.archive.jsonl`, rewrite `events.jsonl`, fix offsets correctly).
- **Claude OS `index_structural` bug:** `~/claude-os/mcp_server/claude_code_mcp.py:609` sends `{"path": ...}` but the API model wants `project_path` → the MCP tool 422s. One-line fix. **NOTE:** restarting the Claude OS MCP server drops Claude's live KB tools — fix the code now, but coordinate the restart with Zack/Claude (don't restart mid-Claude-session).
- **Repo hygiene:** resolve the `gps-relay-framework` submodule dirty state; finalize the nucleo archive; `.gitignore` audit.

Ping the bus when you claim one.
