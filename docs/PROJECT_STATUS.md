# WaveCam — Project Status

**Last updated:** 2026-06-09 (end of GPS-control-loop field-test day) · supersedes the 2026-06-06 status.
**One-line:** GPS coarse-pointing pipeline is **built, deployed, and live end-to-end**; color + person + GPS tracking all work on the rig. Remaining: **P2 (GPS→fusion, to acquire at distance)**, merge the P1 branches to `main`, and harden a few flaky edges (base GPS antenna, cross-device YOLO engine).

---

## Project Goal
**WaveCam** is a vision-based auto-filming **PTZ camera** (a SoloShot replacement) that films Zack foil-surfing **50–300 m offshore**. Jetson Orin Nano runs YOLOv8 person detection + a bright **orange-rashguard color cue**; a **Prisual NDI PTZ** does pan/tilt/zoom; a native **iOS app** is the operator console; **LoRa GPS** coarse-points/zooms at distance while vision refines. Two agents: **Codex/DeepSeek** = Orin backend + deploy; **Claude** = iOS + geo/pointing + calibration UI + displays.

---

## TL;DR

| Area | State |
|---|---|
| Vision tracking (YOLO person + orange color) | ✅ Working on-rig |
| Cinematic zoom (hold subject size) | ✅ Working (needs a YOLO person box) |
| GPS pipe (remote → base → Orin → API → iOS) | ✅ Live end-to-end |
| Aim-at-remote calibration | ✅ Captured (`gps_calibrated: true`) |
| GPS *pointing* the camera (P1 arbiter) | ⚠️ Code deployed; can't activate until `base_locked` (base needs sky) |
| GPS-assisted *acquisition* at distance (P2) | ❌ Not built — the real next milestone |
| iOS app (chip + detail card + calibration) | ✅ Built + installed on Zack's iPhone |
| P1 merged to `main` | ❌ Still on branches (deployed/installed from branches) |

---

## What's DONE

### Pre-GPS base system (live, prior work)
- FastAPI control API `/api/v1` on `:8088` (status/safety/ptz/media/config/telemetry/agent/system); RAW VISCA/UDP PTZ; RTSP video + MJPEG operator feed; live `config/hot` tuning; recording; cinematic zoom (gated by `ptz.cinematic_zoom_enabled`); supervisor + systemd `wavecam.service` (watchdog/auto-resume); optional default-off bearer auth.
- iOS app `ios/WaveCam/` — Live / PTZ / Calibrate / Tools(Tune+Agent+Web) / Connect tabs, Emergency Stop, Keychain, feature-detection on `GET /config`.
- Live detector = **`yolov8n.engine`** (TensorRT).

### P0 — GPS data correct + visible (merged to `main` @ `da35bd1`)
- `gps_geo.py` (haversine/bearing/elevation/lead), `camera_pose.py` (anchor+scale calibration, `lock_base_position`), `gps_pointing.py` (`compute_target` → pan/tilt/zoom encoders) — pure, unit-tested, ported from field-validated legacy `orin/gps_fusion/`.
- Real `gps_fix_snapshot` → `/status.gps` (distance/bearing/target_age/base_age/stale).
- iOS `GlassGPSChip` (feature-detected on `gps.source`).
- `MeshtasticGps` ingest — **off-thread** reader (daemon owns the serial; public reads are non-blocking snapshots — fixed the 2026-06-08 API hang).

### P1 — GPS aims the camera (deployed on `backend/gps-control-loop-p1` @ `6cbe0dd`; NOT merged)
- `TrackingArbiter` — coarse→fine handoff (`vision_follow | gps_tracker | idle`), hysteresis + grace.
- `ViscaIP.pan_tilt_absolute` / `zoom_absolute` / `inquire_zoom`.
- **C3 handoff fix** (release-before-request; ptz_owner refuses cross-owner steals).
- **Calibration endpoint** — `/calibration/heading` → `camera_pose.calibrate_pan_aim` → persists `camera_pose.json` → `pose.calibrated=true`; `/calibration/base-lock`, `/calibration/tilt`; `GET /calibration` returns `gps_calibrated`/`base_locked`; pose loaded on startup.
- **60 s stale threshold** (was 10 s); **GPS ingest auto-reconnect**; **cinematic gate accepts `vision_follow`**.

