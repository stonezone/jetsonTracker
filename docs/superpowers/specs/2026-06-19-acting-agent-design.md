# WaveCam Acting Agent — "Claude with hands" (design spec)

**Date:** 2026-06-19
**Status:** approved design, pre-implementation
**Supersedes:** the Phase-B/C "tool-using agent" portions of `docs/wavecam-agent-oauth-plan.html`
and `~/.claude/plans/encapsulated-sparking-pearl.md`. Those assumed a hand-rolled Python
tool-dispatcher over the Messages API. This spec replaces that with **Claude Code itself**
(`claude -p`) as the agent loop, because the operator chose a capability tier (code edit + deploy)
that only a real coding agent with Bash/Edit/git can serve. Phase A (OAuth auto-refresh for the
legacy `claude` Messages provider) already shipped (`498bc8e`) and is unaffected.

## 1 · Goal

Turn the read-only "Summon Claude" advisor into an **interactive agent that can fix things** on the
live rig — tune config, run calibration, restart the service, and edit/deploy code — driven
conversationally from the operator's surfaces, **without breaking the supervise-only / KILL-reachable
safety invariant.**

### Operator decisions (locked)
- **Capability tier:** full teammate — read · tune · calibrate (camera motion) · restart · deploy code.
- **Trigger:** **armed-session toggle** — operator flips "Claude can act" ON; Claude then proceeds
  autonomously until the operator disarms or hits KILL. (Not per-action approval.)
- **Surfaces:** iOS Agent chat (field) **+** a `:8088` web terminal/chat (desk, heavier code work).

### Non-goals
- Not a per-action approval UX (operator chose armed-session).
- Not replacing the deterministic health snapshot — that stays.
- Not autonomous-while-unattended: the arm toggle is the supervise gate and auto-expires.

## 2 · Safety model (the crux — non-negotiable floors)

Even in armed mode these hold, by construction:

1. **KILL is human-only and supreme.** KILL is the existing `SAFETY`-role control, always reachable
   in the iOS app. It is **not** an MCP tool and Claude has no capability to call, disable, or
   suppress it. KILL → disarm + SIGTERM the `claude` subprocess + the existing KILL path stops the
   camera.
2. **Arm defaults OFF and auto-expires.** `DISARMED` on every boot/session; an armed session times
   out after `agent.arm_ttl_sec` of inactivity. Only the operator can arm.
3. **Camera moves go through the arbiter ownership path** (owner=`agent`/`calibrate`), so armed Claude
   cannot fight a manual aim or the DISABLE-PTZ latch and yields the instant the operator takes the
   stick or KILLs.
4. **Everything is audited.** Every MCP tool call and every Bash/Edit/deploy emits an EventRing event
   and a visible chat line.
5. **Deploy uses `deploy.sh`** (test-gate + version stamp + git commit) — never a raw rsync/file
   clobber.

### Arm state machine

| State | Set by | Claude may use | Camera | Code/deploy |
|---|---|---|---|---|
| **DISARMED** (default) | — | read + tune MCP tools only | ❌ | ❌ |
| **ARMED** | operator toggle (TTL auto-expire) | + Bash/Edit/Write/git + camera/calibrate/restart/deploy tools | ✅ (arbiter owner) | ✅ (`deploy.sh`) |
| **KILLED** | human KILL (supreme) | read only; subprocess SIGTERM'd | stopped | ❌ |

### Two-layer gating (defense in depth)
1. **At launch** — the orchestrator sets `--allowedTools` / `--disallowedTools` + `--permission-mode`
   from the arm-state. Disarmed Claude is *not given* Bash/Edit or camera/deploy tools at all.
2. **At execution** — every mutating MCP tool re-checks `armed && not killed` (and camera tools also
   re-check arbiter ownership) before acting. Holds even if layer 1 is misconfigured. This is the
   system-boundary validation.

## 3 · Architecture

```
 iOS Agent chat ┐                          ┌─ native tools: Bash · Edit · Write · git → code/deploy (deploy.sh)
 :8088 web term ┴─→ /agent/chat (operator) │
                         │                  └─ MCP: wavecam server → control ops (read/tune/calibrate/restart)
                         ▼                          │
                  agent_session orchestrator         ▼
                  + SAFETY BRIDGE ───────────→ claude -p --resume <sid>
                         │                       --mcp-config wavecam.json
                    EventRing audit               --allowedTools <set-for-arm-state>
                         ▲                          --permission-mode <per-arm-state>
                         └──────────────────────────── stream-json: text · tool_use · tool_result
```

Claude Code on the rig is the brain **and** hands; we feed it WaveCam tools and gate it. We do **not**
re-implement an agent loop.

### Verified engine capabilities (`claude --help`, rig v2.1.183)
`-p/--print`, `--resume [sessionId]` / `--continue` (multi-turn memory), `--mcp-config`,
`--allowedTools`/`--disallowedTools`, `--permission-mode`, `--output-format stream-json`,
`--input-format stream-json`. These flags **exist**; their exact headless behavior is the Phase-0 GATE.

### Phase-0 GATE (verify before building on it)
1. `claude -p --resume <id> "<msg>"` reliably continues prior context headlessly. (single most
   load-bearing assumption — "interactive like we are here").
