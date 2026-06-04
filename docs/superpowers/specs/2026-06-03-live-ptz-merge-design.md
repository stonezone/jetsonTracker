# Live + PTZ Merge — Operator Screen Design (2026-06-03)

**Why:** Review H5 — the PTZ tab has no live video, so manual framing is blind. Zack wants Live+PTZ on one screen. Filming alone offshore, you must see the feed while you nudge the camera.

## Zack's requirements (verbatim intent)
1. PTZ **joystick over the feed**, bottom corner, **transparent** but otherwise identical to the current joystick.
2. **Tap / double-tap / long-hold the joystick center to HOME the camera.**
3. A **`[ ]` fullscreen** toggle for the video feed that **keeps the PTZ controls overlaid**.
4. (Project rule) Emergency Stop pinned/visible at all times; portrait + landscape parity.

## Component reuse (already clean in the codebase)
- Feed: `MJPEGPreviewView: UIViewRepresentable` (`LiveView.swift:119`) — takes `url`.
- Joystick: `JoystickPad` + `JoystickNub` + `JoystickLabels` (`PTZView.swift:373-489`).
- Control logic: `sendVelocity` / `releaseManualPTZ` / `holdPTZ` / `startAutoPTZ` + the `PTZCommandState` machine (`PTZView.swift:118-270`) — already aligned with the 557adf7 stop-hardening. Reuse, don't reinvent.
- KILL: root `effectiveKilled` overlay already covers any screen.

## Layout
- **Portrait:** feed 16:9 up top; transparent `JoystickPad` overlaid bottom-right of the feed; zoom + Start-Auto/Record/Stop strip below.
- **Landscape (`verticalSizeClass == .compact`):** full-bleed feed; joystick overlay bottom-corner; Emergency Stop pinned top-right; minimal HUD.
- **Fullscreen `[ ]`:** feed edge-to-edge; joystick + Emergency Stop + Record overlaid; exit affordance top-corner. Reuse the same overlay subviews so there's one source of truth.

## BLOCKER — home command does not exist (route to Codex)
`WaveCamClient` exposes only `ptzVelocity`/`ptzStop`/`ptzStartAuto`; `control_api.py` has **no** home/recenter/position route (the `preset` matches are *color* presets). Requirement #2 cannot ship until the backend adds it.

- **Backend (Codex):** `POST /api/v1/ptz/home` → VISCA home (pan/tilt to the pan-home reference). Must be **owner-gated + KILL-respecting + deadman-safe**, exactly like the other manual PTZ writes hardened in 557adf7. Advertise it in `GET /config` `supported` (e.g. `supported.ptz_home = true`) so iOS can feature-detect.
- **iOS (Claude):** add `client.ptzHome()`; wire the center gesture to it; **feature-detect** — if `supported.ptz_home` is absent, the center gesture shows a subtle "home unavailable" hint and no-ops (never a dead button).

## Build order
1. Codex adds `/ptz/home` + `supported.ptz_home` (small, fits current backend work).
2. Claude builds `MergedLiveView` (feed + transparent overlay joystick + fullscreen + Emergency Stop + Start-Auto/Record), reusing the components above; home gesture feature-detected.
3. xcodegen regen (new file) → build → install → verify on-device portrait + landscape.
4. Decide with Zack whether to drop the standalone PTZ tab or keep it as a fallback.

## Buildable without the backend (fallback if home slips)
Everything except #2: feed + transparent overlay joystick + fullscreen + pinned Emergency Stop + Start-Auto/Record. The center-home gesture is wired but inert until `supported.ptz_home` appears. This still delivers H5 (video while controlling) + the joystick-over-feed + fullscreen.

## Status
Spec'd 2026-06-03 during the autonomous overnight run. Not yet built — gated on the home endpoint (Codex tasked on the bus) to ship requirement #2 whole rather than as a dead gesture.