### iOS P1 — `feature/gps-p1-ios` @ `3185362` (installed; NOT merged)
- HUD chip: `distance·bearing`, green/amber freshness, `NO FIX` when base unlocked.
- Tap chip → detail card (source/range/bearing/target-freshness/base-fix).
- Aim-at-remote calibration: heading capture gated on a live GPS bearing.
- `docs/GPS_P1_FIELD_TEST_CHECKLIST.md`.

### Field test (2026-06-09) — verified live
- GPS pipe end-to-end (distance/bearing tracked a walking remote 11 m → 90 m, bearing following).
- Calibration captured live. Color + **YOLO person** tracking + camera-follow all confirmed by Zack.

---

## What NEEDS to be done
1. **Merge P1 to `main`.** `backend/gps-control-loop-p1` + `feature/gps-p1-ios` are deployed/installed *from branches* — fold into one P1 PR after final cross-review. (`main` is push-protected — PR only.)
2. **P2 — GPS→fusion (the real prize).** GPS→fusion confidence injection + search ROI from bearing + GPS-assisted candidate selection + zoom-by-distance curve, so the system can **acquire** at distance (closes the dead-zone). Spec "P2 refinement" in `docs/superpowers/specs/2026-06-09-gps-control-loop-design.md`.
3. **On-rig GPS-point validation** — outdoors: base-locked → calibrate → arbiter engages `gps_tracker` → camera points (only blocked today by base GPS being indoors).
4. **Base broadcast 30 s** (now 60 s) for margin under the 60 s gate.
5. **Rebuild `yolov8n.engine` on the Orin** (robustness; not urgent).
6. Deferred: VISCA-absolute on-rig send→read-back verify; operator force-GPS override (C2; E-Stop is the bailout); base drift-revalidation.

---

## ISSUES & CONCERNS

- **🟠 Fusion dead-zone (drives P2):** color-only conf **0.45** < `lock_threshold` **0.6** → color-only can *sustain* but never *acquire*. At distance with no YOLO person, the system can't acquire a lock even with GPS aimed at the subject. GPS must inform fusion (P2). [memory: `fusion-confidence-dead-zone`]
- **🟠 Base Wio GPS reliability (hardware):** onboard GPS won't hold a 3D fix indoors/marginal (doorway→stale→null, even ~7 min outside once); reboot + open sky locked it. The phone-as-base fallback was Bluetooth-invisible until reboot. **Run all GPS tests outdoors, clear sky.**
- **🟡 Stale serial on base reboot (recurring):** any base reboot/config-change re-enumerates `/dev/ttyACM0` → ingest handle stale → GPS frozen/null. Fix: `ssh orin 'bash ~/wc-restart.sh'`. [memory: `gotcha-base-reboot-stale-serial`]
- **🟡 Cross-device YOLO TRT engine (latent):** `yolov8n.engine` built on a different GPU ("engine plan file across different devices… likely to cause errors"). Works now; rebuild on the Orin to de-risk.
- **🟡 Config reverts on power-cycle:** Wio config drifts to defaults (base 60 s/30 s vs tuned 30 s/5 s; `ls_secs` reverts to 300, won't hold via CLI). Re-verify after any power event. [memory: `meshtastic-update-rate-firmware-floor`]
- **🟡 Arbiter is either/or, not a blend:** needs all of {fresh + calibrated + base_locked}; doesn't blend GPS+vision (P2's job).
- **Process note (diagnostic rigor):** several false alarms on 2026-06-09 ("YOLO broken", "not deployed", "bus one-sided") were stale/incomplete reads — confirm test conditions (subject in frame) + re-fetch fast-moving state before escalating. [memory: `diagnostic-rigor-verify-conditions`]

---

## ARCHITECTURE / SYSTEM MAP

```
Remote Wio (surfer) --LoRa SHORT_FAST--> Base Wio (Orin /dev/ttyACM0) --USB--> Orin ingest (off-thread)
   GPS+IMU, smart 2s/5m                     camera reference position                  │
                                                                                       ▼
                                                       gps_geo + camera_pose + gps_pointing (pure)
                                                                                       │
   YOLOv8n.engine (person) + orange color ─► Fusion ─► TrackingArbiter ─► controller ─► PTZ (VISCA/UDP)
                                                       (vision_follow|gps_tracker|idle)  gated: ptz.enabled + E-Stop
                                                                                       │
                                                            /status.gps + /calibration → iOS app
```