2. `--mcp-config` + `--allowedTools mcp__wavecam__*` actually restricts Claude to the named tools.
3. `--permission-mode` in `-p` runs allowed tools without an interactive prompt (armed model needs
   no human-in-the-loop callback).
- **Pass →** proceed. **Fail →** surface to Zack with options (e.g. persistent stream-json subprocess
  with injected turns) before building Phase 1.

## 4 · Components (SRP — one purpose each)

- **`agent_mcp_server.py`** *(new, backend)* — WaveCam ops exposed as MCP tools. Calls `SystemManager`
  in-process, never HTTP-to-self. Each mutating tool self-checks arm/killed/ownership.
- **`agent_session.py`** *(new, backend)* — owns: spawn/resume the `claude` session; the arm state
  machine + TTL; the audit emit. Phase 1 = stateless `--resume` per message; Phase 4 = persistent
  streaming subprocess.
- **`control_api.py`** — `POST /agent/chat` (operator), `POST /agent/arm` (operator),
  `GET /agent/stream` (Phase 4, SSE/WS). KILL already exists, stays `SAFETY`.
- **`config.py`** — `AgentCfg` (`enabled:false` default, `model`, `arm_ttl_sec`, tool-tier allowlists,
  `mcp_config_path`); add to the `Config` dataclass **and** `_KNOWN_SECTIONS` (the silent-vanish
  gotcha). Off ⇒ `supported.agent=false`, core boots unaffected. Optionality applies to the new
  capability only; legacy summon keeps working.
- **iOS `AgentChatView.swift`** *(evolve `AgentView`)* — streaming multi-turn chat, tool/diff cards,
  the **arm toggle**, ever-present KILL. Decoders must NOT declare snake_case `CodingKeys` (global
  `convertFromSnakeCase` null-out gotcha). Poll/stream `Task` cancelled in `onDisappear`.
- **`web.py`** *(Phase 4)* — terminal/chat panel on `:8088`.

### MCP tool inventory (tiered to arm-state)
| Tier | Tools | Allowed when |
|---|---|---|
| read | `get_status`, `get_config`, `get_gps`, `get_calibration`, `read_logs` | always (agent enabled) |
| tune | `set_config` (hot keys), `apply_preset` | always |
| camera | `calibrate_*` (session/location/heading/etc.), `ptz_*` | ARMED ∧ ¬KILLED ∧ arbiter-ownable |
| system | `restart_service`, `deploy` (wraps `deploy.sh`) | ARMED ∧ ¬KILLED |

Native Bash/Edit/Write/git are governed purely by `--allowedTools` per arm-state (disarmed: denied).

## 5 · Data flow (one operator message)
operator types → `/agent/chat` (auth=operator) → orchestrator launches `claude -p --resume <sid>`
with the arm-state tool set → Claude reasons, calls MCP/Bash tools (which hit `SystemManager` /
shell) → backend audits each call + relays stream-json events → surface renders chat + tool/diff
cards.

## 6 · Error handling
- claude exits nonzero / timeout → failed turn; session preserved (resume still works).
- MCP refusal (`owner_busy`, killed, disarmed) → `tool_result` error → Claude adapts / guides operator.
- KILL mid-turn → SIGTERM subprocess; existing KILL path stops camera; state → KILLED.
- Arm TTL expiry mid-session → next mutating tool denied; chat notes "disarmed (timeout)".
- Deploy test-fail → `deploy.sh` aborts non-zero → surfaced; rig untouched.

## 7 · Testing
- MCP tool gating matrix (offline): each mutating tool denied when DISARMED / KILLED; allowed when
  ARMED; camera tool additionally checks arbiter ownership.
- Arm state machine: default OFF, TTL expiry, KILL disarms + forbids re-arm while killed.
- Orchestrator: `--resume` continuity; `--allowedTools` set matches arm-state; SIGTERM on KILL.
- Optionality: agent disabled ⇒ core boots, vision FPS/PTZ/KILL unaffected, `supported.agent=false`.
- iOS: chat model decode (no snake CodingKeys); KILL reachable every state; portrait + landscape.
- Live GATE per phase; never "live" until verified `fps>0` while LOCKED.

## 8 · Phasing (field-safe, water-test gated)
0. **GATE** — verify the three engine assumptions above (§3).
1. **MVP** — `agent_mcp_server` (read+tune tools) + `agent_session` arm bridge + KILL integration +
   stateless-resume **iOS chat**; only the *tune* tier enabled (camera/system tools exist but DENIED).
   Proves the act-and-gate stack on the path already verified (`HARNESS_OK`).
2. **Calibrate tier** — enable camera/calibration under ARMED + arbiter ownership. **Water-test gated.**
3. **Restart + deploy tier** — `restart_service` + `deploy` (`deploy.sh` wrapper).
4. **Streaming + web** — persistent streaming orchestrator (Approach A) + `:8088` terminal/chat.

## 9 · Lane / process
Backend-heavy = **Codex's primary lane.** Claim the agent-collab bus for `orin/wavecam/` before each
phase's backend edits; deploy only via `orin/wavecam/deploy.sh`; commit to a feature branch (NOT
`main`); a production deploy/restart of the live rig needs an explicit in-turn go. Supervise-only +
KILL-reachable invariants always hold.
