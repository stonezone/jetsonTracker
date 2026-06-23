# WaveCam (jetsonTracker repo) - vision-based auto-filming PTZ camera

## Project Overview

**WaveCam** is a vision-based auto-filming PTZ camera (a SoloShot replacement) that films Zack foil-surfing **50-300m offshore**. A Jetson Orin Nano runs YOLO11n person detection plus a bright-**orange-rashguard color cue** to keep the subject framed; a **Prisual NDI PTZ camera** does pan/tilt/zoom; a native **iOS app + watchOS companion** are the operator consoles; **direct-LoRa Wio GPS** already provides coarse-point/zoom while vision refines. Two agents build it: **Codex** = Orin backend + deploy; **Claude** = iOS app + device installs + Zack-comms.

> The DIY 2xNEMA17 stepper gimbal and the Apple-Watch/BN-220 GPS in the old "jetsonTracker" design are **SUPERSEDED**. The canonical current architecture lives in the `.claude` memory `wavecam-architecture-pivot` — verify against reality, not legacy docs.

**Tech Stack**: Python (FastAPI control API, OpenCV, PyTorch/TensorRT) on the Orin; Swift/SwiftUI iOS app. Archived legacy code includes C/STM32 firmware, but it is not active WaveCam runtime.
**Database**: None (embedded/IoT project)
**Development Environment**: Local Mac + Jetson Orin (Codex/Zack deploy to the Orin)

## Memory — file index is PRIMARY; claude-os is a code-search sidecar

**Durable cross-session memory lives in the file-based auto-memory at `~/.claude/projects/-Users-zackjordan-code-jetsonTracker/memory/`** — a hand-curated `MEMORY.md` index (auto-loaded into every session) pointing at one-fact-per-file `.md` memories. This is the **source of truth** for decisions, gotchas, and operational lessons. The dir is under its own git for rollback.

- **Recording a lesson:** write a `<slug>.md` fact file (frontmatter `name`/`description`/`metadata.type`) and add a one-line pointer to `MEMORY.md`. Enforced every conversation by hooks (`~/.claude/hooks/memory-currency-check.py`): SessionStart reports drift/staleness; **Stop blocks if the index has orphans or broken links**, so keep `MEMORY.md` in sync. Deep periodic cleanup: the `garden-maintain` skill.
- **claude-os (code-forge MCP `http://localhost:8051`, start via `~/claude-os/start_all_services.sh`) is SECONDARY** — two uses only:
  - `JetsonTracker-project_index` — semantic search over the codebase (`mcp__code-forge__search_knowledge_base`); refresh via `mcp__code-forge__index_semantic`.
  - `JetsonTracker-project_memories` — optional cold archive for bulky/dated worklogs (`/claude-os-remember`, `mcp__code-forge__upload_document`).
- The old `project_profile` + `knowledge_docs` + `code_structure` KBs were **DELETED 2026-06-22** (they held the superseded stepper-gimbal/watch-GPS architecture). Don't recreate them — the file memory + this CLAUDE.md are the live profile. Verify drift-prone claims against the repo/live API, not stale KB text.

## Open work — `docs/TODOs/` (REVIEW AT SESSION START)

**`docs/TODOs/` is the live to-do list of planned-but-unfinished work.** At the start of every session, **read the files in `docs/TODOs/`** to see what's outstanding before picking up new work. The rules (full version in `docs/TODOs/README.md`):

- **Anything that needs doing gets a plan file here** — copy `docs/TODOs/_TEMPLATE.md` to `YYYY-MM-DD-short-slug.md`. This applies to fixes, refactors, follow-ups, investigations: if it's deferred, it's a plan in this folder.
- **Every plan carries a `Created:` date and a `Status:` line**, and keeps a **running, dated worklog** (newest first) updated as the work proceeds — so any agent or Zack can resume it cold.
- **When the work is complete and verified, DELETE the plan file.** The folder should only ever show *open* work; record any lasting lesson in a `.claude` memory or commit message before removing. (Backend/rig plans still need Zack assignment + a bus claim before execution — writing the plan is fine anytime.)
- Specs live in `docs/superpowers/specs/`, point-in-time reviews in `docs/reviews/`, durable facts in `.claude` memory. `docs/TODOs/` is *only* the active plan list.

