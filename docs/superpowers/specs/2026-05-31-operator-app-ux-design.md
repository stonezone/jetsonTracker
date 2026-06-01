# Operator App / UX + System Roadmap — overnight brainstorm (2026-05-31)

Context: autonomous overnight session (Zack asleep, authorized execution). Watch app installed + streaming (low rate ~0.08 Hz). Dashboard MVP live on Orin :8080. This captures Zack's late-night idea dump + the gaps I brainstormed, with answers and a prioritized roadmap. Risky items are planned, not executed unsupervised.

## Zack's idea dump (verbatim intent)
1. Brainstorm the app/UX update — what's missing / what are we overlooking?
2. How does the phone interact with the Orin — does heading matter?
3. Calibration step right after power-up.
4. Use YOLO to fine-tune the GPS↔camera-angle mapping.
5. Zoom at distance based on GPS data (camera has 20x optical).
6. Move OS/boot + storage onto the 512 GB NVMe; keep a **removable microSD** for easy video sharing (swap the card). Don't rely on the microSD to boot. "Can be on both."

## Answers to the design questions

**Does heading matter?**
- **Camera heading: YES.** `reference_heading` (which way pan=0 faces) is what converts a world GPS bearing into a camera pan command. Established once by **landmark calibration** (aim at a known-GPS point, read pan, back-solve). This is the only heading that matters.
- **Phone/Watch compass heading: NO** (for pointing). The phone's heading is irrelevant to base-lock (base = a position). The Watch's `course` matters only for motion prediction/lead, not pointing.

**Phone ↔ Orin interaction.** The iPhone plays three roles: (a) **base GPS** (position near the camera, locked once at setup); (b) the **Orin's internet uplink** — prefer **USB-C↔USB-C tether** (more reliable than a WiFi hotspot for the Cloudflare GPS relay + livestream); (c) the **operator UI host** (Dashboard tab = `WKWebView` of the Orin dashboard). The Orin stays the authority for camera/tracking/recording/streaming.

**Calibration after power-up** = a guided wizard (not a settings page): Preflight → Base-lock → Heading → Tilt → (optional) YOLO walk-around → Zoom/FOV → Dry-run. Persist `camera_pose.json`; warn + offer re-cal if the base drifts (tripod bumped).

**YOLO fine-tune** = YOLO-assisted calibration: subject (Watch on) walks a shallow arc; collect paired samples (GPS bearing/elev, camera pan/tilt/zoom, YOLO box center); least-squares fit `heading_offset`, `tilt_offset`, pan/tilt scale bias. Refinement on top of landmark cal, gated on good GPS accuracy + confident single-subject YOLO.

**GPS-distance zoom** = map distance → optical zoom so the subject holds a target apparent size. Pieces exist (`estimate_target_size_pixels`, `PointingController.zoom_enabled`). Needs a **zoom→FOV calibration curve** (zoom encoder ↔ magnification) to be accurate; stay conservative until calibrated. 20x optical → subject resolvable to ~300 m.

## Gaps / overlooked (the "what are we missing")
1. **Watch fix RATE (~0.08 Hz) — #1 tracking blocker.** A foiler at 7 m/s moves ~85 m between fixes; prediction can't bridge 12 s gaps. Need ~1 Hz. (Codex/watch lane: is the workout location update interval too coarse, or are fixes throttled/dropped?)
2. **Power budget for the beach** — Orin (~7–15 W) + camera + phone, multi-hour, on battery. Size a USB-C PD bank; the dashboard should show battery/power if available.
3. **Recorder + livestream not built** — the footage is the deliverable. ffmpeg `-c copy` RTSP `/1` → NVMe; livestream remux → RTMP (no NVENC).
4. **Sun/glare + sensor safety** — pointing at water with sun causes wash-out and risks the sensor. Add a **no-slew-into-sun keep-out** (compute sun azimuth from time+location) and sane exposure/Image settings.
5. **Network reliability** — Orin internet (tether) is a hard dependency for the GPS relay + stream; surface its health + auto-reconnect; consider a local (no-Cloudflare) GPS path over the USB-C link.
6. **Base-drift detection** — if the tripod moves after base-lock, pointing degrades silently; detect + warn.
7. **GPS dropout behavior** — hold last framing, then widen zoom + slow search; already in the design, needs wiring.
8. **Weatherproofing / mounting** — salt, sand, wind, tripod stability (physical, for Zack).
9. **Onboarding / first-run** — the dashboard should guide the whole arrive→calibrate→track→stop→grab-footage flow.

