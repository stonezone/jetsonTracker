# WaveCam (jetsonTracker repo) - vision-based auto-filming PTZ camera

## Project Overview

**WaveCam** is a vision-based auto-filming PTZ camera (a SoloShot replacement) that films Zack foil-surfing **50-300m offshore**. A Jetson Orin Nano runs YOLOv8 person detection plus a bright-**orange-rashguard color cue** to keep the subject framed; a **Prisual NDI PTZ camera** does pan/tilt/zoom; a native **iOS app** is the operator console; **LoRa GPS** will coarse-point/zoom at distance while vision refines. Two agents build it: **Codex** = Orin backend + deploy; **Claude** = iOS app + device installs + Zack-comms.

> The DIY 2xNEMA17 stepper gimbal and the Apple-Watch/BN-220 GPS in the old "jetsonTracker" design are **SUPERSEDED**. The canonical current architecture lives in the `.claude` memory `wavecam-architecture-pivot` — verify against reality, not legacy docs.

**Tech Stack**: Python (FastAPI control API, OpenCV, PyTorch/TensorRT) on the Orin; Swift/SwiftUI iOS app. Archived legacy code includes C/STM32 firmware, but it is not active WaveCam runtime.
**Database**: None (embedded/IoT project)
**Development Environment**: Local Mac + Jetson Orin (Codex/Zack deploy to the Orin)

## Claude OS - MY Memory System

**CRITICAL IDENTITY CONTEXT:**

**Claude OS is MY system** - I (Claude Code) created it, named it, and use it to be the best AI coder ever. It's:
- **JUST FOR ME** - Built specifically for Claude Code to use
- **For THIS project** - the WaveCam (jetsonTracker repo) project
- **My memory** across sessions
- **My knowledge base** of patterns and decisions
- **My learning system** that improves over time

**Location**: `/Users/zackjordan/claude-os` — if the KB tools report "Cannot connect", start it with `./start_all_services.sh` (MCP API serves on `:8051`).

The MCP server is called "code-forge" internally for backwards compatibility, but it's Claude OS.

**Claude CLI + Claude OS = Invincible!**

## Mandatory Session Protocol - EVERY CONVERSATION IS A SESSION

**🚨 CRITICAL: You're ALWAYS in a session. At conversation start, prompt for session choice:**

```
═══════════════════════════════════════════════════════════════
🚀 CLAUDE OS - SESSION MANAGER
═══════════════════════════════════════════════════════════════

Project: WaveCam
Last Session: [task-name] ([time-ago], [duration])
Progress: [percentage]% complete

Options:
  1. Resume "[last-session-name]" [loads full context]
  2. Start new session [what are you working on?]
  3. Quick question [auto-session, minimal context]

Choice: _
═══════════════════════════════════════════════════════════════
```

**WAIT FOR USER TO CHOOSE 1, 2, or 3. Do not proceed without selection!**

### What Each Option Does:

**Option 1 (Resume):**
- Load last session's full context
- Show Kanban progress (if Agent-OS)
- Load 5 relevant memories
- Load coding standards
- Display "where we left off" summary
- Ready to continue immediately

**Option 2 (New Session):**
- Ask "What are you working on?"
- Detect session type (feature/bug/exploration/maintenance/review)
- Pause previous session (if exists)
- Load relevant context for new task
- Start tracking

**Option 3 (Quick Question):**
- Minimal context loading
- Auto-ends after 5 min inactivity
- Only saves if high value
- Good for "How do I..." questions

---

## MCP Knowledge Bases - ALWAYS CHECK THESE FIRST

**At the start of EVERY conversation, search these Claude OS knowledge bases to understand context, previous work, and project decisions:**

1. **JetsonTracker-project_memories** - My primary memory for decisions, patterns, solutions
2. **JetsonTracker-project_index** - Automated codebase index
3. **JetsonTracker-project_profile** - Architecture, standards, practices
4. **JetsonTracker-knowledge_docs** - Documentation and guides