## Project-Specific Information

### Repository Structure

This is the **MASTER REPO** for all WaveCam code:

```
jetsonTracker/                 # master repo (product = WaveCam)
├── orin/
│   └── wavecam/               # CANONICAL backend: FastAPI control API (/api/v1),
│                              #   vision tracker, fusion, supervisor (Codex's lane).
│                              #   control_api was SPLIT 2026-06-12 (PR #25) into
│                              #   control_{utils,snapshots,media,logs,presets,config,
│                              #   ptz,calibration,system}.py + an 811-line coordinator.
│                              #   estimator.py (Plan-3 shadow filter) + wavecam/tools/sim/
│                              #   (synthetic-scenario harness) exist as of PR #27.
│                              #   Tests: 340+ incl. mypy type gate (tests/), CI runs them on every push
│                              #   (.github/workflows/backend-tests.yml).
├── ios/WaveCam/               # native iOS operator app, SwiftUI (xcodegen) (Claude's lane)
│                              #   + WaveCamWatch Tier-1 companion (Sources-Watch/)
├── docs/
│   ├── superpowers/specs/     # design specs (control API, iOS app, cinematic zoom, supervisor)
│   ├── superpowers/plans/     # ACTIVE plans: roadmap HTML (start here), prewater (done),
│   │                          #   closed-loop-pointing (bench-gated), target-estimator (live)
│   └── hardware/              # field-power wiring, Wio canonical configs (wio-config/)
├── archive/legacy-20260606/   # archived legacy GPS relay, Nucleo, stepper gimbal, dashboard docs/code
├── .agent-collab/             # Claude+Codex coordination bus (claims, events, audit log)
└── archive/                   # preserved retired material; do not delete without explicit request
```

### Development Workflow

- **Claude is PRIMARY on both lanes** — backend (`orin/wavecam/`) and iOS (`ios/WaveCam/`). No per-task Zack assignment is required to edit backend code (standing grant 2026-06-21, supersedes the old Codex-lane gate; see `.claude` memory `backend-authority-grant`). Codex/other agents are optional reviewers. The bus is for **collision-avoidance only** (claim a scope *when another agent is actively working it*), not a permission gate.
- **Deploy to the Orin** (rsync + restart `wavecam.service`): Claude is authorized standing. Always deploy via `deploy.sh` (stamps `/version`). The bus claim is collision-avoidance, not a gate. (KILL-reachable + supervise-only safety invariants ALWAYS hold — these are non-negotiable rails, not lane conventions.)
- iOS build/install: `ios/WaveCam/build-device.sh` (git-stamped build numbers). Full recipe: see `.claude` memory `ios-app-build`.
- SSH to the rig: `ssh orin` (zack@192.168.1.155).
- "committed" != "deployed" — confirm the live deploy before telling Zack a feature is live.

### Live System Map

- **`:8088`** = WaveCam control API (`/api/v1`) + the live tuning web page (now incl. the **ASK CLAUDE** agent chat + arm toggle). This is the **ACTIVE tracker** the iOS app drives. Tools > Web points here.
- **`https://wavecam.freddieland.com`** = same `:8088` UI/API exposed through the Cloudflare `robot-core` tunnel (Google Auth + Access policy, `zackjordan@gmail.com` only).
- **`:8080`** = retired legacy Dash service. It should stay stopped/disabled; do not re-enable it.
- **`:8765` / `ws.stonezone.net`** = retired Apple Watch/iPhone Cloudflare GPS relay. `gps-server.service` is disabled and the tunnel ingress was removed.

### Hardware Stack

