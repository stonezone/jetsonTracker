# WaveCam — Detailed Status Report

**Date:** 2026-06-09
**Branch:** `backend/gps-control-loop-p1` (9 commits ahead of `main`)
**Live config:** `config.orin.servo.yaml` on `/data/projects/gimbal/wavecam/`

---

## 1. Hardware Map

| Component | Device | Connection | Status |
|-----------|--------|------------|--------|
| Camera | Prisual NDI PTZ | LAN `192.168.100.88`, VISCA UDP `:1259` | Operational |
| Compute | Jetson Orin Nano | LAN `192.168.100.10`, SSH `192.168.1.155` | Operational |
| Remote GPS | Wio Tracker L1 Lite (`!9f5802d5`) | LoRa to base, USB-configured from Mac | GPS functional; update rate limited by 5m smart-broadcast threshold |
| Base GPS | Wio Tracker L1 Lite (`!38c3f1fd`) | Orin USB-A `/dev/ttyACM0` | **GPS failing to acquire outdoor fix** (antenna/hardware suspected) |
| Operator | iPhone 15 Pro Max | USB tether `172.20.10.8/28` or Wi-Fi `192.168.1.155` | Operational |

---

## 2. Software Stack — What's Deployed

### 2.1 Commits on `backend/gps-control-loop-p1` (all deployed)

```
6cbe0dd fix: cinematic zoom gate accepts vision_follow owner
173da12 fix: relax stale threshold to 60s for base GPS (was 10s)
0ede3f2 fix: add POST /calibration/base-lock route (was missing)
20d327f fix: C3 handoff (release-before-request) + P1 calibration endpoint wiring
e9fa676 chore: named tuple unpack in reader loop + update run.py for auto-reconnect
7d8a8a4 fix: GPS ingest auto-reconnect on startup race + device unplug
33cea9c fix: P1 review — C3 handoff, C1 base reval, C4 atomic zoom, W2 state order, W3 lock
d77bf10 chore: remove unused imports from P1 (anti-vibe cleanup)
80a9c4b backend: P1 — TrackingArbiter + ViscaIP absolute pan/tilt/zoom
```

### 2.2 Files Changed (603 insertions, 41 deletions)

| File | Purpose |
|------|---------|
| `tracking_arbiter.py` | GPS↔vision handoff state machine (new, 125 lines) |
| `pipeline.py` | Arbiter integration, GPS commanding, C3 handoff (+129 lines) |
| `ptz_visca.py` | Absolute pan/tilt/zoom VISCA commands (+55 lines) |
| `control_api.py` | Calibration endpoints, GPS snapshot builder (+96 lines) |
| `gps_meshtastic.py` | Serial reconnect, reader health, camera_age (+80/-41) |
| `config.py` | GPS config keys (+7 lines) |
| `controller.py` | PtzAbsoluteCommand, STOP_CMD constants (+11 lines) |
| `run.py` | GPS wiring into pipeline (+14/-10) |
| `test_tracking_arbiter.py` | Arbiter unit tests (new, 127 lines) |

---

## 3. GPS Control Loop — Phase Status

### 3.1 P0: Data + Display — SHIPPED ✅

GPS data flows end-to-end: Remote Wio → LoRa → Base Wio → Orin serial → `MeshtasticGps` (off-thread) → `/api/v1/status` GPS snapshot (source, target_age, base_age, distance_m, bearing_deg, stale). iOS `GlassGPSChip` displays it on the Live HUD. Deployed, merged to main, installed on Zack's phone.

### 3.2 P1: Arbiter + Absolute Pointing — CODE COMPLETE, PHYSICALLY BLOCKED

The `TrackingArbiter` handoff state machine is fully implemented and deployed:

```
vision locked (K consecutive frames) → vision_follow (velocity servo)
vision unlocked + GPS viable → gps_tracker (absolute pan/tilt/zoom to bearing)
neither → idle (hold position)
```

GPS viability requires all three:
1. `gps_fresh` — remote target age < `stale_threshold_sec` (60s)
2. `gps_calibrated` — camera pose with reference heading captured
3. `base_locked` — base GPS age < 60s (camera position known)

**Current blocker:** `base_locked = false`. The base Wio has failed to acquire a 3D GPS fix outdoors (antenna/hardware issue suspected). Claude attempted phone-as-base fallback via BLE (transparent to backend), but GPS data is stale on the current status poll (target 6.7 hours old, base null). Both Wios need to be outdoors, powered on, and connected via LoRa for the arbiter to activate.

