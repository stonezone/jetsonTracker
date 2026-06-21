# Calibration v2 — Map Place + Refine — Design

**Created:** 2026-06-21
**Status:** Draft (review-revised) — awaiting Zack review before writing-plans
**Supersedes/extends:** `2026-06-20-map-base-placement-design.md` (the shipped map-placement feature is the seam this builds on)
**Review:** 5-lens adversarial review run 2026-06-21 (29 findings confirmed against live code, folded in below; 10 false-positives dropped).

**Goal:** One reliable operator-driven calibration that locks the camera's geographic location, height, and heading by hand on satellite imagery, then refines pan+tilt against a single physical aim at the tracker — bypassing GPS noise as the *source of truth* while still using GPS where it's trustworthy. "Reliable" is honest: the full flow (through the tracker aim) is the calibrated path; skipping the aim is an explicitly-labeled **coarse mode**.

**Architecture:** Extend the existing `MapPlacementView` / `MapKitContainer` / `MapPlacementModel` trio. Add the offset-calibrate step as its **own view + its own observable model** (`OffsetCalibrateView` + `OffsetCalibrateModel`) so neither the 1,114-line `CalibrateView` nor `MapPlacementModel` absorbs a new concern. Backend changes are surgical and run inside the existing CALIBRATE session/owner lockout.

**Tech Stack:** SwiftUI (iOS 17, xcodegen), MapKit `.hybrid`; FastAPI `orin/wavecam` `/api/v1/calibration/*`; `CameraPose` (plain-JSON pose), `gps_geo`/`gps_pointing` pure math.

---

## Global Constraints

- Vision loop ≥30 FPS; CALIBRATE owns the PTZ (owner=`calibrate` lockout); **KILL is human-only, supreme, cancels CALIBRATE**.
- Pan/tilt scale fixed **14.4 counts/deg** both axes; tilt encoder zero = horizontal.
- Portrait + landscape parity (tripod-bracket mount) — **including every new screen**.
- Feature-detect every config-driven control against `GET /config`; degrade gracefully if the offset endpoint is absent (fall back to step-3 heading).
- Deploy only via `deploy.sh`; backend changes pass `pytest` + mypy gate.
- **Altitude constant:** target and subject are a fixed **1 m** above sea level, applied **identically in calibration and live pointing**. Base altitude is the only operator-entered height; it persists per saved spot.

---

## Background — the verified mechanism

