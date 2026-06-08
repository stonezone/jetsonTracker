# GPS / LoRa Coarse-Cueing — Design Spec

- **Status:** Decisions **LOCKED (2026-06-06)** — camera position = base Wio GPS (§6.1); remote IMU for prediction (§6.6); LoRa-only / **2× Wio Tracker L1 Lite**. **Hardware on the bench (2026-06-06)** — ready to build the Meshtastic ingest. Backend lands as PR-only while Codex is out (Zack deploys).
- **Date:** 2026-06-05
- **Owners:** Backend/Orin = Codex · iOS + this spec = Claude
- **Supersedes/feeds:** `docs/wavecam_build_plan.md` (GPS phases), `orin/wavecam/wavecam/gps_stub.py` (the ingest seam)

## 1. Goal

When the foil-surfer is too far offshore (toward 300 m) for YOLO + orange-color to lock reliably, use the subject's **LoRa GPS** to **coarsely point and zoom** the PTZ at the subject so vision can then acquire and refine. Once vision has an orange-confirmed person passing the size-gate, **vision leads** and Cinematic Zoom holds subject size; GPS drops back to a standby cue. GPS never moves the camera autonomously outside the existing **SUPERVISE-ONLY** gate, and Emergency Stop/KILL stays reachable at all times.

### Non-goals (this spec)
- No magnetometer/compass (motor magnets corrupt it — pan-home = "forward" is the heading reference, per architecture).
- No live phone→GPS relay (the Apple-Watch / iPhone-tunnel / Cloudflare path is **dropped**).
- Wave-state classification (RIDING/RETURNING/IDLE) is acknowledged but **deferred to a later phase** (Section 9, P5).

## 2. What already exists (reuse, don't rebuild)

This is the key framing: the GPS pointing stack was largely built and **live-validated** for the earlier architecture. The work is **ingest + camera-position + integration**, not new pointing math.

| Asset | State | Location |
|---|---|---|
| `NormalizedFix` contract + `get_fix()` seam | stub, ready to swap | `orin/wavecam/wavecam/gps_stub.py` |
| GPS→VISCA pointing loop (pan-primary, lead/feed-forward, dist→zoom) | **EXISTS, live-validated sign conventions** | `orin/gps_fusion/pointing_controller.py` |
| Bearing/elev→encoder via 2-point empirical calibration + persistence | EXISTS | `orin/gps_fusion/camera_pose.py` |
| Geo math (bearing, haversine, position prediction) | EXISTS | `orin/gps_fusion/geo_calc.py` |
| Fusion engine + vision offset/size-gate | EXISTS (per build plan) | `orin/gps_fusion/fusion_engine.py`, `vision_assist.py` |
| Offline unit tests for pose/pointing/fusion/vision | EXIST | `orin/scripts/test_*.py`, `orin/wavecam/tests/test_fusion.py` |
| Heading/tilt/zoom calibration API | **LIVE** | `POST /api/v1/calibration/{heading,tilt,zoom}`, `GET /api/v1/calibration` |
| PTZ primitives (home, velocity, zoom, auto, stop) | LIVE | `POST /api/v1/ptz/*` |
| `supported.gps` feature flag scaffold | present in `GET /config` | `orin/wavecam/wavecam/control_api.py` |
| iOS Calibrate tab | LIVE | `ios/WaveCam/Sources/` (Calibrate) |

**Caveat to fix during port:** `camera_pose.py` and `pointing_controller.py` live in the legacy `orin/gps_fusion/` tree and assume the **"beach iPhone base GPS next to the camera"** for the camera's own position (`lock_base_position()`). That iPhone-GPS source is dropped. They must be (a) moved/wired into the live `orin/wavecam/wavecam/` pipeline, and (b) re-pointed at the **base Wio's own L76K GPS** as the camera position (§6.1). Note `lock_base_position()` (averages base GPS fixes, rejecting poor-accuracy ones) is **reusable as-is** — just feed it the base Wio's fix instead of the iPhone's.

## 3. Architecture / data flow