---

## CONNECTION INFO
- **Orin:** `ssh orin` (zack@192.168.1.155). Wired LAN: Orin `192.168.100.10`, camera `192.168.100.88`. Field uplink: iPhone USB tether on the Orin USB-A → `172.20.10.8/28` (Wi-Fi hotspot fallback). Credentials live only in the password manager, never the repo.
- **Camera (Prisual NDI PTZ):** RAW VISCA over UDP `192.168.100.88:1259` (no auth; NOT Sony 8-byte). Video RTSP `/1` 1080p60, `/2` 640×360; ONVIF `:81` backup.
- **Control API:** `http://<orin>:8088/api/v1`. Live tuning page at `:8088`. (`:8080` = retired legacy Dash — keep stopped.)
- **Deploy:** runtime at `/data/projects/gimbal/wavecam`; deploy = scp + `systemctl restart wavecam.service` (Codex/Zack's lane; Claude restarts only via `~/wc-restart.sh`). Detector model resolves via systemd ExecStart → `config.orin.servo.yaml` → `detector.model` = `yolov8n.engine`.

---

## BRANCHES & DEPLOY STATE
- `main` @ `da35bd1` — P0 merged; **P1 not yet merged.**
- `backend/gps-control-loop-p1` @ `6cbe0dd` — DeepSeek's P1 backend (9 commits). **Deployed.**
- `feature/gps-p1-ios` @ `3185362` — Claude's P1 iOS. **Installed.**
- Worktrees: main checkout = backend branch (DeepSeek); `jetsonTracker-gpsctl` = iOS branch (Claude). Per-agent worktrees prevent shared-checkout collisions.

---

## CLAUDE OS — how to use

Three complementary memory layers — use all three:

**1. Claude OS knowledge bases (`code-forge` MCP, served `:8051`; start via `/Users/zackjordan/claude-os/start_all_services.sh` if "Cannot connect").**
This project's KBs: `JetsonTracker-project_memories` (decisions/patterns/session knowledge), `JetsonTracker-knowledge_docs` (specs/docs/status — *this doc lives here*), `JetsonTracker-project_profile` (architecture/standards), `JetsonTracker-project_index` + `-code_structure` (auto code indexes).
- **Search first, every session:** `mcp__code-forge__search_knowledge_base(kb_name="JetsonTracker-project_memories", query="…")` — or `/claude-os-search <query>`.
- **Save knowledge:** `mcp__code-forge__upload_document(kb_name, content, filename, title, tags)` — or `/claude-os-remember <text>` / `/claude-os-save`.

**2. File-based session memory** (`~/.claude/projects/-Users-zackjordan-code-jetsonTracker/memory/`, loaded each session via `MEMORY.md`). Current: `gps-control-loop-status`, `fusion-confidence-dead-zone`, `gotcha-base-reboot-stale-serial`, `meshtastic-update-rate-firmware-floor`, `diagnostic-rigor-verify-conditions`, `ios-app-build`, `user-zack-wavecam`, `wavecam-architecture-pivot`.

**3. Agent collaboration bus** (`.agent-collab/bin/collab.py`) — Claude ↔ DeepSeek status/question/answer/ack + claims. **Agent-to-agent coordination goes on the bus, not the user chat.** Verify partner claims independently (re-fetch git/deploy/bus before asserting).

**Session protocol:** start by searching `JetsonTracker-project_memories`, reading the file-memory `MEMORY.md`, and checking the collab bus.

---

## NEXT SESSION — where to pick up
1. **Outdoors, clear sky** → base GPS locks → `base_locked: true` → run **calibrate → arbiter engages `gps_tracker` → camera points** (the one flow not yet validated end-to-end).
2. **Start P2 (GPS→fusion)** — the dead-zone fix; the milestone that makes 50–300 m acquisition real.
3. **Merge both P1 branches → one PR → `main`** (after final cross-review).
4. Housekeeping: base broadcast 30 s; rebuild `yolov8n.engine` on the Orin; re-verify Wio configs (power-cycle drift).
