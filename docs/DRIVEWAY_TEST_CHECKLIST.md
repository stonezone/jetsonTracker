# WaveCam Driveway Test — close-range shakedown (before the water test)

**Backend under test:** `a90209d` (Plan v3 Phases 0–4 — deployed + verified).
**Why a driveway test first:** prove *control + vision + safety + observability* at a
controlled close range before the 50–300 m water test. **GPS pointing accuracy is NOT
meaningful here** — at driveway range the GPS bearing error is large (≈27° at 10 m;
heading needs a stationary target ≥50 m). So keep **GPS bearing-cue and GPS-zoom OFF**
and validate vision, CALIBRATE, KILL, manual, and base-drift.

## Diagnostic read
Everything below is observable in `GET /api/v1/status`. From a laptop:

```
ssh orin 'curl -s localhost:8088/api/v1/status'
```

Watch the **`authority`**, **`tracking`**, and **`gps`** sections. The iOS app shows
owner / state / locked / conf; the Phase 0–4 fields (`base_drift_state`, `track_id`,
`gps_cue`, and the GPS gate inputs) live in the API.

## Checklist

1. **Pre-flight** — rig outside; `wavecam.service` active; `tracking.fps ≥ 30`; camera
   video up; **KILL reachable in the iOS app**.
2. **GPS fix** — base Wio on **battery** (off the Orin USB rail) outside until it has
   sats; remote Wio outside too. Expect: `gps.target_sats` climbing,
   `authority.base_locked: true`, `authority.base_drift_state: locked`, and once the
   remote transmits, `authority.gps_fresh: true`.
3. **CALIBRATE** (iOS Calibrate tab) — location lock (averaged base fix) → level →
   heading. *Driveway caveat:* a heading aimed at anything < 50 m will be coarse — that
   is expected; the goal is the flow plus `authority.calibration_valid` flipping **true**
   only after validate → confirm.
4. **Vision lock** — stand in the orange rashguard ~10–30 m out. Expect
   `tracking.locked: true`, `tracking.state: TRACKING`, `authority.owner: vision_follow`,
   camera follows. Walk laterally → it tracks.
5. **KILL** — Emergency Stop → camera stops immediately; `safety.killed: true`;
   `authority.owner: idle`; manual + auto both blocked. Release → it re-searches. (Note
   whether auto-resume after release is the behavior you want — the re-arm decision.)
6. **Manual** — joystick nudge → `authority.owner: manual`; stop → auto-releases after
   the deadman (~0.8 s) back to search/auto.
7. **Base-drift bump test** — with base locked, physically nudge/drag the tripod a few
   metres. Expect `authority.base_drift_state` go `locked → suspect → unlocked` and GPS
   authority withheld. Re-CALIBRATE to clear. (Validates Phase 1 at a range you can
   physically exercise.)

## Pass criteria
Vision tracks + holds; KILL stops + latches; manual releases cleanly; CALIBRATE reaches
`calibration_valid: true`; base-drift trips on a real bump but not on GPS jitter. Capture
the `authority` JSON at each step as the record carried into the water test.

## What is intentionally OFF for this test
`detector.tracker` (tracker IDs), `fusion.gps_bearing_cue_enabled`, `gps.drive_zoom` —
all default-off; base-drift is on but fail-safe (only *confirmed* drift withholds GPS).
Enable individually only after the basics pass.