### 3.3 P2: GPS→Fusion Integration — NOT YET BUILT

The next phase integrates GPS data INTO the vision fusion layer so GPS bearing/distance can bias confidence, narrow the search ROI, and prioritize candidates near the expected position. Currently GPS and fusion are independent systems — the arbiter points the camera but fusion has zero GPS awareness. Spec: `docs/superpowers/specs/2026-06-09-gps-control-loop-design.md`.

---

## 4. Known Issues (by severity)

### 🔴 Critical: YOLO TensorRT Engine Cross-Device

**Symptom:** `has_person=false` despite a clearly-framed, stationary person. Person detection works briefly after restart then degrades.

**Root cause:** `yolov8n.engine` at `/data/projects/gimbal/models/` was built on a **different GPU**. The load log shows: `Using an engine plan file across different models of devices is not recommended and is likely to affect performance or even cause errors.` Inference is unreliable on this Orin.

**Impact:** Without person detections, fusion gets only color blobs (conf=0.45). The fusion confidence dead-zone (see below) means the system can never acquire lock without person+color confirmation. Tracking works at color-only sustain level but can't acquire new locks.

**Fix:** Rebuild `yolov8n.engine` ON the Orin Nano using `ultralytics export` or `trtexec --onnx=... --device=0`. Codex lane.

### 🔴 Critical: Fusion Confidence Dead-Zone

**Root cause:** In `fusion.py:_select()`, color-only blobs return `conf=0.45`. The lock threshold is `0.6`, unlock is `0.35`. The hysteresis band (0.35–0.6) means 0.45 can SUSTAIN an existing lock but NEVER ACQUIRE one. At distance where YOLO can't resolve a person, the system can never acquire lock — even with GPS pointing the camera at the subject.

| Scenario | Confidence | Can Lock? |
|----------|-----------|------------|
| Color + Person matched | 0.5 + 0.5×person_conf ≈ 0.75–1.0 | ✅ Yes |
| Color blob only (no person) | 0.45 | ❌ No (0.45 < 0.6) |
| Person only (no color) | 0.2 | ❌ No |

**Impact:** The system's primary tracking cue (orange rashguard) cannot acquire lock on its own. This defeats the "coarse-point→vision-refine" design because vision needs a person+color match to lock, but at 200-300m there is no person detection.

**Fix options:**
- Short-term: lower `lock_threshold` to 0.4 (config hot-key, immediate)
- Proper fix (P2): GPS-informed fusion — boost confidence on blobs near GPS-expected bearing

### 🟡 Medium: Base Wio GPS Hardware

**Symptom:** `base_age_sec: null` even after 7+ minutes of open sky. No 3D fix all day.

**Likely cause:** Antenna or hardware issue on the base Wio. The remote Wio gets fixes fine.

