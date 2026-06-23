# GPS Field-Test Checklist

Goal: validate **GPS coarse-pointing** (camera pans/zooms to the remote via GPS), then the **vision-refine handoff**, on the live direct-LoRa stack.

## 0. Safety invariants (verify first)
- GPS moves the camera **only** when: `ptz.enabled` **AND** `gps.enabled` **AND** calibrated **AND** base-locked **AND** GPS fresh. Uncalibrated by default → GPS can't move the camera until you deliberately calibrate.
- **Emergency Stop / KILL** must be reachable on the Live screen at all times. Test it interrupts GPS mode before trusting anything else.
- The agent is **supervise-only by default** — it can advise but not move the camera. An **ARM toggle** (default OFF) lets the operator enable the acting-agent for autonomous adjustments (TTL 600 s). **KILL is human-only and supreme.** Only the tracking pipeline aims the camera when the agent is disarmed or acting within its armed scope.

## 1. Hardware pre-flight
- [ ] **Boot order:** Orin cold-boots with the base Wio **unplugged** (U-Boot stalls otherwise) → after boot, plug the base Wio into USB-A → `sudo systemctl restart wavecam.service`.
- [ ] **Base Wio battery / acquisition order:** follow the boot order above — (1) cold-boot the Orin with the base Wio **unplugged**, (2) plug the base Wio into USB-A while it **runs on its installed battery** during fix acquisition (clear sky), then (3) `sudo systemctl restart wavecam.service`. The battery exists to let it acquire independently: powering the L76K from the Orin USB rail during acquisition lets host RF noise drive it to 0 sats.
- [ ] **Tracker Wio** (`02d5` in config): charged, antenna attached, LED showing fast-flash → short-blink as it gets a fix.
- [ ] **Serial healthy:** `ssh orin` → `journalctl -u wavecam.service | grep -i "DirectRadioGps"` shows `connected` and periodic `{"seq":...}` packets.

## 2. Deploy / readiness
- [ ] Confirm `/version` shows the expected commit and `/status` returns 200 with fps ≥ 30.
- [ ] Confirm `gps.source` in status is `direct_lora` once packets arrive.
- [ ] iOS app installed; the 5 tabs reachable — **Live / Calibrate / Tools / Connect / Media** (the old PTZ tab is merged into **Live**). Live tuning now lives on the web page at `http://<orin>:8088` (control API `/api/v1`), which also hosts the **ASK CLAUDE** agent chat + the agent **ARM** toggle.

## 3. Get GPS fixes (open sky)
- [ ] Base + tracker both under **open sky** (a doorway is too obstructed). 30–60 s.
- [ ] The **GPS chip** on the Live HUD should show `GPS <dist>m·<brg>°` (green). If it shows `GPS·NO FIX`, the **base** hasn't locked its 3D fix — give it more open sky or switch to battery power.

## 4. Calibrate (v3 single-screen flow)
The Calibrate tab is one screen that runs: **location + height** (pick a datum — base-relative or sea-level) → **heading** → **aim** (on the Live feed + zoom) → **Capture / multi-point Refine** → **Validate** → **Confirm**.
- [ ] **Location + height** — set the camera reference position (captured once `base_stable` is true) and the height datum.
- [ ] **Heading** ("aim at remote"): place the tracker where you can see it, **center the camera on it** (manual aim), confirm the GPS chip shows a live distance+bearing, then **Capture heading**. (The app sends the GPS base→remote bearing; the backend reads the pan encoder → `calibrate_pan_aim`. The capture is **blocked** if there's no GPS bearing — no bogus 0° calibration. The phone magnetometer is unusable near the motor, so heading is operator-set.) Pan/tilt scale = **14.4 counts/deg** (measured) — calibration error scales directly off this.
- [ ] **Refine / Validate / Confirm** — optionally Refine with multiple aim points (least-squares offset), then Validate and Confirm.
- [ ] Verify `GET /api/v1/calibration` shows `calibrated: true` (persists to `camera_pose.json`, survives restart). Note: `calibration_valid` is **session-scoped** — a `wavecam.service` restart **resets** it, so re-Validate/Confirm after any restart.

## 5. Enable + test GPS pointing
- [ ] On the web tuning page (`http://<orin>:8088`) confirm `gps.enabled` + `ptz.enabled` (or check `GET /api/v1/config`). E-Stop within reach. Set `tracking.mode: gps_only` if you want to force GPS-only behavior for this test.
- [ ] **Pointing accuracy:** does the camera pan to the tracker's bearing + land on/near it? (Conservative GPS speeds: pan 4, tilt 3.)
- [ ] **Coarse track:** walk the tracker → camera follows (cadence ≈ the direct-LoRa beacon rate, ~2 Hz).
- [ ] **GPS-loss → STOP:** power off the tracker or block its antenna → camera **stops and holds** (must NOT coast toward a stale bearing).
- [ ] **E-Stop** interrupts GPS mode instantly.

## 6. Vision-refine handoff
- [ ] With the orange subject resolvable in-frame, confirm **vision takes over** from GPS (fine framing) and hands **back** to GPS when vision loses lock. Set `tracking.mode: auto` for normal arbitration. (Detector = **YOLO11n** TensorRT, swapped from yolov8n on 2026-06-15.)

## Common blockers
- **No base fix:** base Wio on USB rail or USB-powered → ensure it is running on its installed battery for acquisition; no sky view → relocate.
- **No tracker packets:** tracker off/dead battery; mismatched radio firmware between base and tracker; antenna missing.
- **Calibration blocked:** no live GPS distance+bearing; base not stable.
- **Vision won't take over:** `tracking.mode` set to `gps_only`; unlock/lock thresholds inverted; subject too small/far.