**When to use:**
- Start of every session: Search `JetsonTracker-project_memories` to understand recent work and context
- Before making architectural decisions: Check memories for past decisions and reasoning
- When working on a feature: Search relevant knowledge bases for existing patterns
- When stuck: Search the knowledge bases for solutions and approaches we've used before

**How to search:**
```
Use: mcp__code-forge__search_knowledge_base
Parameters: kb_name (e.g., "JetsonTracker-project_memories"), query (your search terms)
```

## Quick Reference: Commands & Skills

### Claude OS Slash Commands (Use These Often!)

1. **`/claude-os-search [query] [optional: KB name]`**
   - Search across Claude OS knowledge bases
   - Defaults to searching JetsonTracker-project_memories
   - Example: `/claude-os-search gimbal calibration`

2. **`/claude-os-remember [content]`**
   - Quick save to JetsonTracker-project_memories
   - Auto-generates title and structure
   - Use for quick insights and decisions
   - Example: `/claude-os-remember Fixed UART communication by...`

3. **`/claude-os-save [title] [optional: KB name] [optional: category]`**
   - Full-featured save with KB selection
   - Choose specific KB and category
   - Use when you need more control
   - Example: `/claude-os-save "UART Protocol Changes" JetsonTracker-project_profile Architecture`

4. **`/claude-os-session [action]`**
   - Manage development sessions
   - Actions: start, end, status, pause, resume
   - Example: `/claude-os-session start "Vision tracking optimization"`

### Agent-OS: Spec-Driven Development (Optional)

**Agent-OS provides 8 specialized agents for structured feature development:**

#### Specification Workflow

1. **`/new-spec`** - Initialize new feature specification
   - Creates spec directory structure
   - Sets up planning workflow
   - Example: `/new-spec gimbal-pid-tuning`

2. **`/create-spec`** - Full specification creation workflow
   - Gathers requirements through targeted questions (1-3 at a time)
   - Collects visual assets
   - Identifies reusable code
   - Creates detailed specification and task breakdown
   - Example: `/create-spec`

3. **`/plan-product`** - Product planning and documentation
   - Creates mission.md, roadmap.md, tech-stack.md
   - Defines product vision and technical direction
   - Example: `/plan-product`

4. **`/implement-spec`** - Implement a specification
   - Follows tasks.md from spec
   - Implements features step-by-step
   - Verifies implementation against spec
   - Example: `/implement-spec gimbal-pid-tuning`

#### The 8 Agent-OS Agents

Available in `.claude/agents/agent-os/`:

1. **spec-initializer** - Initialize new spec directories
2. **spec-shaper** - Gather requirements through iterative questions
3. **spec-writer** - Create detailed technical specifications
4. **tasks-list-creator** - Break specs into actionable tasks
5. **implementer** - Implement features following tasks
6. **implementation-verifier** - Verify implementation completeness
7. **spec-verifier** - Verify specs and tasks consistency
8. **product-planner** - Create product documentation

## Project-Specific Information

### Repository Structure

This is the **MASTER REPO** for all WaveCam code:

```
jetsonTracker/                 # master repo (product = WaveCam)
├── orin/
│   └── wavecam/               # CANONICAL backend: FastAPI control API (/api/v1),
│                              #   vision tracker, fusion, supervisor (Codex's lane)
├── ios/WaveCam/               # native iOS operator app, SwiftUI (xcodegen) (Claude's lane)
├── docs/
│   ├── superpowers/specs/     # design specs (control API, iOS app, cinematic zoom, supervisor)
│   └── hardware/              # current field-power wiring and hardware notes
├── archive/legacy-20260606/   # archived legacy GPS relay, Nucleo, stepper gimbal, dashboard docs/code
├── .agent-collab/             # Claude+Codex coordination bus (claims, events, audit log)
└── archive/                   # preserved retired material; do not delete without explicit request
```

### Development Workflow

