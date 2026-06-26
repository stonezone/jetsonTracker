# WaveCam Yard Test — pre-water shakedown

**Backend under test:** `main` (Plan v3 Phases 0–4; live detector = **yolo11n**).
**Why a yard test first:** prove control + vision + safety **and the GPS bootstrap** on dry
land before the 50–300 m (**~165–985 ft**) water test.

**Your range — measure with the rangefinder and update these:**
- Garage door → gate = **164 ft (50 m)**
- Garage door → mom's house = **~328 ft (100 m)** ← confirm exact reading
- Close apron at the garage = **under ~100 ft**

The yard reaches real range, so this runs in **two tiers**: **A) close range** (vision +
safety at the garage, GPS OFF — bearing is junk under ~100 ft, ≈27° at 33 ft) and **B) GPS
bootstrap** at the gate / mom's house, where GPS bearing is finally meaningful (~4° at
164 ft, ~2° at 328 ft).

## Diagnostic read
`ssh orin 'curl -s localhost:8088/api/v1/status'` — watch the **`authority`**,
**`tracking`**, and **`gps`** sections. The iOS app shows owner / state / locked / conf. (The
app has **5 tabs — Live / Calibrate / Tools / Connect / Media**; the old separate PTZ tab is
now merged into **Live**, and joystick/aim controls live there.)

## TIER A — close range at the garage (< ~100 ft): vision + control + safety
Keep **`tracking.mode: vision_only`** and **`gps.drive_zoom` OFF** (iOS Tune). Under ~100 ft
GPS would mis-point badly — this tier is purely vision + safety.

1. **Pre-flight** — `wavecam.service` active; `tracking.fps ≥ 30`; camera video up;
   **KILL reachable in the iOS app**.
2. **Vision lock** — stand in the orange rashguard **~33–98 ft (10–30 m)** out. Expect
   `tracking.locked: true`, `tracking.state: TRACKING`, `authority.owner: vision_follow`;
   walk laterally → it tracks.
3. **KILL** — Emergency Stop → camera stops immediately; `safety.killed: true`;
   `authority.owner: idle`; manual + auto both blocked. Release → it re-searches. (Note
   whether auto-resume after release is the behavior you want — the re-arm decision.)
4. **Manual** — joystick nudge → `authority.owner: manual`; release → auto-releases after
   the deadman (~0.8 s) back to search/auto.

## TIER B — GPS bootstrap at range (gate 164 ft / mom's house 328 ft)
Now GPS bearing is meaningful. Run this tier with the target at **≥164 ft**.

5. **GPS fix** — both Wios run the custom **direct-LoRa** firmware (`firmware/direct-lora/`, not
   Meshtastic). Base Wio on **battery** (off the Orin USB rail) outside until it has sats;
   remote Wio outside too. Expect `gps.target_sats` climbing, `authority.base_locked: true`,
   `authority.base_drift_state: locked`, and `authority.gps_fresh: true` once the remote
   transmits.
6. **CALIBRATE** (iOS Calibrate tab) — location lock (averaged base fix) → level →
   **heading aimed at mom's house (328 ft)** — the best target you have. (The gate at 164 ft
   works but sits right at the ≥50 m minimum; farther = more accurate heading.) validate →
   confirm → `authority.calibration_valid: true`. **`calibration_valid` is session-scoped — a
   `wavecam.service` restart clears it, so re-CALIBRATE after any restart and minimize restarts
   mid-test.**
7. **Stationary GPS point + zoom** — put the **remote Wio at mom's house (328 ft)**. Set
   `tracking.mode: auto` (or `gps_only`), turn **`gps.drive_zoom` ON** and
   **`fusion.gps_bearing_cue_enabled` ON** (both are hot keys in iOS Tune — no restart).
   Expect `authority.owner: gps_tracker`, the camera slews to that bearing, and drive_zoom
   zooms in. **Verify it actually points at mom's house** (eyeball / rangefinder). This is
   the core bootstrap test that's impossible at close range.
8. **Walking GPS track** — carry the remote Wio from the gate (164 ft) out toward mom's
   (328 ft). Pointing should lead/track; once you're resolvable and in orange, vision takes
   over (`authority.owner: vision_follow`).
9. **Base-drift bump** — with base locked, nudge/drag the tripod a few feet. Expect
   `authority.base_drift_state: locked → suspect → unlocked` and GPS authority withheld.
   Re-CALIBRATE to clear. (If pointing lands short/long, sanity-check the slew against the
   **measured pan/tilt scale of 14.4 counts/deg** — a wrong scale, not drift, is the usual
   culprit for a consistent angular miss.)

## Pass criteria
**Tier A:** vision tracks + holds; KILL stops + latches; manual releases cleanly.
**Tier B:** `calibration_valid: true` on a ≥164 ft heading; the camera points at the real
GPS target within a frame or two; drive_zoom zooms with distance; base-drift trips on a real
bump but not on GPS jitter. Capture the `authority` JSON at each step as the record carried
into the water test.

## What's intentionally OFF / staged
- **Tier A:** GPS bootstrap OFF (`vision_only`, `drive_zoom` off) — GPS is junk under ~100 ft.
- `detector.tracker` (tracker IDs) off by default.
- **Acting-agent ARM toggle (iOS Connect tab) stays OFF for the yard test.** The agent ships
  inside `wavecam.service` but is supervise-only until ARMed; leave it disarmed so it never moves
  the camera unattended. KILL is human-only and disarms it.
- `gps.drive_zoom` and `fusion.gps_bearing_cue_enabled` are **both hot keys** (toggle live in
  iOS Tune, no restart) — ON only for Tier B and the water test, OFF for Tier A.
- **Heading quality is the upstream dominator for all GPS pointing.** A sloppy/short heading
  (e.g. a 33 ft landmark ≈ 27° error) makes the camera mis-point by multiple frame-widths —
  always calibrate heading on a target **≥164 ft**.
