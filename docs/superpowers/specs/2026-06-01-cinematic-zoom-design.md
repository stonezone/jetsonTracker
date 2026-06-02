# Cinematic Zoom — Design Spec (2026-06-01)

Status: **implemented in repo**. Default off; live only when
`ptz.cinematic_zoom_enabled=true`.

## Goal
Optional vision-based auto-zoom that holds the tracked subject at a chosen size in
frame ("Subject size"), so the operator isn't manually riding the zoom while filming.
Default **OFF**; opt-in **"Cinematic Zoom"** toggle in the iOS Tune panel.

## Scope
**In:** YOLO person-box-driven zoom-to-frame on the Orin, gated by a hot flag; iOS Tune
controls (toggle + Subject-size slider).
**Out (explicit non-goals):**
- Far-range coarse zoom — the **GPS phase** owns point + coarse-zoom at distance; cinematic
  zoom only *refines* once the subject is resolvable in-frame. (Resolves the YOLO/color
  "can't see it at 300 m" chicken-and-egg: GPS brings it into frame first.)
- Color-blob-at-distance zoom — rejected (noisy size reference, hunts); GPS covers distance.
- Building `yolo26n.engine` — separate maintenance-window TensorRT export task.

## Behavior
- **Enabled + locked on a YOLO person box:** drive tele/wide to bring the person-box height
  toward `zoom_target_frac` of frame height; `zoom_deadband` stops it at target; speed capped
  by `zoom_max_speed`. Uses `controller.compute_zoom`.
- **Color-only / no-person frame:** HOLD zoom. Pan/tilt centering continues; no hunting off
  the color blob.
- **Default OFF** (`ptz.cinematic_zoom_enabled = false`).

## Backend (Codex) — incorporates his review
1. **Person-bbox source (critical):** do NOT pass `FusionResult.bbox` to `compute_zoom` — it can
   be a color-blob bbox when no person is selected. Add `person_bbox` (or a person-source marker)
   to `FusionResult`; feed only the selected YOLO person bbox. No person → `compute_zoom` gets
   `None` → `("stop", 0)` = hold.
2. **Gate:** run only when `cinematic_zoom_enabled` AND owner is autonomous (testbed/vision)
   AND not suppressed by the manual-zoom override.
3. **Zoom command path:** `ptz.zoom(tele|wide|stop, speed)` with a **separate** rate-limit/de-dupe
   (`_last_zoom_key`/`_last_zoom_time`) — do not reuse pan/tilt `_last_cmd_key`.
4. **Manual override:** a separate **zoom-override deadman** timestamp — a manual
   `POST /ptz/zoom` suppresses ONLY cinematic zoom for the deadman window; pan/tilt auto-tracking
   continues; cinematic zoom resumes after. Do not suppress pan/tilt tracking for a manual zoom nudge.
5. **Config (hot):** `ptz.cinematic_zoom_enabled` (bool, default false) + `ptz.zoom_target_frac`
   (float 0.2–0.8, default 0.5); surface `ptz.zoom_deadband`, `ptz.zoom_max_speed`. Add to the
   hot-config allow-list + `config_snapshot.current.ptz` (+ supported/defaults). Internal aliases
   (`target_frac`/`zoom_deadband`/`zoom_max`) may stay; API names are explicit.
6. **Tests:** person-source gating, color-only hold, no-person stop/hold, manual-override
   suppression, zoom rate-limit/de-dupe, hot-config snapshot round-trip.

## iOS (Claude)
- Tune panel: add a **"Cinematic Zoom"** toggle (`ptz.cinematic_zoom_enabled`) and a
  **"Subject size"** slider (`ptz.zoom_target_frac`, 0.2–0.8). Both **feature-detected** via
  `GET /config` — only shown/active when the backend exposes the keys. Hot-applied via
  `config/hot` like the other Tune controls. anti-vibe; portrait + landscape.

## Data flow
operator toggles Cinematic Zoom (iOS) → `config/hot {ptz.cinematic_zoom_enabled:true}` →
pipeline loop: if enabled + locked person box + not manual-suppressed → `compute_zoom(person_bbox, frame_h)`
→ `ptz.zoom(...)` (separate rate-limit). Manual `ptz/zoom` → sets zoom-override deadman →
cinematic suppressed → resumes after the window.

## Split
- **Backend:** FusionResult.person_bbox, loop wiring, gate, zoom rate-limit,
  manual-override deadman, config keys + snapshot, tests.
- **iOS:** Tune controls (toggle + slider, feature-detected).

## Resolved (Zack, 2026-06-01) — APPROVED
Manual override: **keep pan/tilt auto-tracking running.** Implement the **separate zoom-override
deadman** — a manual `ptz/zoom` suppresses ONLY cinematic zoom for the deadman window; pan/tilt
centering continues uninterrupted; cinematic zoom resumes after. (NOT the full owner-suppression.)
Spec approved and implemented in repo.