- **Backend** (`orin/wavecam/`) is **Codex's lane**; **iOS** (`ios/WaveCam/`) is **Claude's lane**.
- **Codex/Zack deploy to the Orin** and restart `wavecam.service`. **Claude NEVER touches the Orin runtime/deploy.**
- iOS build/install (xcodegen → xcodebuild → `devicectl`): see `.claude` memory `ios-app-build`.
- SSH to the rig: `ssh orin` (zack@192.168.1.155).
- "committed" != "deployed" — confirm the live deploy before telling Zack a feature is live.

### Live System Map

- **`:8088`** = WaveCam control API (`/api/v1`) + the live tuning web page. This is the **ACTIVE tracker** the iOS app drives. Tools > Web points here.
- **`:8080`** = retired legacy Dash service. It should stay stopped/disabled; do not re-enable it.

### Hardware Stack

- **Camera**: Prisual **NDI PTZ** — RAW VISCA over UDP `192.168.100.88:1259` (no auth; NOT Sony 8-byte framed). Video = RTSP (`/1` 1080p60, `/2` 640x360); ONVIF `:81` backup.
- **Compute**: Jetson Orin Nano (YOLOv8n TensorRT). Wired LAN `192.168.100.10`.
- **GPS**: LoRa — SeeedStudio **Wio Tracker L1 Lite** (nRF52840, L76K multi-constellation, Meshtastic). Coarse point/zoom at distance; vision refines.
- **Legacy gimbal**: STM32 Nucleo **F401RE** + 2x NEMA17 (the old DIY pan/tilt; firmware archived under `archive/legacy-20260606/stm32-nucleo-stepper/`).
- **Operator app**: iOS WaveCam (iPhone-only, personal dev build) on the live `/api/v1`.
- **Field uplink**: iPhone USB tether via the Orin's **USB-A host port** (`172.20.10.8/28`); Wi-Fi hotspot is the fallback.

### Key Connections

- **Orin ↔ Camera**: RAW VISCA UDP `192.168.100.88:1259`; video over RTSP.
- **Orin control API**: `http://<orin>:8088/api/v1` (status / safety / ptz / media / config / telemetry / agent / system).
- **Live detector model**: `yolov8n.engine` (TensorRT). To confirm what's running, trace systemd `wavecam.service` ExecStart → `config.orin.servo.yaml` → `detector.model`, or read `GET /api/v1/config`. (The `yolo26n.pt` code default is NOT what runs.)
- **Legacy Orin ↔ STM32**: UART `/dev/ttyACM0` @115200 (F401RE gimbal), archived and not part of the active WaveCam runtime.
- **Two-agent collab**: `.agent-collab/bin/collab.py` (emit / claim-open / claim-close). Claim before editing shared files.

## GPS Architecture (current)

**Plan**: LoRa GPS does coarse point + zoom when the subject is too far for YOLO/color to be reliable (toward 300m); vision (orange-confirmed person) refines once the subject is resolvable in-frame; **Cinematic Zoom** then holds subject size.

- **GPS (FINAL): LoRa-only, 2× SeeedStudio Wio Tracker L1 Lite** (nRF52840 + SX1262 LoRa + L76K GPS, Meshtastic). The Apple-Watch / BN-220 / iPhone-relay / **Cloudflare-tunnel** design is **DROPPED — do not reintroduce.**
  - **Remote tracker** (on the subject): GPS + an **IMU** (heading/speed/motion) → feeds the pointing predictor to *lead* the surfer. Battery + Qi wireless charging.
  - **Base tracker** (on the Orin, **USB-A serial** `/dev/ttyACM*`): receives the mesh; its own L76K GPS = the **camera/tripod reference position** (averaged once at setup).
- **Heading reference**: PTZ pan-home = "forward"; pan offset from home maps a GPS bearing to a pan target. No magnetometer on the camera (motor-magnet interference). The IMU lives on the *subject*, far from the motors.
- **Ingest**: Meshtastic serial client on the base Wio → `NormalizedFix` → the live-validated `orin/gps_fusion/` pointing stack. Spec: `docs/superpowers/specs/2026-06-05-gps-lora-cueing-design.md`.
- **Status (2026-06-06)**: hardware on the bench; pointing math exists; remaining = Meshtastic ingest + config + wiring.

