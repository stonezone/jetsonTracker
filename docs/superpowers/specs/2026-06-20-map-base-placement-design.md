# Map-based base placement + heading (iOS / Apple MapKit) — design

**Status:** draft for review · **Date:** 2026-06-20 · **Lane:** iOS (Claude) · backend already supports it

## Context

The WaveCam base/camera location comes from the base Wio GPS, which is ±5–15 m noisy. Field evidence (2026-06-20): the phone GPS and the base Wio, sitting ~1 m apart, reported positions 15 m apart. A bad base position biases **every** GPS bearing (a 15 m base offset is ~4° at 200 m, and was ~28° at 10 m). Vision refines once the subject is in frame, but a wrong base degrades the GPS hand-off the whole system depends on.

If the operator can place the base at its **true** spot by eyeballing the tripod against satellite landmarks, the camera reference becomes effectively sub-meter **regardless of GPS** — removing that constant bias at all ranges. The backend already supports this: `lock_location` accepts manual `lat`/`lon`/`manual_error_radius_m` (bypasses live-base averaging), and `heading_lock` accepts `target_lat`/`target_lon` **or** a direct `bearing_deg`. So this is an iOS MapKit UI feeding existing endpoints — no new backend math.

## Goals / non-goals

- **Goal:** operator sets the base **location** by dragging a pin on Apple satellite/hybrid imagery, and sets **heading** by map reference, both bypassing GPS noise.
- **Goal:** support **both** heading interactions — look-at pin (default/recommended) and a rotatable heading arrow.
- **Non-goal:** changing backend calibration math; replacing the tracker/subject GPS (still GPS); RTK; Google imagery (Apple MapKit hybrid only).

## Decisions (from brainstorming)

- **Imagery:** Apple MapKit `.hybrid` (satellite + labels), iOS 17 SwiftUI `Map`. No API key, no SDK, caches tiles.
- **Heading:** ship both; **look-at pin is the default** because it reuses the backend `target_lat`/`target_lon` path and needs no compass/azimuth guessing.
- **Placement:** map placement is an **alternative path inside the existing CALIBRATE wizard** Location and Heading steps (not a replacement), because GPS base is unreliable and the operator should be able to choose map placement first.

## Approach

A new SwiftUI `MapPlacementView` (MapKit `.hybrid`) presented from the wizard's Location and Heading steps. It runs **inside an active CALIBRATE session** (owner=`calibrate`), so the same PTZ lockout/KILL invariants hold.

Flow:
1. **Location:** Location step shows a "Place on map" button beside the existing "Lock location (GPS)". Tapping opens `MapPlacementView` in *base-pin* mode: a draggable base annotation centered on the current base GPS fix as a starting guess. Operator drags it onto the real tripod spot, confirms → `calibrateLocationManual(lat, lon, errorRadiusM)` → `POST /calibration/location` with `{lat, lon, use_live_base:false, manual_error_radius_m, method:"map_manual"}`. Default `manual_error_radius_m ≈ 3 m` (eyeballed placement is tighter than the 15 m GPS model).
2. **Heading:** Heading step shows "Set heading on map" beside the GPS/landmark flow. `MapPlacementView` in *heading* mode, two sub-modes:
   - **Look-at pin (default):** operator physically aims the camera at an identifiable point (dock edge, driveway, rock), then drops a look-at annotation on that exact point. App reads the **current pan encoder** from `/status` at drop time and POSTs heading-lock `{target_lat, target_lon, pan_enc, operator_accepted}` (backend computes bearing base→look-at). Reuses the existing target-coords path.
   - **Rotate arrow (alt):** operator rotates an arrow overlay to the camera's forward azimuth; app sets `bearing_deg` directly with the current pan encoder. Eyeballed; offered as a fallback.

## Components

- **`ios/WaveCam/Sources/MapPlacementView.swift`** (new): MapKit `.hybrid` map; `@State` for the base pin coordinate, look-at coordinate, heading-arrow angle, mode (`.base` / `.headingLookAt` / `.headingArrow`); crosshair + live lat/lon readout; "Use this location" / "Set heading" actions; a "tiles not loaded" warning when offline. One view, one purpose; <~350 lines.
- **`WaveCamClient.swift`:** add `calibrateLocationManual(lat:lon:errorRadiusM:source:)` (POSTs manual coords, `use_live_base:false`); extend the heading methods to accept optional `targetLat`/`targetLon` + `panEnc` (currently send only `bearing_deg`). Reuse existing `WCCalibration*` models.
- **`CalibrateView.swift`:** add "Place on map" / "Set heading on map" buttons to `LocationCard` and `HeadingCard`; present `MapPlacementView` as a sheet. No new `WizardStep` (keeps the state machine intact) — map placement is an alternate capture within the existing steps.
- **Backend:** no change required. Optional: accept `method:"map_manual"` as a label (purely informational in the stored entry).

