# GPS P1 — Field-Test Checklist (2026-06-09, for the morning)

Goal: first on-rig validation of **GPS coarse-pointing** (camera pans/zooms to the remote via GPS), then the **vision-refine handoff**. Built overnight by Claude (iOS) + DeepSeek (backend), cross-reviewed.

## 0. Safety invariants (verify first)
- GPS moves the camera **only** when: `ptz.enabled` **AND** `gps.enabled` **AND** calibrated **AND** base-locked **AND** GPS fresh. Uncalibrated by default → GPS can't move the camera until you deliberately calibrate.
- **Emergency Stop / KILL** must be reachable on the Live screen at all times. Test it interrupts GPS mode before trusting anything else.
- The supervise-only agent rule is unchanged — only the tracking pipeline (vision/gps_tracker) aims the camera.

## 1. Hardware pre-flight
- [ ] **Boot order:** Orin cold-boots with the Wio **unplugged** (U-Boot stalls otherwise) → after boot, plug the base Wio into USB-A → `sudo systemctl restart wavecam.service`.
- [ ] **Both Wios SHORT_FAST**, verify with `meshtastic --get lora` on each (config reverts on power-cycle).
- [ ] **Remote** (`02d5`): Smart Position ON, 2 s / 5 m, light-sleep disabled, GPS enabled, heading+speed+timestamp flags on.
- [ ] **Base** (`f1fd`): GPS on; broadcast can be ON-BOOT-ONLY (Orin reads it over USB).
- [ ] **Serial healthy:** `ssh orin` → `journalctl -u wavecam.service | grep -i "MeshtasticGps connected"` shows a recent connect (the auto-reconnect fix handles the startup race now).

## 2. Deploy
- [ ] **Backend:** merge/deploy DeepSeek's `backend/gps-control-loop-p1` **after the C3 handoff fix lands** (see Known Items). Restart `wavecam.service`. Confirm `/status` 200 + fps ≥ 30.
- [ ] **iOS:** install `feature/gps-p1-ios` (chip + calibration) on the phone (`xcodegen` not needed — no new Source files; build + `devicectl`).

## 3. Get GPS fixes (open sky)
- [ ] Base + remote both under **open sky** (a doorway is too obstructed — tonight the base got a time-lock but no 3D fix). 30–60 s.
- [ ] The **GPS chip** on the Live HUD should show `GPS <dist>m·<brg>°` (green). If it shows `GPS·NO FIX`, the **base** hasn't locked its 3D fix — give it more open sky.

## 4. Calibrate (aim-at-remote)
- [ ] **Calibrate tab → Base lock** — captures the camera reference position.
- [ ] **Heading step** ("aim at remote"): place the remote where you can see it, **center the camera on it** (manual joystick), confirm the GPS chip shows a live distance+bearing, then **Capture heading**. (The app sends the GPS base→remote bearing; the backend reads the pan encoder → `calibrate_pan_aim`. The capture is **blocked** if there's no GPS bearing — no bogus 0° calibration.)
- [ ] Verify `GET /api/v1/calibration` shows `calibrated: true` (persists to `camera_pose.json`, survives restart).

## 5. Enable + test GPS pointing (the core P1)
- [ ] Tune → enable `gps` + confirm `ptz.enabled`. E-Stop within reach.
- [ ] **Pointing accuracy:** does the camera pan to the remote's bearing + land on/near it? (Conservative GPS speeds: pan 4, tilt 3.)
- [ ] **Coarse track:** walk the remote → camera follows (coarsely; ~2 s GPS cadence).
- [ ] **GPS-loss → STOP:** block/remove the remote → camera **stops and holds** (must NOT coast toward a stale bearing).
- [ ] **E-Stop** interrupts GPS mode instantly.

## 6. Vision-refine handoff (after C3 fix)
- [ ] With the orange subject resolvable in-frame, confirm **vision takes over** from GPS (fine framing) and hands **back** to GPS when vision loses lock. *(Blocked until DeepSeek's C3 owner-release fix — see below.)*

## ⚠️ Backend to-do BEFORE deploy (DeepSeek's lane — he went idle ~02:26; precise fixes are on the collab bus)
1. **Calibration endpoint wiring — REQUIRED for steps 4–5.** Until this lands, `pose.calibrated` stays false and **GPS cannot point at all.** In `control_api` `/calibration/heading`: on capture, call `pipeline.pose.calibrate_pan_aim(pan_enc=<encoder already read>, bearing_deg=<heading_deg from iOS = GPS base→remote bearing>, enc_per_deg=4.47)` → `CameraPose.save()` to `camera_pose.json` → **load it on pipeline startup** into `pipeline.pose`. Add base-lock (`lock_base_position` → pose lat/lon) + expose `calibrated: bool` in `GET /calibration`. (`camera_pose.py` already has `calibrate_pan_aim`/`lock_base_position`/`save`/`load`; 21 tests pass.)
2. **C3 owner-release fix — REQUIRED for step 6 (handoff); steps 4–5 work without it.** In the pipeline arbiter branches, release the outgoing autonomous owner before claiming the new one (`ptz_owner.request` refuses cross-owner steals) + gate the GPS send on owning. Otherwise once GPS claims from idle, vision can't take back (camera freezes at the GPS point).

## Deferred (P2 / fine for a first test)
- **VISCA absolute on-rig verify:** `pan_tilt_absolute`/`zoom_absolute` need a send→read-back check on the live camera (bridge `pan_enc_per_deg≈4.47`) — pair with DeepSeek in a camera window.
- **Base drift-revalidation:** base-lock is presence-gated, not drift-rechecked (bumped tripod mid-session). P2 hardening.
- **C2 force-GPS override:** deferred (E-Stop is the bailout for a wrong-subject lock).

## Branches
- `feature/gps-p1-ios` — Claude: GPS HUD chip (`af4b2a0`) + aim-at-remote calibration (`46253b1`).
- `backend/gps-control-loop-p1` — DeepSeek: arbiter + VISCA absolute + pipeline + the review fixes + auto-reconnect (`e9fa676`), C3 owner-release fix pending.
- Integrate both → one P1 PR after C3 lands + cross-review. Do **not** push to `main` without Zack.