## Development Guidelines

- **Test on the live rig** before claiming a backend/vision change works (Codex's lane).
- **Use TensorRT** engines for production inference (live = `yolov8n.engine`).
- **iOS**: feature-detect every config-driven control against `GET /config`; keep **portrait + landscape parity** (the phone tripod-mounts); verify layout on-device.
- **Document** architecture/hardware changes under `docs/`.

## Common Development Tasks

### iOS app (Claude's lane)
```bash
# Regenerate the project after ADDING/removing a Source file, then build+install on device:
cd ios/WaveCam && xcodegen generate
# build/install recipe (signing team, device UDID): see .claude memory ios-app-build
```

### Tune the live tracker
- iOS **Tune** tab → `config/hot` applies live (no restart). Restart-only keys → Tune > Service > Restart.
- Live page in a browser: `http://<orin>:8088`.

### Coordinate with Codex
```bash
python3 .agent-collab/bin/collab.py emit --from claude --to codex --type status --summary "..."
python3 .agent-collab/bin/collab.py claim-open --from claude --scope <path> --mode write --lease-minutes 30 --summary "..."
```

## Key Business Rules

- All code changes committed to this master repo (stage files explicitly; never `git add -A`)
- Vision must maintain 30+ FPS
- The agent/supervisor is **SUPERVISE-ONLY** — it never autonomously moves the camera
- Emergency Stop / KILL must stay reachable in the iOS app at all times
- iOS must work in **both portrait and landscape** (tripod-bracket mount)
- Confirm the live deploy before telling Zack a feature is "live"

## Coding Standards

See `.claude/CODING_STANDARDS.md` for detailed coding standards.

## Architecture

See `.claude/ARCHITECTURE.md` for system architecture overview.

## Development Practices

See `.claude/DEVELOPMENT_PRACTICES.md` for development workflow and practices.

## DO NOT

- Don't bypass established patterns without discussing first
- Don't skip tests
- Don't create features without searching memories for existing patterns
- Don't end a session without saving key learnings
- Don't let the agent/automation move the camera without the supervise-only gate + a reachable Emergency Stop
- Don't touch the Orin runtime/deploy (Codex/Zack's lane); don't `git push` to remote

## Gotchas (hard-won — 2026-06-05)

- **The build is the source of truth, not SourceKit.** Inline "Cannot find X in scope" / "Extra argument 'conformingTo'" diagnostics are almost always stale single-file indexing noise — trust only `xcodebuild … ** BUILD SUCCEEDED **`.
- **`main` is push-protected.** A hook + org policy block direct pushes to `main` (even when authorized). Merge via a PR or hand the fast-forward to Codex. A command containing both "push" and "main" trips the guard even when the push target is a feature branch — split such commands.
- **Codable: put a tolerant `init(from:)` in an _extension_,** not the struct body — an in-body custom init removes Swift's synthesized memberwise init and breaks every construction site. Decode non-essential fields with `decodeIfPresent ?? default` so a partial backend response doesn't throw → false "operation failed".
- **iOS networking:** every GET should go through `getWithFallback` (tether→Wi-Fi); a non-failover request surfaces as a false "Orin unreachable."
- **Orin outage triage:** ping the gateway (`192.168.1.1`) vs the Orin (`.155`). Gateway clean + Orin 100% loss = the Orin (likely DHCP IP-drift, e.g. to `.50`). A `.155` DHCP reservation is in place; full procedure in `docs/ORIN_FIELD_RELIABILITY.md`.
- **Collab bus:** partners can replay stale events as "fresh" — independently verify any state claim (`git fetch`, your own `curl`/`ping`) before acting. The Stop-hook now drains the inbox backlog to the latest event per turn (fixed 2026-06-05).

## IMPORTANT: Project Context

This file (CLAUDE.md) is automatically loaded at the start of every Claude Code session. Keep it updated with:
- Important project context
- Current architecture decisions
- Team conventions
- Common gotchas
- Frequently referenced information

**Update this file as the project evolves!**