**Workaround attempted:** Phone-as-base via BLE (pair Zack's phone to base Wio, enable phone location provide). Transparent to backend.

**Action needed:** Hardware debugging of the base Wio's L76K GPS receiver.

### 🟡 Medium: `ls_secs` Firmware Revert Quirk

**Symptom:** Setting `power.ls_secs` to `4294967295` (disabled) via CLI reads back as `300`. Setting to any value reverts to 300.

**Impact:** Low for production. Light sleep triggers after 5 minutes of idle — during active surfing the remote is moving and stays awake. Only affects bench testing when the remote sits still.

**Workaround:** Keep the remote moving during tests. For permanent fix, investigate `power.is_power_saving=false` or Meshtastic firmware version.

### 🟢 Low: Remote Smart Broadcast 5m Threshold

**Symptom:** Remote updates every ~30s during land testing.

**Root cause:** Smart broadcast needs 5m movement to trigger. Walking slowly → ~30s to cover 5m.

**Impact:** None for foil-surfing (covers 5m in 2-3s). Only noticeable during bench/walking tests. The 2s minimum interval fires correctly once the threshold is crossed.

---

## 5. Config State (Live on Orin)

| Key | Current Value | Notes |
|-----|--------------|-------|
| `color.preset` | `orange_red` | Original calibrated preset |
| `color.enabled` | `true` | |
| `fusion.lock_threshold` | `0.6` | Too high for color-only acquisition |
| `fusion.unlock_threshold` | `0.35` | |
| `fusion.require_person` | `false` | Correct for color-primary mode |
| `ptz.cinematic_zoom_enabled` | `false` | Was on earlier, now off |
| `ptz.zoom_target_frac` | `0.5` | |
| `gps.stale_threshold_sec` | `60` | Deployed in config file |
| `calibration.gps_calibrated` | `true` | But reference_heading is null (reset?) |
| `calibration.base_locked` | `false` | Base Wio no fix |
| `detector.model` | `yolov8n.engine` | **Cross-device TRT engine** |

---

## 6. Immediate Next Steps (in order)

1. **Rebuild YOLO engine on this Orin** — unblocks person detection → person+color match → lock acquisition → cinematic zoom engagement
2. **Lower `lock_threshold` to 0.4** — hot-key change, immediate workaround for color-only lock acquisition (revert when GPS→fusion is built)
3. **Debug base Wio GPS** — check antenna connection, test with different position (clear sky, away from buildings/trees), consider swapping units
4. **Outdoor test session** — base outdoors with sky → `base_locked` goes true → calibrate → enable GPS → verify arbiter handoff
5. **P2 planning** — design GPS→fusion confidence injection, search ROI, zoom-by-distance

---

## 7. Reference: Specs & Docs

| Document | Path |
|----------|------|
| GPS architecture (FINAL) | `docs/superpowers/specs/2026-06-05-gps-lora-cueing-design.md` |
| GPS control loop design | `docs/superpowers/specs/2026-06-09-gps-control-loop-design.md` |
| Wio GPS optimization | `docs/hardware/WIO_TRACKER_GPS_OPTIMIZATION.md` |
| Control API split plan | `docs/control-api-split-plan.md` |
| Coding standards | `.claude/CODING_STANDARDS.md` |
| Architecture overview | `.claude/ARCHITECTURE.md` |
| Development practices | `.claude/DEVELOPMENT_PRACTICES.md` |

---

## 8. Claude OS — How To Use

### Knowledge Bases

Four KBs index the project. Always search them at session start:

| KB | Purpose | MCP Server |
|----|---------|------------|
| `JetsonTracker-project_memories` | Decisions, patterns, incident reports, session summaries | `code-forge` |
| `JetsonTracker-project_index` | Automated codebase index (tree-sitter) | `code-forge` |
| `JetsonTracker-project_profile` | Architecture, coding standards, practices | `code-forge` |
| `JetsonTracker-knowledge_docs` | Specs, hardware docs, guides | `code-forge` |

**Search syntax:**
```
mcp__code-forge__search_knowledge_base(kb_name="JetsonTracker-project_memories", query="GPS control loop")
```

### Slash Commands

| Command | Action |
|---------|--------|
| `/claude-os-search <query>` | Search all project KBs |
| `/claude-os-remember <content>` | Save to project memories |
| `/claude-os-save <title> <KB> <category>` | Save with full control |
| `/claude-os-session start <name>` | Start a named dev session |
| `/claude-os-session status` | Show session progress |

### Collab Bus

Two-agent coordination with Codex (backend/Orin lane):

```
# Check Claude's inbox
python3 .agent-collab/bin/collab.py inbox --agent claude --peek

# Emit to Codex
python3 .agent-collab/bin/collab.py emit --from claude --to codex \
  --type status --summary "Brief summary"

# Claim a shared file for editing
python3 .agent-collab/bin/collab.py claim-open --from claude \
  --scope <file-path> --mode write --lease-minutes 30 \
  --summary "What I'm doing"

# Full status
python3 .agent-collab/bin/collab.py status
```

Message types: `ack`, `answer`, `question`, `status`, `risk`, `veto`, `proposal`, `memory.delta`, `handoff`.

### Memory Files

Project memories live in `.claude/projects/-Users-zackjordan-code-jetsonTracker/memory/`. Key files:

| File | Content |
|------|---------|
| `gps-control-loop-status.md` | P0/P1/P2 status |
| `fusion-confidence-dead-zone.md` | 0.45 dead-zone analysis |
| `wavecam-architecture-pivot.md` | Architecture decisions |
| `working-style-codex-collab.md` | Collaboration conventions |
| `diagnostic-rigor-verify-conditions.md` | Verify conditions before diagnosing |

### Starting Claude OS

If KB tools report "Cannot connect":
```bash
cd /Users/zackjordan/claude-os && ./start_all_services.sh
```
MCP API serves on `:8051`.

---

*Report generated 2026-06-09 by Claude Code (codex/deepseek lane). Memory files and KBs updated in sync.*