## Data flow

`CALIBRATE start (owner=calibrate)` → Location step → "Place on map" → drag base pin → confirm → `POST /calibration/location {lat,lon,use_live_base:false,manual_error_radius_m≈3}` → Heading step → "Set heading on map" → (look-at) aim camera + drop pin → read live `pan_enc` → `POST /calibration/heading-lock {target_lat,target_lon,pan_enc,operator_accepted:true}` → Validation → Confirm → exit. Everything writes the same session/pose as the GPS path.

## Error handling / edge cases

- **Offline tiles:** MapKit needs network to fetch tiles the first time. Mitigation: set the base on shore where imagery has loaded; show a non-blocking "map imagery not loaded — connect to load satellite tiles" banner; never hard-fail.
- **Pin precision:** zoom to max; show a fixed center crosshair + live lat/lon (6 dp); the operator places the *map center* on the spot rather than dragging a tiny pin (crosshair model is more precise than a draggable marker).
- **Session guard:** all writes require an active CALIBRATE session (owner=`calibrate`); if not active, the buttons are disabled with a hint to start CALIBRATE.
- **Look-at correctness:** the look-at heading is only valid if the camera is **physically aimed** at the look-at point when the pin is dropped — read `pan_enc` at drop time and show the live camera bearing so the operator can confirm alignment; warn if the camera is moving.
- **KILL / unreachable:** standard — KILL cancels CALIBRATE; map writes use `getWithFallback`/the standard client error handling.

## Testing

- **iOS unit:** bearing/distance math (app-side base→look-at bearing must match the backend `bearing_deg` geo function for the same coords); `MapPlacementView` state transitions; client request shaping (manual location body; heading target-coords body).
- **Backend:** manual-location and `target_lat`/`target_lon` heading paths already have tests; add a regression only if the `method:"map_manual"` label or a tighter default radius is wired in.
- **End-to-end (on device + live rig):** start CALIBRATE → place base on the map at a surveyed spot → confirm `/calibration` shows the manual location (±3 m) → set heading via look-at → confirm reference_heading is sane → compare GPS pointing accuracy vs the GPS-locked base. Verify offline-banner behavior with the network off.

## Review-driven requirements (blind peer review 2026-06-20: Opus + GLM/z.ai; Gemini failed)

These are **must-implement guards**, converged on by 2 independent peers + self-review. The heading-via-map path is the fragile part; location placement is the clean win.

1. **pan_enc capture (V1, high):** do not trust a client-*polled* encoder for look-at heading. Capture `pan_enc` backend-side at the moment the heading-lock request arrives, or do an atomic client read + hard-block if the camera is moving (PTZ owner=calibrate should be stationary). Show the live camera bearing so the operator sees alignment.
2. **Minimum look-at distance (V2, high):** require a **distant** look-at landmark (≥50 m), same logic as GPS heading — placement error ÷ distance is the heading error. Warn/refuse a near look-at.
3. **Aim via the video feed (V3, high):** the operator aims using the **live video optical center** (not body orientation), confirms the optical center is on the landmark, *then* drops the map pin. Draw a line from the base pin along the camera's current bearing (from `/status`) on the map to link the two views.
4. **Honest error radius (V4, med):** scale `manual_error_radius_m` with map zoom / imagery quality; do not hardcode 3 m. Coarse/old tiles → larger radius.
5. **Hard-block on unloaded tiles (V5, med):** disable "confirm" until satellite tiles are loaded for the visible region; a non-blocking banner is insufficient.
6. **Arrow mode frame (V6, med):** force **North-Up** while rotating the heading arrow; define its coordinate system explicitly. (The arrow is operator-rotated on the map — not device-compass-derived — so no declination handling needed.)
7. **Re-check session at POST (V7, med):** verify CALIBRATE is still active immediately before each location/heading POST; fail clean if it dropped.
8. **Location-before-heading sequencing (V8, med):** confirm the manual location POST committed before a map heading reads the locked base, so heading isn't computed against a stale base.
9. **Bearing parity (test gate):** the app-side base→look-at bearing must match the backend `bearing_deg` geo function — keep as a unit test.
10. **Optional UX:** a fine-tune nudge control (meters) for the pin; an undo for a confirmed location.

## Risks / open

- Heading-arrow model accuracy depends on the operator eyeballing the camera's forward azimuth — hence look-at is the default; the arrow is a labeled fallback.
- Apple imagery recency/resolution varies by location — acceptable for landmark placement; revisit only if the actual filming spot has poor imagery.
- Map-center-crosshair vs draggable-pin UX is an implementation detail to validate on device.