```
[REMOTE Wio Tracker L1 Lite on the foiler: L76K GPS + IMU (heading/speed/motion)]
        │  LoRa / Meshtastic (0.2–2 Hz)
        ▼
[BASE Wio on the Orin (USB-A serial): receives mesh; its own L76K GPS = camera position]
        │
        ▼
[Orin: Meshtastic serial reader] ──► NormalizedFix(lat,lon,course,speed,ts,age) + IMU   ← replaces GpsStub
        │
        ▼
[Fusion] ── base-GPS camera position + subject fix + IMU-assisted lead ──► bearing + range + lead
        │                                                           │
        │  vision has confirmed orange-person & size-gate? ── yes ──┤
        │                                                           ▼
        │                                              [Vision leads: existing tracker + Cinematic Zoom]
        │  no / target lost                                         │
        ▼                                                           │ target lost N s
[PointingController] ── bearing→pan-enc (calibrated), range→zoom ──►[VISCA PTZ]  (SUPERVISE-ONLY)
```

**Mode FSM (fusion):** `GPS_CUE → ACQUIRING → VISION_FOLLOW → (target lost ≥ T_lost) → GPS_CUE`. GPS only owns the camera in `GPS_CUE`/`ACQUIRING`; vision owns it in `VISION_FOLLOW`. Hard interlock (already present in the dashboard safety guard): the two never drive the PTZ simultaneously.

## 4. Components & work split

**Codex (Orin/backend):**
1. **Meshtastic ingest** — serial reader → `NormalizedFix`; derive course/speed from position deltas; expose `enabled=True` + `get_fix()`; staleness/age handling. Replaces `GpsStub`.
2. **Port** `camera_pose` + `pointing_controller` + `geo_calc` into the `wavecam` package; re-point camera position to the new source; keep the validated sign conventions and tunables in config.
3. **Fusion/handoff FSM** wiring GPS cue ↔ existing vision tracker, under the supervise-only gate.
4. **Control API**: `GET /api/v1/gps` (status), set `supported.gps=true` when ingest is live, `POST /api/v1/gps/camera-position` (set/lock camera fix), reuse `/calibration/heading` for pan-home.

**Claude (iOS):**
5. **GPS status card** — fix lock, age, range-to-subject, bearing, current mode (GPS_CUE/ACQUIRING/VISION_FOLLOW). Feature-detected on `supported.gps`; ships inert until backend advertises it.
6. **Calibration slice** — extend the existing Calibrate tab: "set camera position" + the 2-point pan-home calibration ("aim at known bearing → capture"). Reuse `/calibration/heading`.
7. **Safety surface** — GPS mode never hides KILL; show a clear "GPS-cueing (supervised)" state; stale-fix warning.

## 5. Control API additions

- `GET /api/v1/gps` → `{ enabled, fix:{lat,lon,course,speed,age_sec}|null, camera_set:bool, bearing_deg, range_m, mode, stale:bool }`
- `POST /api/v1/gps/camera-position` → set camera lat/lon/alt (body or "lock from N samples"); persists to `CameraPose`.
- `GET /config` → `supported.gps = true` once ingest is live (drives all iOS gating).
- Reuse existing `POST /api/v1/calibration/heading` (2-point pan) and `/ptz/*` primitives — no new pointing endpoints needed.

## 6. Key design decisions

**6.1 Camera position source — RESOLVED (Zack, 2026-06-06): the base Wio's own GPS.** We run **2× Wio Tracker L1 Lite**, so the **base unit on the Orin already has an L76K GPS** — its fix (averaged once at setup, since the tripod is stationary) *is* the camera/tripod reference position. No phone-GPS, no manual entry, no dedicated extra node — both endpoints have GPS, symmetric and automatic. The earlier options (one-shot phone read / manual entry / a separate node) are moot. Backend: lock the base fix into `CameraPose` from N startup samples (`POST /gps/camera-position` "lock from N").

**6.2 Pan-home = forward.** Empirical 2-point calibration (`calibrate_pan_two_point`): aim at two known-bearing references (landmarks, or the LoRa node walked to two spots) → encoder↔degree scale + anchor. Already implemented + has a live API.