## Operator App / UX design

**Surfaces.** (a) **Orin web dashboard** (built MVP; the authority). (b) **iOS app** = existing GPS-relay `Track` tab + new `Dashboard` tab (`WKWebView` → Orin) + `Settings`. The iOS app does not duplicate Orin logic — it calls the dashboard API. This is Zack's "all-in-one app."

**End-to-end flow (the UX spine):** Power up → open app → **Preflight** (all checks green or a clear fault banner) → **Calibrate** (guided wizard) → **Start Tracking** → **Monitor** (live `/2` preview + status + one-tap manual PTZ override) → **Stop** → **Footage** (segments + share/eject card). Everything reachable without SSH.

**Dashboard panels** (per `DASHBOARD_SPEC.md`): Session header (state + big actions), Health, GPS, Camera preview, PTZ control, Tracking tune, Media, Network, Logs. MVP done = Health/GPS/PTZ-readback/Logs. Overnight target = + PTZ controls, + preview, + calibration wizard.

## Storage architecture (NVMe boot) — PLAN ONLY, needs Zack awake
Current: rootfs/boot on microSD; `/data` on the 512 GB NVMe (405 GB free) — **recordings already land on NVMe**. Zack wants: OS+boot off the microSD (onto NVMe, reliable) + a **removable microSD for video sharing** (swap-and-go). Target: boot from NVMe; record to NVMe (reliable) **and** mirror/copy finished segments to the removable microSD for easy sharing ("on both").
**Why not tonight:** Jetson Orin NVMe-boot migration (clone rootfs, rewrite `extlinux.conf`/boot order, possibly flash via host in recovery mode) can leave a **non-booting Orin** if botched — unrecoverable without Zack + a host PC. **Procedure to do together when awake**, with the microSD as a known-good fallback. I'll pre-stage a documented, reversible runbook.

## Prioritized roadmap

**Tonight (autonomous, safe — me, Orin/Python):**
1. Dashboard **PTZ control** endpoints + buttons (nudge/velocity/zoom/stop/home/presets) — operator can adjust the camera from the phone.
2. Dashboard **`/2` live preview** (MJPEG/periodic-JPEG) — Zack's "live video feed."
3. **Calibration wizard** backend + UI (preflight + base-lock + heading) → writes `camera_pose.json`.
4. **P4 GPS+YOLO fusion** in `run_tracker` (size-gate) + distance→zoom (conservative).
5. **Recorder** service (ffmpeg `-c copy` `/1` → NVMe segments) + dashboard Media controls.
6. Capture verification artifacts (YOLO-annotated frames, dashboard screenshots) for Zack's AM review; bounce with Codex on the bus.

**Codex (watch/iOS lane):** Watch fix **RATE → ~1 Hz** (top blocker), iPhone/base fresh emission, Watch direct-vs-relay reconnection, iOS `Dashboard` tab (`WKWebView`).

**Deferred (needs Zack):** NVMe boot migration; livestream platform/keys + USB-C-tether networking; weatherproofing/mounting; power bank sizing.

## Notes
- Do NOT change `gps_server` acceptance rules (Codex owns the base-fix on iOS).
- Camera moves stay small + self-restoring during tests.
- Prune the bus log if it grows large; keep `.agent-collab` out of git.
