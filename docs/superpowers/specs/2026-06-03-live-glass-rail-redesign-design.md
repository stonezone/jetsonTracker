# WaveCam Live Screen — Glass Rail Redesign (2026-06-03)

**Goal:** Declutter the merged Live screen and modernize it with Liquid Glass. The current landscape layout crams five boxed controls into a 190pt opaque, *scrolling* sidebar that steals width from the feed. Replace it with one cohesive glass rail and a full-bleed feed. Presentation-only — all command logic (`PTZManualController`) is reused unchanged.

## Approved decisions (brainstormed with Zack)
- **Joystick:** floats over the feed **bottom-left** (semi-transparent).
- **Zoom:** a **vertical slider** (replaces the ±-button card).
- **Controls:** one cohesive **Liquid Glass rail** on the **right edge** (landscape) / a horizontal **glass dock at the bottom** (portrait).
- **HUD:** minimal — keep the targeting reticles + a small glass **lock chip** (lock state + the plain-English "why not locked" hint). Remove the verbose pan/tilt/zoom text overlay and fps.
- **AUTO toggle:** one control replaces *Start Auto* + *Stop PTZ* (on = autonomous tracking, off = idle; joystick release still stops manual moves).
- **Errors:** a brief glass **toast** over the feed (auto-dismiss), not a permanent pill.
- **PTZ tab:** removed (done, build 8). The dead `PTZView` screen + its now-exclusive components are deleted in this redesign.
- **Refresh button:** removed (1Hz poll covers it).

## Layout

### Landscape (`verticalSizeClass == .compact`)
- Feed full-bleed, edge to edge (behind the rail).
- **Glass rail**, right edge, ~62pt wide, full height (inset for safe area), top → bottom:
  `⤢ Fullscreen` · divider · **zoom vertical slider** · divider · `◎ AUTO` · `● REC` · `⌂ HOME` · …spacer… · **`■ STOP`** (red glass, pinned bottom).
- **Joystick** floats bottom-left over the feed (tap/long-press center = home, feature-detected).
- **Lock chip** glass, top-left.

### Portrait
- Feed fills the upper area; the rail becomes a **horizontal glass dock** pinned to the bottom with the same controls (zoom slider becomes horizontal). Joystick floats bottom-left over the feed. STOP stays in the dock, distinct + red.

### Fullscreen (`⤢`)
- Hides the rail/dock **and** the system tab bar → pure feed + the floating joystick + a persistent **STOP** + a small exit affordance. The root TopBar KILL chip remains regardless.

## Zoom slider behavior
Backend zoom is **velocity-based** (`client.zoom(value)`, mode `velocity`). The slider is a **spring-to-center velocity control**: center = stop; displacement up = tele, down = wide, with speed proportional to displacement; releasing snaps back to center (stop). This gives proportional, cinematic zoom vs. fixed-speed buttons. Reuse `PTZManualController.updateZoom`/`stopZoomCommand`.

## Components
- **New:**
  - `LiveControlRail` — the cohesive glass panel (adapts rail↔dock by size class).
  - `GlassControlButton` — icon (+ optional active/danger state) button used in the rail.
  - `GlassZoomSlider` — the spring-to-center vertical/horizontal velocity slider.
  - `FeedToast` — transient glass error/refusal overlay bound to `lastControlError`/`controller.refusalText`.
  - `GlassLockChip` — minimal lock-state + lock-reason chip (wraps the existing `FeedLockReason` logic).
- **Reused unchanged:** `MJPEGPreviewView`, `FeedReticles`, `FeedAimReticle`, `JoystickPad` (parameterized: left position, `onHome`, `semiTransparent`), `RecordButton`, `EmergencyStopButton`, `PTZManualController` (all command/timer logic), the `WaveCamClient` API.
- **Removed:** `PTZActionRow`, `PTZZoomCard`, `PTZControlFeedback`/`PTZFeedbackPill`, `FeedPTZOverlay`, `FeedTopTags` (fps), and the dead `PTZView` screen + its exclusive helpers (`PTZHeader`, `PTZStatusPill`, `PTZJoystickCard`, `PTZReadoutCell`). Verify each is unreferenced before deleting.

## Liquid Glass + fallback
- App deploys to **iOS 17**; the SDK is iOS 26. Use `if #available(iOS 26, *)` → `.glassEffect(...)` / `GlassEffectContainer`; **else** a `.ultraThinMaterial` dark fallback with a hairline white stroke. One wrapper view (`GlassSurface`) encapsulates the availability split so call sites stay clean.

## Safety invariants (unchanged)
- Emergency Stop / KILL always reachable: the rail/dock STOP **and** the root TopBar chip; `KillLatchOverlay` still covers everything on `effectiveKilled`.
- Manual-PTZ command path (velocity repeat, release-stop retry, deadman) untouched — it lives in `PTZManualController`.
- Home gesture stays feature-detected (`supported.ptzHome`); inert with a hint if absent.

## Non-goals
- No change to the backend, the command protocol, or `PTZManualController` logic.
- No change to the other tabs (Calibrate/Tools/Connect/Media).
- Not adding new PTZ features — purely a layout/visual redesign + consolidation cleanup.

## Verification
- Build (`xcodebuild` generic/iOS) green; install + **force-relaunch** on device.
- On-device check (Zack): landscape + portrait, joystick drives + releases, zoom slider zooms + stops, AUTO toggles tracking, REC, HOME (or hint), STOP always reachable, lock chip truthful, error toast appears on a refused command. Liquid Glass renders on iOS 26; material fallback otherwise.
