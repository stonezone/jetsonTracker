# GPS Field-Test Checklist

Goal: validate **GPS coarse-pointing** (camera pans/zooms to the remote via GPS), then the **vision-refine handoff**, on the live direct-LoRa stack.

## 0. Safety invariants (verify first)
- GPS moves the camera **only** when: `ptz.enabled` **AND** `gps.enabled` **AND** calibrated **AND** base-locked **AND** GPS fresh. Uncalibrated by default → GPS can't move the camera until you deliberately calibrate.
- **Emergency Stop / KILL** must be reachable on the Live screen at all times. Test it interrupts GPS mode before trusting anything else.
- The supervise-only agent rule is unchanged — only the tracking pipeline aims the camera.

## 1. Hardware pre-flight
- [ ] **Boot order:** Orin cold-boots with the base Wio **unplugged** (U-Boot stalls otherwise) → after boot, plug the base Wio into USB-A → `sudo systemctl restart wavecam.service`.
- [ ] **Base Wio battery check:** the base Wio now has a battery installed. Let it acquire its fix on battery power with clear sky, then connect USB data to the Orin. If it is powered from the Orin USB rail during acquisition, host RF noise can still drive the L76K to 0 sats.
- [ ] **Tracker Wio** (`02d5` in config): charged, antenna attached, LED showing fast-flash → short-blink as it gets a fix.
- [ ] **Serial healthy:** `ssh orin` → `journalctl -u wavecam.service | grep -i "DirectRadioGps"` shows `connected` and periodic `{"seq":...}` packets.

## 2. Deploy / readiness
- [ ] Confirm `/version` shows the expected commit and `/status` returns 200 with fps ≥ 30.
- [ ] Confirm `gps.source` in status is `direct_lora` once packets arrive.
- [ ] iOS app installed; Calibrate and Tune tabs reachable.

## 3. Get GPS fixes (open sky)
- [ ] Base + tracker both under **open sky** (a doorway is too obstructed). 30–60 s.
- [ ] The **GPS chip** on the Live HUD should show `GPS <dist>m·<brg>°` (green). If it shows `GPS·NO FIX`, the **base** hasn't locked its 3D fix — give it more open sky or switch to battery power.

## 4. Calibrate (aim-at-remote)
- [ ] **Calibrate tab → Base lock** — captures the camera reference position once `base_stable` is true.
- [ ] **Heading step** ("aim at remote"): place the tracker where you can see it, **center the camera on it** (manual joystick), confirm the GPS chip shows a live distance+bearing, then **Capture heading**. (The app sends the GPS base→remote bearing; the backend reads the pan encoder → `calibrate_pan_aim`. The capture is **blocked** if there's no GPS bearing — no bogus 0° calibration.)
- [ ] Verify `GET /api/v1/calibration` shows `calibrated: true` (persists to `camera_pose.json`, survives restart).

## 5. Enable + test GPS pointing
- [ ] Tune → confirm `gps.enabled` + `ptz.enabled`. E-Stop within reach. Set `tracking.mode: gps_only` if you want to force GPS-only behavior for this test.
- [ ] **Pointing accuracy:** does the camera pan to the tracker's bearing + land on/near it? (Conservative GPS speeds: pan 4, tilt 3.)
- [ ] **Coarse track:** walk the tracker → camera follows (cadence depends on beacon interval, 1–5 Hz).
- [ ] **GPS-loss → STOP:** power off the tracker or block its antenna → camera **stops and holds** (must NOT coast toward a stale bearing).
- [ ] **E-Stop** interrupts GPS mode instantly.

## 6. Vision-refine handoff
- [ ] With the orange subject resolvable in-frame, confirm **vision takes over** from GPS (fine framing) and hands **back** to GPS when vision loses lock. Set `tracking.mode: auto` for normal arbitration.

## Common blockers
- **No base fix:** base Wio on USB rail or USB-powered → ensure it is running on its installed battery for acquisition; no sky view → relocate.
- **No tracker packets:** tracker off/dead battery; mismatched radio firmware between base and tracker; antenna missing.
- **Calibration blocked:** no live GPS distance+bearing; base not stable.
- **Vision won't take over:** `tracking.mode` set to `gps_only`; unlock/lock thresholds inverted; subject too small/far.