**6.3 Range → zoom.** Reuse `pointing_controller` dist→zoom (near/far bounds, max-frac). Coarse only; vision + Cinematic Zoom does the fine size-hold once locked. Tunables in config (`zoom_near_m`, `zoom_far_m`, …).

**6.4 Fix staleness failsafe.** If `age_sec` exceeds a threshold (e.g. 3–5 s; LoRa is 0.2–2 Hz), GPS cue is marked `stale` and the PTZ holds (no chasing a dead fix). Surfaced in the iOS card.

**6.5 Vision-overrides-GPS.** The instant vision confirms (orange person + size-gate), fusion switches to `VISION_FOLLOW`; GPS becomes advisory. Prevents GPS jitter from fighting a good visual lock.

**6.6 Remote IMU → motion prediction (Zack, 2026-06-06).** The remote Wio carries an **IMU** (heading/speed/motion). It augments GPS course/speed to *lead* the subject through LoRa lag — feeding `pointing_controller`'s prediction/feed-forward (`predict_position`, `enc_per_vel`) so the camera aims where the surfer is *going*, not where he was. The IMU sits on the **subject**, far from the camera motors → none of the magnetometer-near-motors concern that keeps a magnetometer off the camera. It is an enhancement (course/speed refinement), **not a hard dependency** — GPS-only still works if IMU data is absent/stale.

## 7. Safety

- **Supervise-only**: GPS cueing obeys the existing gate; the agent/automation never moves the camera without it.
- **E-Stop/KILL** reachable in every GPS state in the iOS app.
- **Single-owner PTZ**: hard interlock so GPS and vision never co-drive (existing dashboard guard extended to the FSM).
- **Fail-safe-hold** on stale/lost fix (6.4) rather than blind pan.

## 8. Testing

- **Offline (exists/extend):** unit tests for `camera_pose`, `pointing_controller`, `fusion_engine`, `vision_assist` with synthetic tracks (already present in `orin/scripts/` + `orin/wavecam/tests/`).
- **Bench:** feed canned Meshtastic frames → confirm `NormalizedFix` + bearing/range.
- **Yard:** walk the LoRa node; verify the PTZ pans to bearing + zooms by range (supervise-only); verify handoff to vision when you enter frame in the orange rashguard; capture a clip to `docs/verification/`.
- **Field:** 50–300 m offshore acquisition → vision handoff → Cinematic Zoom hold.

## 9. Phasing

- **P1 — Ingest:** Meshtastic serial → `NormalizedFix`; `GET /api/v1/gps`; `supported.gps`. (Codex)
- **P2 — Camera pose + calibration:** camera-position source (6.1) + 2-point pan-home; iOS calibration slice. (Codex + Claude)
- **P3 — Coarse point/zoom (supervise-only):** port `pointing_controller` into the live loop; bearing→pan, range→zoom; iOS GPS status card. (Codex + Claude)
- **P4 — GPS↔vision fusion/handoff:** the mode FSM + interlock. (Codex; iOS shows mode)
- **P5 — Wave-state classifier + per-spot cone:** RIDING/RETURNING/IDLE; deferred. (Codex)

## 10. Open decisions

1. ~~Camera position source~~ — **RESOLVED (§6.1): the base Wio's own GPS.**
2. **Modes** — single orange-cue path for v1, or carry the build plan's `winging` / `tow_boogie` two-mode toggle from the start? *(still open — Zack)*
3. **Per-spot riding cone** (P5) — needed for wave-state only; fine to defer.

## 11. Risks

- **LoRa update rate** at distance (0.2–2 Hz) → coarse only; mitigated by lead/feed-forward + vision refine. (Acceptable by design.)
- **Camera-position error** propagates to bearing error; worst at close range. 2-point pan calibration + one-shot averaged fix mitigate; coarse cue tolerance is generous at 50–300 m.
- **Legacy→live port drift** — `gps_fusion/` was validated on the prior architecture; re-validate sign conventions on the current VISCA path during P3 (the controller notes they were live-checked, but confirm post-port).
- **Meshtastic serial reliability** on the Orin USB — bench-test before field.