- **Camera**: Prisual **NDI PTZ** — **20x optical zoom** (subject is well-resolved at 300m; the range challenge is narrow-FOV *acquisition*, NOT pixel count — don't treat distant subjects as a small-object detection problem). RAW VISCA over UDP `192.168.100.88:1259` (no auth; NOT Sony 8-byte framed). Video = RTSP (`/1` 1080p60, `/2` 640x360); ONVIF `:81` backup.
- **Compute**: Jetson Orin Nano (YOLO11n TensorRT). Wired LAN `192.168.100.10`.
- **GPS**: LoRa — SeeedStudio **Wio Tracker L1 Lite** (nRF52840, SX1262, L76K multi-constellation) running the custom `firmware/direct-lora/` firmware. The remote tracker sends GPS over LoRa; the base Wio emits JSONL over USB; `DirectRadioGps` feeds WaveCam. Meshtastic is no longer used.
- **Legacy gimbal**: STM32 Nucleo **F401RE** + 2x NEMA17 (the old DIY pan/tilt; firmware archived under `archive/legacy-20260606/stm32-nucleo-stepper/`).
- **Operator app**: iOS WaveCam (iPhone-only, personal dev build) on the live `/api/v1`.
- **Field uplink**: iPhone USB tether via the Orin's **USB-A host port** (`172.20.10.8/28`); Wi-Fi hotspot is the fallback.

### Key Connections

- **Orin ↔ Camera**: RAW VISCA UDP `192.168.100.88:1259`; video over RTSP.
- **Orin control API**: `http://<orin>:8088/api/v1` (status / safety / ptz / media / config / telemetry / agent / system).
- **Live detector model**: YOLO11n TensorRT (`yolo11n.engine`) — swapped from yolov8n on 2026-06-15, rebuilt directly on the Orin (cross-device export problem is resolved). The `yolo26n` family is NOT usable on the rig's ultralytics (8.3.233); the model is not the tracking bottleneck. To confirm what's running: `GET /api/v1/config` or trace systemd `wavecam.service` ExecStart → `config.orin.servo.yaml` → `detector.model`.
- **Orin ↔ base Wio**: USB serial `/dev/ttyACM0` @115200, JSONL from the custom direct-LoRa firmware.
- **Legacy Orin ↔ STM32**: UART (F401RE gimbal), archived and not part of the active WaveCam runtime.
- **Two-agent collab**: `.agent-collab/bin/collab.py` (emit / claim-open / claim-close). Claim before editing shared files.

## GPS Architecture (current)

> **DEPLOYED STATE (2026-06-14): the custom direct-LoRa firmware is the LIVE GPS source** (`gps.source=direct_lora` on the rig), NOT Meshtastic. Root-cause of the long Wio no-fix: the L76K GNSS speaks **CASIC/PCAS at fixed 9600** (not MTK/PMTK/57600) — firmware fixed in `firmware/direct-lora/`. The base Wio now has a battery installed; acquire the fix on battery power and keep it off the Orin's USB rail (host RF noise → 0 sats), then connect USB data. `/status.gps` now exposes `target_battery_mv`/`target_sats`; `/config` advertises `supported.tracking_mode`. A **`tracking.mode` hot key (`auto` | `gps_only` | `vision_only`)** gates the arbiter — `gps_only` forces GPS pointing and ignores vision (no false-color-lock hijack). A separate **`tracking.enabled` hot key (DISABLE-PTZ latch)** turns autonomy OFF entirely — the arbiter idles every frame so a manual aim holds until re-enabled (distinct from the transient STOP-PTZ hold; web + iOS Tune toggles). A **CALIBRATE mode** (operator wizard, iOS Calibrate tab) establishes camera location + heading: spec `docs/superpowers/specs/2026-06-13-calibrate-mode-design.md`; endpoints `/api/v1/calibration/{session/start,session/exit,location,level,heading-lock,validation,validation/confirm}` (owner=`calibrate` PTZ lockout — kills the arbiter fight; KILL cancels CALIBRATE). **Heading = aim at a STATIONARY target AT RANGE (≥50 m / a surveyed landmark), NOT close** (10 m ≈ 27° GPS-bearing error); scale fixed at 14.4 counts/deg; location lock = averaged base fix with a model (HDOP×UERE) radius. The legacy Meshtastic relay path is fully superseded.

**Plan**: LoRa GPS does coarse point + zoom when the subject is too far for YOLO/color to be reliable (toward 300m); vision (orange-confirmed person) refines once the subject is resolvable in-frame; **Cinematic Zoom** then holds subject size.

- **2× SeeedStudio Wio Tracker L1 Lite** (nRF52840 + SX1262 LoRa + L76K GPS). **Remote tracker** (on the subject): GPS + IMU → feeds the pointing predictor to *lead* the surfer. **Base tracker** (USB serial `/dev/ttyACM*` on the Orin): its own L76K = the camera/tripod reference position. The Apple-Watch / BN-220 / iPhone-relay / **Cloudflare-tunnel** GPS-relay design is **DROPPED — do not reintroduce.**
- **Heading**: set via CALIBRATE mode (see the DEPLOYED note above); pan-home = "forward", pan offset maps a GPS bearing to a pan target. No camera magnetometer (motor-magnet interference); the IMU lives on the subject.
- **Ingest**: `DirectRadioGps` reads the base Wio's JSONL over USB (off-thread, lock-guarded) → camera→target distance/bearing/stale. (Old Meshtastic `MeshtasticGps` path superseded.)
- **Specs**: `docs/superpowers/specs/2026-06-05-gps-lora-cueing-design.md`, `2026-06-09-gps-control-loop-design.md`. Control-loop P0/P1/P2-v1 deployed 2026-06-10; GPS knobs in iOS Tune > GPS TRACKING.

## Development Guidelines

- **Test on the live rig** before claiming a backend/vision change works (Codex's lane).
- **Use TensorRT** engines for production inference (live = `yolo11n.engine`).
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
- The agent is **supervise-only by default**; an **operator-only ARM toggle** (default OFF, TTL auto-expire, KILL-disarmed) grants it a shell to act via the control API — it never moves the camera *unattended*. Spec: `docs/superpowers/specs/2026-06-19-acting-agent-design.md`; memory `acting-agent-autonomous-build`.
- Emergency Stop / KILL must stay reachable in the iOS app at all times — **KILL is human-only and supreme** (never an agent capability; it disarms the agent + stops motion)
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
- Don't let the agent move the camera without the **operator ARM gate** + a reachable KILL; KILL stays human-only (never an agent tool)
- Don't `git push` to `main`/`master` on your own initiative (hook + org policy block it); a feature-branch push needs an in-turn user request. Backend edits + `deploy.sh` deploys no longer need a per-task assignment (Claude is primary) — but never bypass `deploy.sh`, the KILL/supervise-only rails, or the explicit-staging rule.

## Verification discipline — hard-won 2026-06-22/23 (DO NOT repeat these)

Specific failures from the calibration build that shipped broken builds to the rig + phone. Read before deploying/installing or saying "done".

- **"Done" = observed working, never "it compiles/deploys/installs."** Say what was verified vs not. For a multi-step flow (CALIBRATE → aim → capture → validate → confirm → track; PTZ ownership), verify the WHOLE sequence, not just the step you changed. Canonical miss: *"verified the takeover, not the sequence after it"* — a velocity-takeover that passed in isolation made every later calibrate step refuse `calibrate_owner_lost` and stranded the rig in `manual`. Write/run the end-to-end test (or exercise on-device) BEFORE declaring ready or deploying. Unit/compile green ≠ feature works.
- **The mission is the camera tracking the foiler — not clean code.** Don't let tech-debt / refactor / density / polish substitute for validating that tracking actually works. When accuracy/latency is unmeasured, MEASURE before building more features (the 12°-cal-vs-3°-FOV gap won't be closed by another refactor). Flag adjacent work; don't drift into it for a whole session.
- **Replacing a hardcoded constant with a field: `grep` EVERY old usage repo-wide and update all sites.** `subject_alt_m` was added everywhere but `offset_calibrate` kept a hardcoded `1.0` (TECH5) → biased tilt. A new field/config that supersedes a literal is not done until the literal is gone everywhere it mattered.
- **iOS UI changes need a visual check before "good."** Simulator builds are blocked by the watch `AppIcon` single-`universal`-1024 quirk (device/on-device builds are fine), so verify density/layout changes ON-DEVICE (screenshot/look) — never ship a UI change you haven't seen and call it good. `controlSize`/copy/spacing changes especially.
- **Before `deploy.sh` / `wavecam.service` restart: check `/status.authority.calibration_valid`.** It's session-scoped — a restart DESTROYS the operator's hard-won VALID. If it's True (or you're mid-field-test), warn the operator first and minimize restarts. Repeated silent resets during field testing were a real, documented friction.

