# WaveCam — Project Status

> ⚠️ **Partially superseded (2026-06-23).** Calibration is now **v3** — a single-screen flow: location + height (datum: base-relative or sea-level) → heading (operator-set; the rig's phone magnetometer is unusable near the motor) → aim on the **Live** tab (feed + zoom) → **Capture** / multi-point **Refine** (least-squares offset) → **Validate** → **Confirm**. `calibration_valid` is **session-scoped** (a `wavecam.service` restart resets it). Pan/tilt scale = **14.4 counts/deg** (measured). The live detector is **yolo11n** (swapped from yolov8n 2026-06-15). For the authoritative current state see `CLAUDE.md` (canonical architecture + dev process), `MEMORY.md` (index of session lessons), and the Claude-OS `JetsonTracker-project_memories` KB. The snapshot below is kept for history.

**Last updated:** 2026-06-23 · supersedes the 2026-06-14 status.
**One-line:** Direct-LoRa GPS is the live source, vision + GPS tracking is deployed on `main`, `tracking.mode` (`auto`/`gps_only`/`vision_only`) is exposed, and WaveCamWatch is bundled. Remaining: field hardening, GPS→fusion confidence injection, and optional external 10 Hz GNSS.

---

## Project Goal
**WaveCam** is a vision-based auto-filming **PTZ camera** (a SoloShot replacement) that films Zack foil-surfing **50–300 m offshore**. Jetson Orin Nano runs **yolo11n** person detection + a bright **orange-rashguard color cue**; a **Prisual NDI PTZ** does pan/tilt/zoom; a native **iOS app + watchOS companion** is the operator console; **direct-LoRa GPS** coarse-points/zooms at distance while vision refines.

---

## TL;DR

| Area | State |
|---|---|
| Vision tracking (yolo11n TensorRT person + orange color) | ✅ Working on-rig |
| Cinematic zoom (hold subject size) | ✅ Working (needs a YOLO person box) |
| GPS pipe (tracker → base → Orin → API → iOS) | ✅ Live end-to-end via direct-LoRa |
| Calibration (v3 single-screen: location+height → heading → aim on Live → Capture/Refine → Validate → Confirm) | ✅ Shipped; `calibration_valid` is session-scoped (restart resets it) |
| GPS *pointing* the camera (P1 arbiter) | ✅ Live; requires calibrated + base_locked + fresh GPS |
| GPS-assisted *acquisition* at distance (P2) | ⚠️ Partial — `gps_boost` only; full fusion injection pending |
| iOS app + WaveCamWatch | ✅ Built + installed; watch records offline sessions and provides safety controls |
| P1 merged to `main` | ✅ Merged |

---

## What's DONE

### Base system
- FastAPI control API `/api/v1` on `:8088` (status/safety/ptz/media/config/telemetry/agent/system); RAW VISCA/UDP PTZ; RTSP video + MJPEG operator feed; live `config/hot` tuning; recording; cinematic zoom; supervisor + systemd `wavecam.service`; optional default-off bearer auth.
- **Agent subsystem** (on-demand, part of `wavecam.service` — NOT a daemon): an advisor that shells out to the Claude Code CLI (`claude -p`) on the operator's Claude subscription (default provider `claude_code`; alts `deepseek`/`glm`/`kimi`) via `POST /api/v1/agent/{chat,summon}`; PLUS an operator-**ARM**-gated, **supervise-only** acting-agent (ARM toggle default OFF, TTL 600 s). It only moves the camera when ARMed; unarmed it can only inspect/advise. **KILL is human-only + supreme** (never an agent capability; it disarms the agent + stops motion).
- iOS app `ios/WaveCam/` — Live (now includes PTZ) / Calibrate / Tools (Tune+Agent+Web) / Connect / Media tabs, Emergency Stop, Keychain, feature-detection on `GET /config`. (The old separate **PTZ** tab was merged into **Live**.)
- WaveCamWatch — Status/Tracking tab (KILL/Resume/Record remote controls) and Record Session tab (1 Hz GPS + 4 Hz IMU JSONL for offline scoring).
- Live detector = **`yolo11n.engine`** (TensorRT), rebuilt on the Orin Nano (swapped from yolov8n 2026-06-15).

### GPS
- `gps_geo.py` (haversine/bearing/elevation/lead), `camera_pose.py` (anchor+scale calibration, `lock_base_position`), `gps_pointing.py` (encoder targets) — pure, unit-tested.
- `DirectRadioGps` ingest — off-thread reader over USB serial from the base Wio.
- Custom `firmware/direct-lora/` firmware — tracker Wio sends 32-byte LoRa packets; base Wio emits JSONL; replaces the retired Meshtastic path.
- Real `gps_fix_snapshot` → `/status.gps` (distance/bearing/target_age/base_age/stale/sats/battery).
- iOS `GlassGPSChip` (feature-detected on `gps.source`).

### Arbiter + calibration
- `TrackingArbiter` — coarse→fine handoff (`vision_follow | gps_tracker | idle`), hysteresis + grace.
- `ViscaIP.pan_tilt_absolute` / `zoom_absolute` / `inquire_zoom`.
- **Calibration v3** — single-screen flow: location + height (datum: base-relative or sea-level) → heading (operator-set; the rig's phone magnetometer is unusable near the motor) → aim on the **Live** tab (feed + zoom) → **Capture** / multi-point **Refine** (least-squares offset) → **Validate** → **Confirm**; persists to `camera_pose.json`. `calibration_valid` is **session-scoped** — a `wavecam.service` restart resets it. Pan/tilt scale = **14.4 counts/deg** (measured).
- Hot `tracking.mode` key — `auto` / `gps_only` / `vision_only`.

---

## What NEEDS to be done
1. **Field hardening** — packet-age histograms at 100/300/800 m over water, on-body antenna orientation, wet-case link budget.
2. **P2 — GPS→fusion confidence injection + search ROI + zoom-by-distance**, so the system can **acquire** at distance without relying solely on color-only lock.
3. **External 10 Hz GNSS evaluation** — SparkFun MAX-M10S if L76K rate/quality becomes the binding constraint.
4. **Watch session scoring pipeline** — ingest WaveCamWatch JSONL for offline analysis.
5. **Cloudflare Access + MJPEG preview** — controls/API work through `wavecam.freddieland.com`; live preview may need a different ingress path.

---

## ISSUES & CONCERNS

- **🟠 Fusion dead-zone (drives P2):** color-only conf **0.45** < `lock_threshold` **0.6** → color-only can *sustain* but never *acquire*. `gps_only` mode works around this for far-range testing, but true distance acquisition needs GPS-informed fusion.
- **🟠 Base Wio USB RF noise:** onboard L76K can show 0 sats when the base Wio is powered from the Orin USB rail. **Workaround:** the base Wio now has a battery installed; acquire the fix on battery power, then connect USB data.
- **🟡 Stale serial on base reboot:** any base Wio reboot re-enumerates `/dev/ttyACM0` → ingest handle stale. Fix: `ssh orin 'sudo systemctl restart wavecam.service'`.
- **🟡 MJPEG through Cloudflare Access:** live preview may not stream reliably through Access; use local `:8088` on-site.
- **🟢 Cross-device YOLO TRT engine:** resolved — `yolo11n.engine` rebuilt on the Orin.
- **🟢 Meshtastic config drift:** no longer applies; direct-LoRa firmware has compile-time radio constants.

---

## ARCHITECTURE / SYSTEM MAP

```
Tracker Wio (surfer) --LoRa--> Base Wio (Orin /dev/ttyACM0) --USB--> Orin ingest (off-thread)
   L76K GNSS 1-5 Hz               camera reference position            │
                                                                       ▼
                                           gps_geo + camera_pose + gps_pointing (pure)
                                                                       │
   yolo11n.engine (person) + orange color ─► Fusion ─► TrackingArbiter ─► controller ─► PTZ (VISCA/UDP)
                                                       (vision_follow|gps_tracker|idle)  gated: ptz.enabled + E-Stop
                                                                       │
                                                            /status.gps + /calibration → iOS app / Watch
```

---

## CONNECTION INFO
- **Orin:** `ssh orin` (zack@192.168.1.155). Wired LAN: Orin `192.168.100.10`, camera `192.168.100.88`. Field uplink: iPhone USB tether on the Orin USB-A → `172.20.10.8/28` (Wi-Fi hotspot fallback). Credentials live only in the password manager, never the repo.
- **Camera (Prisual NDI PTZ, 20× optical zoom):** RAW VISCA over UDP `192.168.100.88:1259` (no auth; NOT Sony 8-byte). Video RTSP `/1` 1080p60, `/2` 640×360; ONVIF `:81` backup.
- **Control API:** `http://<orin>:8088/api/v1`. Live tuning page at `:8088` (includes the **ASK CLAUDE** agent chat + an agent **ARM** toggle). (`:8080` = retired legacy Dash — keep stopped.)
- **Remote access:** `https://wavecam.freddieland.com` via Cloudflare `robot-core` tunnel (Google Auth + Access, `zackjordan@gmail.com` only).
- **Deploy:** runtime at `/data/projects/gimbal/wavecam`; deploy **only via `deploy.sh`** (rsync + `systemctl restart wavecam.service`; stamps `/version`) — never bypass it. Detector model resolves via `config.orin.servo.yaml` → `detector.model` = `yolo11n.engine`. The `config.local.yaml` overlay (preserved across deploys) sets `gps.source: direct_lora`.

---

## BRANCHES & DEPLOY STATE
- `main` — current. Live on Orin at commit `4a25265` (branch `fix/b13-calibrate-restore`).
- `backend/gps-control-loop-p1` / `feature/gps-p1-ios` — historical P1 branches, now merged.

---

## Memory — how to use

**`CLAUDE.md` is the canonical architecture + dev process** (auto-loaded every session); **`MEMORY.md` indexes the durable session lessons** — keep the two distinct.

**File-based memory is PRIMARY** (see CLAUDE.md "Memory" section for the canonical rule). Durable lessons live in `~/.claude/projects/-Users-zackjordan-code-jetsonTracker/memory/` — a `MEMORY.md` index (auto-loaded every session) pointing at one-fact-per-file `.md` memories. Record a lesson = new `<slug>.md` + a `MEMORY.md` pointer; the SessionStart/Stop currency hook enforces index integrity each conversation.

**claude-os (`code-forge` MCP, `:8051`) is SECONDARY** — `JetsonTracker-project_index` for semantic code search, `JetsonTracker-project_memories` for bulky-worklog archive. The `project_profile`, `knowledge_docs`, and `code_structure` KBs were **deleted 2026-06-22** (held the superseded stepper/watch-GPS design) — don't recreate them.

**Agent collaboration bus** (`.agent-collab/bin/collab.py`) — collision-avoidance only.

---

## NEXT SESSION — where to pick up
1. **Outdoors, clear sky** → base Wio on its installed battery → `base_locked: true` → run **calibrate → tracking.mode auto → camera follows**.
2. **Start P2 (GPS→fusion)** — the dead-zone fix; the milestone that makes 50–300 m acquisition reliable.
3. **Field hardening** — packet loss, antenna orientation, wet-case range.