Live pointing builds the subject target with no explicit altitude today (`pipeline.py:550`), so `GeoPoint.alt_m` defaults to **0** (`gps_geo.py:22`). The base altitude comes from the noisy GPS base-lock average (`control_calibration.py:882` → `lock_base_position`), and consumer GPS vertical is the worst axis (the attached GPX reads **+13.5 m while on the water**). Tilt depression is `atan2(subject_alt − base_alt, dist)` (`gps_geo.py:57`), so a bogus +13 m base altitude drove the camera steeply **down** at short range (`atan2(−13, 9) ≈ −55°` — yesterday's "it's pointing down").

Pan maps as `pan_anchor_enc + normalize_180(bearing − pan_anchor_bearing)·14.4` (`camera_pose.py:118`); a single aim fully calibrates it via `calibrate_pan_aim(enc, bearing, 14.4)` (`camera_pose.py:69`) because the scale is known. **Tilt maps as `tilt_anchor_enc + (elev − tilt_anchor_elev)·14.4` ONLY when `tilt_enc_per_deg ≠ 0`; otherwise `elevation_to_tilt_encoder` returns a FIXED `tilt_anchor_enc` and never tracks elevation** (`camera_pose.py:123-126`). This is the M1/C2 trap — the existing `step=='tilt'` handler sets all three fields (`control_calibration.py:888-890`) and the offset handler must too.

**This design therefore:**
- Sets the operator-entered base height (replacing noisy GPS altitude) and pins subject = target = **1 m** in *both* the live path and calibration (a 1-line change at `pipeline.py:550` to build the subject GeoPoint with `alt_m=1.0`), giving one elevation formula and true iOS↔backend parity.
- Re-anchors pan via `calibrate_pan_aim` AND tilt via all three fields (`tilt_anchor_enc`, `tilt_anchor_elev`, `tilt_enc_per_deg=14.4`) from the single aim.
- Keeps the GPS base-lock OUT of the v2 wizard so it cannot clobber the manual altitude.

---

## The flow

### Start screen (every time)
- **Use saved spot** → recall a named spot, **show its stored pin + base height for a one-tap confirm/edit** (FU-9 — never blind-trust a height that may have drifted), then silently POST the location (`map_manual`, lat/lon/alt_m, `use_live_base:false`) against a fresh CALIBRATE session and advance the UI to Heading **with the stored `last_heading_deg` pre-filled as a starting guess** (operator confirms/refines — never auto-locked). (C7 — recall skips operator *interaction*, not the backend write.)
- **New full calibration** → the sequence below.

### 1. Location
Drop the base pin on `.hybrid` satellite imagery **or** type lat/lon (decimal degrees, e.g. `21.680843, -158.036440`). The locked base renders as a pin for landmark verification. Written via `method:"map_manual"`, `use_live_base:false`. **The v2 wizard never invokes the GPS base-lock step** (CLOBBER-1).

### 2. Base height
One numeric field: **base height above sea level (m)**, default 2 m, free for dune/balcony. Sent as `alt_m` on the location body; persisted per saved spot. As the operator types, **show the predicted depression** ("camera will look ≈X° down at 100 m") so an implausible height is caught immediately (FU-8). Target+subject stay a hidden constant 1 m.

### 3. Heading (provisional)
Primary: **type the bearing** (phone compass / nav device). Alt: **two-finger twist** on the north-up map rotates a heading arrow. This calls `calibration/heading-lock` (`operator_accepted:true`, `bearing_deg`), which **does anchor pan** at the current encoder→typed bearing (C1 — it is *not* passive). It also stores the typed bearing so step 4 can display the offset. If step 4 runs, it **overwrites** this pan anchor; if step 4 is skipped, this is the calibration (coarse mode — see Decisions).

### 4. Offset calibrate (authoritative refine) — two phases (FU-1) — OPTIONAL (coarse mode if skipped)
**Skipping step 4 is allowed** but yields a state explicitly labeled **"coarse (heading uncalibrated)"** with a visible warning and a stated expected accuracy (heading only as good as the typed compass read, ±~5–15°). The full 4a/4b aim is the calibrated path.
**4a — Place tracker:** operator walks the tracker 50–100 m out. The screen shows **live tracker fix quality (sats, HDOP, fix age) AND LoRa packet freshness**, plus the live base→tracker **distance + bearing**, so the operator knows (a) the link is up, (b) they reached 50–100 m, before walking back. Distinguish *no fix* / *no packets from remote* / *stale* — the recovery differs (FU-2, FU-4).
**4b — Aim & capture:** operator returns to base, frames the tracker dead-center (manual PTZ), taps capture. Capture is sampled **at button-press** (not cached), and the tracker position is **averaged over a 5–10 s window** to suppress jitter (FU-2). Backend then:
- `B = base→tracker GPS bearing`; `d = base→tracker GPS distance`.
- `elev_cal = atan2(1 − base_h, d)`.
- pan: `calibrate_pan_aim(captured_pan_enc, B, 14.4)` (overwrites step 3).
- tilt: `tilt_anchor_enc = captured_tilt_enc`, `tilt_anchor_elev = elev_cal`, **`tilt_enc_per_deg = 14.4`** (M1/C2).

The screen shows the **offset** = `normalize_180(B − step-3 bearing)` with a sign convention and an interpretation band (FU-7): *small* = compass was good, *moderate* = expected, *large* = warn (tracker too close, mis-aim, or wrong base height). A **geometry sanity check** ties in (C9, replacing the ill-defined "divergence" check): if `|elev_cal| > 30°` at `d > 50 m`, warn that **base height is likely wrong** — and cross-validate against the entered base height (FU-8). Base + tracker render on the map for visual confirmation.

Session/lockout TTL must **exceed the walk time** (auto-extend while in step 4) with a visible countdown; keep the screen awake; re-validate session at capture press and recover cleanly from KILL/session-drop ("session expired during the walk — restart") (FU-1, FU-10).

### 5. Lock + save
Validate/confirm (existing session-scoped gate). Optionally name the spot → store `(name, lat, lon, base_height_m, last_heading_deg)` locally on iOS (UserDefaults). `last_heading_deg` is saved as a **starting guess only**: on recall it pre-fills the Heading step, which the operator always confirms/refines (never a blind heading lock).

---

## Components

### iOS (Claude's lane)
- **`MapPlacementModel.swift`** (extend): add `baseHeightM` (default 2) + manual lat/lon entry + validation + `manualHeadingDeg`. Stays scoped to **placement + heading** (ARCH-02).
- **`OffsetCalibrateModel.swift`** (new): its own `@Observable` — B/d/elev, fix-quality + LoRa-freshness gating, averaging window, offset readout + interpretation band, geometry sanity check. (Does **not** become a `Mode.offset` branch in MapPlacementModel.)
- **`OffsetCalibrateView.swift`** (new): the 4a/4b screen; dual-pin map (base + live tracker), capture, offset/warn readout. Portrait+landscape (FU-6).
- **`MapPlacementView.swift`** (extend): manual-coord + base-height fields with live depression hint; manual-bearing primary, twist alt.
- **`SavedSpotsStore.swift`** (new, small): Codable `[SavedSpot]` (`name, lat, lon, base_height_m, last_heading_deg`) in UserDefaults; list/add/recall/confirm-edit. `last_heading_deg` is a starting guess only. iOS-local only.
- **`CalibrateView.swift`** (modify minimally): start-screen [Use saved spot]/[New] router only — no step bodies inlined (ARCH-01 confirms this split).
- **`WaveCamClient.swift`** (extend): `mapLocationBody` gains `alt_m` inside the existing `nonisolated static func`; add a `nonisolated static func` body builder + call for offset-calibrate; feature-detect the endpoint and degrade to step-3 (PATTERN-01/04). Any new decoded response field uses the shared `.convertFromSnakeCase` decoder with **no explicit CodingKeys** and `decodeIfPresent ?? default`; custom `init(from:)` only in an extension (PATTERN-03).

### Backend (Claude primary — surgical, test-gated)
- **`control_calibration.py`**: net-new **offset-calibrate handler** modeled on `heading_lock` — `_require_active()` guard, capture pan+tilt from `_current_encoder()`, compute `B/d/elev_cal` **under `self._lock`**, `calibrate_pan_aim` + set all three tilt fields, `_persist_step`; **no new owner** (runs as the CALIBRATE owner already holding the PTZ) (OFFSET-1). Returns the offset for display. Ensure the **v2 location path is always `map_manual`/`use_live_base:false`**; add a defensive guard so a stray base-lock/live-base lock does not overwrite an operator `alt_m` within the session (CLOBBER-1, C4). This may require a persisted `alt_manual: bool` on `CameraPose` — i.e. **the spec's earlier "no schema change" is wrong** (C8 was dropped as FP, but C4 stands); decide guard-vs-flag in the plan.
- **`gps_pointing.py`**: add optional `max_up_elev_deg` to `compute_target` (default from gps config, ~+5°): clamp `elev = min(elev, max_up_elev_deg)` **before** `elevation_to_tilt_encoder`, log when engaged (CLAMP-1, M4, C6). Confirm the elev sign convention before fixing the threshold sign.
- **`pipeline.py:550`**: build the subject GeoPoint with `alt_m=1.0` so live elevation matches the calibration constant (M2).
- **`config`**: add `pointing.max_tilt_up_deg` (default +5°), surfaced in `/config` for iOS feature-detect (C6).

---

## Math (authoritative reference)

```
elev(dist)        = atan2(1 - base_h, dist)          # target=subject=1 m, SAME in live + calibration
pan_enc(bearing)  = pan_anchor_enc + normalize_180(bearing - pan_anchor_bearing) * 14.4
tilt_enc(elev)    = tilt_anchor_enc + (elev - tilt_anchor_elev) * 14.4     # requires tilt_enc_per_deg = 14.4

offset calibrate (one aim at tracker, position averaged 5-10 s):
  B        = bearing(base_gps -> tracker_gps)
  d        = haversine(base_gps, tracker_gps)
  elev_cal = atan2(1 - base_h, d)
  pan:  calibrate_pan_aim(captured_pan_enc, B, 14.4)
  tilt: tilt_anchor_enc = captured_tilt_enc; tilt_anchor_elev = elev_cal; tilt_enc_per_deg = 14.4
  display_offset = normalize_180(B - step3_bearing)         # only when step 3 ran
  sanity:  |elev_cal| > 30 deg at d > 50 m  =>  warn "base height likely wrong"

live tilt clamp: elev = min(elev, pointing.max_tilt_up_deg)  # default +5 deg, logged when engaged
```

## Error handling
- Map tiles not loaded → block confirm (existing guard); offline coords still typeable.
- Step 4: gate capture on fix **quality** (sats/HDOP/age) + LoRa freshness, not mere presence; distinct messages for no-fix / no-packets / stale; in-place "waiting / retry" so the walk isn't restarted (FU-2, FU-4).
- CALIBRATE session dropped / KILL mid-walk → persistent session-alive indicator, re-validate at capture, clear "restart calibration" recovery (FU-10).
- Manual coord/bearing parse errors → inline validation, no submit.
- Tilt clamp engaged → log (no silent truncation).

## Testing
- iOS unit (`WaveCamTests`): `alt_m` plumbed into location body; manual-coord + manual-bearing validation; `offset = normalize_180(B − step3)`; saved-spot round-trip + confirm/edit; `GeoMath.elevation` uses the **same 1 m constant** the backend uses (parity).
- Backend (`pytest` + mypy): manual `alt_m` stored and **not** clobbered by a subsequent base-lock/live-base within a session; offset handler re-anchors pan + sets all three tilt fields and returns the offset; live subject `alt_m=1.0`; tilt-up clamp (absurd-high base alt still tilts down unaffected; contrived up-elev clamps); `elev` parity with iOS.
- On-device + live-rig (T-final): full 4a/4b flow at the yard/beach, both orientations; confirm tilt no longer dives at close range with base height set.

## Scope
**In:** start-screen choice; map-drop + manual-coord location; base-height field + live depression hint; manual + twist heading; **optional** two-phase single-aim offset re-anchor (pan + all-three-field tilt) with quality/LoRa gating, averaging, offset+sanity readout, dual-pin verify, and a labeled **coarse mode** when skipped; iOS-local saved spots (incl. `last_heading_deg` starting guess) with confirm/edit; backend offset handler + alt_m no-clobber + tilt-up clamp + live subject 1 m.

**Out (own specs later):** wave-aware tracking; Claude-reviews-wave-screenshots auto-tuning; watch triggers beyond record start/stop; landmark-aim heading; backend-persisted/shared saved spots; zoom-framing tuning. (SCOPE-01: hold this line; do not let "name the spot" grow into shared/backend spots.)

## Resolved decisions (Zack, 2026-06-21)
1. **Save heading as a starting guess** — `last_heading_deg` is stored per spot and pre-fills the Heading step on recall; the operator always confirms/refines it. Never a blind heading lock.
2. **Step 4 is optional, labeled "coarse mode"** — skipping the tracker aim is allowed but produces a clearly-labeled coarse state with a warning + stated expected accuracy. The full aim is the calibrated path.

## Open risks
- **PM-2 (deferred, low):** the live up-tilt clamp is asymmetric — a grossly-too-HIGH operator base altitude (e.g. a 200 m typo on a beach) dives the camera at the ground with no floor to catch it. Down-tilt is intentionally unclamped (legit for a balcony/dune). Mitigations already in scope: the Phase-3 iOS base-height field shows the predicted depression as you type, and the Phase-2 offset step warns when `|elev_cal|>30°`. A symmetric down-floor or a base-alt plausibility bound (e.g. −5…50 m) at `_commit_location` is a candidate follow-up if those prove insufficient in the field.
- `alt_manual` flag vs guard for the no-clobber requirement — RESOLVED in Phase 1: runtime flag on `CameraPose` (not persisted); cleared only by a later non-manual `/location` commit or a restart.
- Twist gesture vs map-pan (map rotation already disabled, so two-finger rotation is free) — verify on-device.
- Saved-spots iOS-local lost on reinstall — acceptable for v2.
