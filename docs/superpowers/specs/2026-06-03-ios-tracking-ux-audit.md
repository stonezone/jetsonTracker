# iOS Tracking-Settings UX Audit + Clarity Fixes (2026-06-03)

**Trigger:** Zack reported the tracking settings "don't make sense / work reliably" — require-person, RESUME/START AUTO/STOP PTZ, and "only red-orange works." Lenses: frontend-design + fullstack-guardian + anti-vibe.

## Root cause of "only red-orange works"
Not a bug. The **color preset is the color the tracker chases**; Zack wears **orange**. Selecting "blue" makes it hunt for blue → no match on Zack → no color lock → no target → START AUTO has nothing to follow → the camera sits still and everything *looks* broken. All six presets are valid HSV ranges (`orin/wavecam/wavecam/color_presets.py`). `Validated`. The real gap is **UI clarity + missing "why isn't it locked" feedback.**

## Fixed this pass (committed, built + installed to phone)
- **TuneView**: friendly preset names ("Orange / red (rashguard)" …) + captions — color preset ("match the subject's color … other presets won't lock onto you") and require-person ("Off: track the color even when YOLO can't make out a person — best at distance").
- **LiveView**: new `FeedLockReason` HUD line under the top tags — built from real `tracking` fields, says **"No target — does Color preset match the subject?" / "Color seen · no YOLO person" / "STOPPED · Resume to track" / "Searching…"**. Silent when locked/offline or when the backend omits the component fields.

## Verified
- **`f21c8b9` failover split is correct**: `getWithFallback` (GET) fails over on `.timedOut` + connection errors (`isReadRouteFailoverAllowed`); `post` (POST) only on connection errors (excludes `.timedOut`). Fixes the iOS "PTZ dead while video works" symptom; commands stay double-send-safe.
- **Zoom-on-joystick-release** now stops: `releaseManualPTZ → resetZoomCommand(sendStop:false)` → sets `zoomCommand=0` → `.onChange` → `updateZoom(0)` (sends stop + cancels the repeat); backend zoom-deadman (`ef67215`) backstops it.

## Open findings (recommended, not yet implemented)
- **F1 (HIGH UX) — silent PTZ refusals.** `startAutoPTZ`/`holdPTZ` set `commandState = .idle` when the backend refuses (`accepted == false`) with **no feedback** (`PTZControlFeedback` has no refused case). So a refused Start Auto (e.g., while KILL is latched) silently does nothing. **Fix:** add a `.refused` path + a pill — esp. "Resume first — Emergency Stop is latched" when `client.killed`, else "PTZ busy / no owner." (`PTZView.swift:156-168, 617-633`.)
- **F2 (MED UX) — AUTO confirmed but not moving.** With no target (wrong color / no lock), START AUTO is accepted (`.auto`, "Backend confirms Auto PTZ") but the camera doesn't move → looks broken. Now mitigated by the Live `FeedLockReason`; optionally add a compact lock/target hint to the PTZ screen too.
- **F3 (LOW) — no KILL/RESUME on the iOS PTZ screen.** iOS uses the global `EmergencyStopButton` (KILL) + full-screen `KillLatchOverlay` (hold-to-RESUME), unlike the `:8088` web page's 4-in-a-row. Functionally fine (KILL always reachable), but a different mental model than the web page Zack was using.

## Backend / web parity (Codex, after his usage resets Jun 7)
The `:8088` web console has the same cryptic labels (the screenshot Zack used). Apply the same clarity there (color preset = subject color; require-person help; a no-target reason).