## Gotchas (hard-won — 2026-06-05, expanded 2026-06-12)

- **The 4.47 rule:** pan/tilt scale = **14.4 counts/deg** (MEASURED at the hard stops, both axes; tilt zero = horizontal). The old 4.47 was unmeasured folklore that made every GPS slew land 1/3 short. Never trust an unmeasured constant; land measurements with provenance + a pinning test.
- **Bench with wavecam.service STOPPED.** Ownership contention masquerades as camera dynamics — "overshoot and hunt" was the tracker fighting the bench script; uncontended absolute moves land with zero error (~115 counts/s, ~2.4 s overhead).
- **Tooling frame source = the camera's `/snapshot.jpg`** (fresh per GET). cv2 RTSP/MJPEG captures freeze on reuse; the service's preview.mjpeg serves stale frames to readers.
- **Check `resp.ok` on every control-API call** — refusals (owner_busy etc.) are silent 200s with ok:false. PTZ velocity needs `takeover:true` to displace autonomous owners; RESUME hands the owner to "testbed".
- **iOS/watch plists: verify keys in the BUILT product** (`PlistBuddy` on the .app). Nonexistent `INFOPLIST_KEY_*` settings are dropped silently — this shipped a watch app whose background recording mode didn't exist.
- **Hot-config persists to `config.local.yaml`** (rsync-excluded) — deploys no longer clobber tuning. The estimator/sensors config sections must exist in YAML *and* the loader's known-sections list or they silently vanish (/config renders defaults and masks it).
- **Post-deploy verification must check fps>0 while LOCKED** — two "zombie rigs" (API answering, vision loop dead) passed idle checks.
- **devicectl installs the watch app directly** (the iPhone Watch app won't install dev builds — greyed icon is normal); the watch must be awake/near the phone; retry loops work.

- **The build is the source of truth, not SourceKit.** Inline "Cannot find X in scope" / "Extra argument 'conformingTo'" diagnostics are almost always stale single-file indexing noise — trust only `xcodebuild … ** BUILD SUCCEEDED **`.
- **`main` is push-protected.** A hook + org policy block direct pushes to `main` (even when authorized). Merge via a PR or hand the fast-forward to Codex. A command containing both "push" and "main" trips the guard even when the push target is a feature branch — split such commands.
- **Codable: put a tolerant `init(from:)` in an _extension_,** not the struct body — an in-body custom init removes Swift's synthesized memberwise init and breaks every construction site. Decode non-essential fields with `decodeIfPresent ?? default` so a partial backend response doesn't throw → false "operation failed".
- **iOS networking:** every GET should go through `getWithFallback` (tether→Wi-Fi); a non-failover request surfaces as a false "Orin unreachable."
- **Orin outage triage:** ping the gateway (`192.168.1.1`) vs the Orin (`.155`). Gateway clean + Orin 100% loss = the Orin (likely DHCP IP-drift, e.g. to `.50`). A `.155` DHCP reservation is in place; full procedure in `docs/ORIN_FIELD_RELIABILITY.md`.
- **Collab bus:** partners can replay stale events as "fresh" — independently verify any state claim (`git fetch`, your own `curl`/`ping`) before acting. The Stop-hook now drains the inbox backlog to the latest event per turn (fixed 2026-06-05).
- **Orin BOOT ARCHITECTURE changed 2026-06-11 (the microSD DIED mid-migration).** The rig now boots **pure NVMe**: UEFI entry `WaveCam NVMe boot` → **systemd-boot** on `/dev/nvme0n1p3` (aligned FAT16 ESP) → `/boot/Image` + `/boot/initrd` copies ON THE ESP → root `nvme0n1p1`. The L4T launcher is GONE from the boot path (its SD-resident A/B machinery died with the card; kept as `BOOTAA64.l4t` for reference). Fallback: the **WAVEBOOT** spare SD card (tested, `WaveCam card boot` second in order) — insert it and the rig boots. **Kernel/initrd updates must also be copied to `/boot/efi/boot/` or the rig boots the OLD kernel.** Full recovery lessons: `.claude` memory `orin-sd-death-recovery` (incl.: L4T kernel ignores cmdline `initrd=`; EFI-shell `cp` can corrupt — always `comp`/sha256; failed boots flip `OS chain A status: Unbootable` in Setup → L4T Configuration; odd-sector/forced-FAT32 ESPs are firmware-hostile).
- **The old Wio cold-boot stall is GONE under systemd-boot** (verified 2026-06-11: clean cold boot with the Wio plugged in; reader auto-connected, no restart needed). The unplug-to-boot ritual is retired.
- **Direct-LoRa Wio firmware is fixed-config.** Both Wios run the same custom firmware from `firmware/direct-lora/`. There is no Meshtastic preset/interval to configure. Debug via `firmware/direct-lora/tools/read_base.py` or by watching `/api/v1/status.gps`.
- **Base Wio has a battery installed; use it for acquisition, not the Orin USB rail.** Host RF noise from the Orin can drown the L76K GPS and produce 0 sats. Connect USB data after the fix is stable.
- **Never block the API request thread on GPS I/O.** `get_fix()` and `get_camera_position()` are non-blocking reads of a background thread's cached snapshot. Direct serial/GPS calls on the request thread will hang the HTTP API.
- **The "above sea level" height trap (field-fix 2026-06-21, bit twice).** Tilt depression depends ONLY on camera-vs-subject RELATIVE height, never absolute altitude. Pinning the subject at 1 m ASL while the operator enters their real altitude (110 m) → `atan2(1−110, 55) ≈ −63°` straight into the ground. Use `CameraPose.subject_alt_m` + an operator-chosen datum: base-relative (`alt_m=0`, subject=offset) or sea-level (both ASL). Never label a height field "above sea level."
- **Calibration gates are ADVISORY, not walls, for field bring-up.** `heading_lock` + `validate_heading` commit on operator-accept and only RECORD the uncertainty/miss (the operator is the authority; the rig's phone mag is unusable ~22 µT by the motor). GPS-pointing needs the FULL chain — `/status.authority`: `gps_fresh ∧ gps_calibrated ∧ base_locked ∧ calibration_valid` (+ tracking.enabled, mode auto/gps_only, not killed, owner≠calibrate); one silent False blocks everything, so read the chain, don't guess. `calibration_valid` is **session-scoped** — every restart resets it (re-validate after each boot; minimize restarts mid-calibration). `base_locked` is flipped by the drift monitor (`gps.base_drift_enabled`). Operator-critical flows (calibrate map/aim) must NEVER be modal — a sheet that covers Exit/KILL traps the operator.

## IMPORTANT: Project Context

This file (CLAUDE.md) is automatically loaded at the start of every Claude Code session. Keep it updated with:
- Important project context
- Current architecture decisions
- Team conventions
- Common gotchas
- Frequently referenced information

**Update this file as the project evolves!**
