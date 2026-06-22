# Calibration v3 — single-screen redesign (no modal sheets, no blockers)

**Created:** 2026-06-22
**Status:** Approved (Zack, in-field, "spec implement install") — building now; review the result next session.
**Supersedes the UX of:** the modal-sheet wizard (`CalibrateView` + `MapPlacementView`/`OffsetCalibrateView` sheets). Keeps the backend calibration endpoints; replaces the iOS calibrate screen.

**Goal:** One coherent Calibrate screen that never hides Exit/KILL, never hard-blocks, lets the operator aim the camera *in place*, shows both trackers live on a map, and makes the height model explicit. Plus a Settings panel to edit the pose after calibration and reload.

## Why (root causes of the v2 disaster)
- Map + offset were **modal sheets** that covered Exit + KILL → operator got stuck "owned by calibrate," couldn't exit or see anything.
- **No PTZ control inside calibrate** + CALIBRATE owns the camera → "it tells me to aim but won't let me."
- Heading/validation **hard gates** blocked reaching VALID (now advisory).
- Height buried in the modal + mislabeled "above sea level" → wrong values, 63° down-dive.

## The screen (single scrollable view; Exit + KILL pinned top, always visible)

**Persistent header:** banner (INVALID / VALID / COARSE), **Exit Calibrate**, **KILL** — never covered.

**Live map panel (always shown):** Apple `.hybrid`. Base pin + tracker pin from live GPS, each with a stat line (lat/lon, sats, fix age, and base→tracker distance + bearing). This is the "always show both trackers + where they are vs the base" the operator asked for. Reused in the steps and the Settings panel.

**Step 1 — Location + Heading**
- Drag the base pin to the real spot, or type lat/lon. Map shows base + tracker live.
- Heading: **slider** OR **manual numeric entry** (operator reads it off a compass/nav). No aiming here.
- Advisory only — commits whatever the operator sets.

**Step 2 — Heights** (the explicit model)
- **Datum picker: `Relative to base` | `Above sea level`.**
- `Relative to base`: base = 0 (implicit); **one field** = tracker/subject height vs base (e.g. −1).
- `Above sea level`: **two fields** = base_asl + tracker_asl (e.g. 3 and 0.5).
- Live readout: "camera looks ≈X° down at 100 m" from the resulting Δh.

**Step 3 — Aim & offset**
- **Embedded joystick** (compact, only in this step to avoid clutter) that claims manual from CALIBRATE so the operator aims the camera at the tracker 50+ m out **without leaving the screen**. STOP-hold to freeze the aim.
- **Capture** → backend compares the aim (live pan/tilt encoders) to the tracker's live GPS, sets the pan+tilt offset → **VALID** → tracking.
- Advisory: shows the resulting offset + miss; never blocks.

**Calibration Settings panel** (same screen, collapsible)
- Manually edit base height / tracker height (+ datum), heading, and location.
- **Apply & reload** → posts the pose + re-reads `/status` so the map + stats refresh.
- Always shows both trackers' live stats + position relative to the base.

## Backend changes (small)
- **`CameraPose.subject_alt_m`** (new field, replaces the hardcoded 1 m in `pipeline.py:550`). Live tilt = `atan2(subject_alt_m − alt_m, dist)`.
- The location/height endpoint accepts `alt_m` (base) + `subject_alt_m` (subject) in the chosen datum:
  - `relative_to_base`: `alt_m = 0`, `subject_alt_m = tracker_rel`.
  - `sea_level`: `alt_m = base_asl`, `subject_alt_m = tracker_asl`.
- Persisted in the pose JSON (survives restart; `subject_alt_m` defaults to 1.0 for back-compat).
- No new hard gates. All existing calibration gates remain advisory (already done: heading uncertainty, validation independence/miss).

## iOS components (SRP — replace the 1,130-line CalibrateView with focused units)
- `CalibrateView` — the single screen: pinned header + scroll of the panels below.
- `CalibrationMapPanel` — live base/tracker pins + stat lines (reused by steps + settings).
- `CalibrationLocationHeadingStep`, `CalibrationHeightsStep`, `CalibrationAimOffsetStep` — the three step cards.
- `CalibrationSettingsPanel` — manual pose edit + Apply & reload.
- Reuse the existing PTZ joystick component for the embedded aim control; reuse `EmergencyStopButton` in the header.
- `MapPlacementView`/`OffsetCalibrateView` modal sheets are retired (logic folded into the inline steps).

## Principles
- **Never cover Exit/KILL.** No modal sheets for calibration.
- **Never hard-block.** Every check is a yellow advisory line.
- **Aim in place.** The joystick lives in the aim step.
- **Heights are explicit + datum-labeled.** No "sea level" ambiguity.
- Portrait + landscape (tripod mount); feature-detect endpoints against `/config`.

## Testing
- Backend: `subject_alt_m` plumbed + persisted; live tilt uses it (TDD); both datum mappings reduce to `atan2(subject−base,dist)`.
- iOS unit: heights datum → (alt_m, subject_alt_m) mapping; map panel stat formatting; no-blocker advisory states.
- On-device (Zack, next session): full run — location+heading → heights → aim+offset → tracking; Settings edit + reload; Exit/KILL always reachable.

## Out (later)
- Saved-spots recall UI (still deferred).
