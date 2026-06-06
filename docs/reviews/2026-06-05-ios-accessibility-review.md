# iOS Accessibility Review — WaveCam Build 24
**Date:** 2026-06-05  
**Reviewer:** Claude (senior code review)  
**Scope:** ios/WaveCam/Sources/*.swift — all 17 files  
**Focus screens:** MergedLiveView, MediaView, TuneView, ContentView, EmergencyStopButton, CalibrateView, ConnectionView, AgentView, JoystickPad, Theme+Glass (shared components)

---

## Summary

| Severity | Count |
|----------|-------|
| Critical | 4 |
| High | 6 |
| Medium | 7 |
| Low | 5 |
| **Total** | **22** |

**Top 5 findings:**
1. `HoldToResumeButton` is not VoiceOver-activatable (long-press gesture, no accessibility action) — the safety resume path is broken for blind users.
2. `JoystickPad` is entirely opaque to VoiceOver — PTZ motion has no accessible alternative.
3. `GlassZoomSlider` exposes no accessibility value or increment/decrement actions.
4. All color-only state signifiers (lock/record/safety dots, service health rows, tracking-state chip colors) lack non-color cues — colorblind users cannot distinguish LOCKED from SEARCH from STOPPED.
5. Virtually every font token in `WCFont` and inline `Font.system(size:)` calls uses fixed point sizes — Dynamic Type is unsupported app-wide.

---

## Critical

### C-1: `HoldToResumeButton` — long-press resume not VoiceOver-operable
**File:** `ios/WaveCam/Sources/ContentView.swift:205`

The `KillLatchOverlay` shows a hold-gesture button to resume from Emergency Stop. The implementation uses `onLongPressGesture(minimumDuration:)`. VoiceOver does not synthesize long-press gestures from the Activate action; it fires a tap. The button will never fire for a blind operator while the kill latch is active. `.accessibilityLabel` and `.accessibilityHint` are set, and `.accessibilityAddTraits(.isButton)` is present, but there is no `.accessibilityAction` that actually calls `action()`.

**Fix:** Add a custom accessibility action that triggers the resume:
```swift
.accessibilityAction(named: "Resume") { action() }
```
Alternatively wrap in a `Button` that uses `onLongPressGesture` plus a fallback tap-after-confirm for VoiceOver.

---

### C-2: `JoystickPad` — no accessible PTZ control
**File:** `ios/WaveCam/Sources/JoystickPad.swift:8`

`JoystickPad` is a continuous drag-gesture control with no accessibility representation. VoiceOver cannot move the camera at all from the Live screen. For an operator using assistive tech this is a complete loss of PTZ authority. `JoystickNub` has `.accessibilityLabel("Joystick nub")` but no increment/decrement actions for pan or tilt.

**Fix (minimum viable):** Add stepwise accessibility actions on `JoystickPad` or expose a separate `AccessibilityRepresentation` with named actions:
```swift
.accessibilityElement(children: .ignore)
.accessibilityLabel("PTZ joystick")
.accessibilityHint("Pan and tilt the camera")
.accessibilityAction(named: "Pan left")  { onCommand(-0.5, 0); DispatchQueue.main.asyncAfter(deadline: .now()+0.5) { onStop() } }
.accessibilityAction(named: "Pan right") { onCommand( 0.5, 0); DispatchQueue.main.asyncAfter(deadline: .now()+0.5) { onStop() } }
.accessibilityAction(named: "Tilt up")   { onCommand(0, 0.5);  DispatchQueue.main.asyncAfter(deadline: .now()+0.5) { onStop() } }
.accessibilityAction(named: "Tilt down") { onCommand(0, -0.5); DispatchQueue.main.asyncAfter(deadline: .now()+0.5) { onStop() } }
```

---

### C-3: `GlassZoomSlider` — no accessible value or adjustment
**File:** `ios/WaveCam/Sources/MergedLiveView.swift:376`

`GlassZoomSlider` is a custom `DragGesture` control with no accessibility label, no `accessibilityValue`, and no `adjustableAction`. VoiceOver cannot zoom the camera. The `+`/`-` overlay images at 8pt have no labels and are decorative in intent, but the slider body itself is completely silent.

**Fix:** Add to the outer `GeometryReader` frame or its `ZStack`:
```swift
.accessibilityElement(children: .ignore)
.accessibilityLabel("Zoom")
.accessibilityValue(zoomCommand == 0 ? "Stopped" : zoomCommand > 0 ? "Tele" : "Wide")
.accessibilityAdjustableAction { direction in
    switch direction {
    case .increment: zoomCommand = min(1.0, zoomCommand + 0.25)
    case .decrement: zoomCommand = max(-1.0, zoomCommand - 0.25)
    @unknown default: break
    }
}
```

---

### C-4: `EmergencyStopButton(.chip)` in TopBar — touch target too small
**File:** `ios/WaveCam/Sources/ContentView.swift:44–53`

The `.chip` style specifies `.frame(minHeight: 44)` vertically, but the horizontal dimension is set only by `HStack` content: `12pt` horizontal padding on each side plus the text `"KILL"` (4 glyphs at 12pt bold ≈ 28pt) and a 9x9pt square. Actual tappable width is approximately 61pt — that part passes. However the `RoundedRectangle` background inside the `TopBar`'s `HStack` carries no `.contentShape` modifier, so the hit area is the exact visual bounds, not padded. The vertical `minHeight: 44` is also at risk of collapsing when the TopBar `HStack` constrains height. More importantly, in landscape on smaller iPhones (iPhone 12 mini) the TopBar has `WaveCamClient.connected` text plus `GuideButton` (30×30) plus the chip, and the chip's horizontal padding is just 12pt each side — borderline for one-handed gloved use.

Additionally, `GuideButton` is 30×30pt with no `.contentShape` enlargement.

**Fix:**  
- Add `.contentShape(Rectangle().size(CGSize(width: max(frame.width, 44), height: 44)))` to `GuideButton` body, or change `.frame(width: 30, height: 30)` to `.frame(width: 44, height: 44)` with the image inset.
- For the `.chip` style, add `.frame(minWidth: 44)` alongside `minHeight: 44`.

---

## High

### H-1: Dynamic Type not supported — entire app uses fixed font sizes
**File:** `ios/WaveCam/Sources/Theme+Glass.swift:14–22`

Every token in `WCFont` uses `Font.system(size:weight:)` with a literal point size. Swift UI's `Font.system(size:)` does **not** scale with the user's preferred text size setting; only semantic styles (`.body`, `.caption`, `.headline`, etc.) or `Font.system(.body)` with `relativeTo:` scale. This affects every screen.

Affected tokens and typical impact:
- `WCFont.label` (10pt) and `WCFont.caption` (11pt): at Accessibility sizes these should be 17–20pt; they stay at 10–11pt.
- `WCFont.title` (20pt): stays 20pt; semantic `.title` would be ~28pt at AX5.
- Inline `.font(.system(size: 9, ...))` throughout `LiveView.swift`, `AgentView.swift`, `TuneView.swift` etc. are even smaller.

For an outdoor, sun-bright context this is especially problematic — users who rely on Large Text cannot read telemetry.

**Fix:** Replace `WCFont` tokens with scaled equivalents:
```swift
static let title      = Font.system(.title2).weight(.bold)
static let heading    = Font.system(.headline)
static let body       = Font.system(.callout)
static let bodyBold   = Font.system(.callout).weight(.semibold)
static let caption    = Font.system(.caption)
static let captionMono = Font.system(.caption).monospaced()
static let mono       = Font.system(.callout).monospaced().weight(.semibold)
static let label      = Font.system(.caption2).weight(.semibold)
```
For inline sizes (especially `size: 9`, `size: 7`, `size: 8` in HUD overlays) wrap in `.dynamicTypeSize(.xSmall ... .large)` to cap growth to a readable ceiling while still honoring moderate accessibility sizes.

---

### H-2: Color-only state signifiers throughout — colorblind users get no alternative
**File:** Multiple

All of the following state values are communicated **only** by color; no secondary shape, icon, label, or pattern distinguishes them for deuteranopia / protanopia users:

| Location | Colors used | States distinguished |
|----------|-------------|----------------------|
| `GlassLockChip` text color | teal / red / orange / orange | LOCKED / STOPPED / OFFLINE / SEARCH |
| `FeedTopTags` (`LiveTag`) | orange / orange / red | LOCKED / OFFLINE / REC — LOCKED and OFFLINE are both orange |
| `SupervisorServiceRow` status dot | teal / amber / red / gray | up / degraded / down / unknown |
| `AgentRequestCard` status dot | varies | idle/requesting/requested/failed |
| Connection status icon | teal / red | CONNECTED / OFFLINE |
| `GlassChip` "WAIT"/"NOW"/"DONE" | gray / teal / teal | pending / active — visually similar |
| `CalibrationStepRow` badge | teal-filled / number | done vs active vs pending |

**Fix:** Supplement color with a redundant non-color cue:
- `SupervisorServiceRow`: add a shape alongside the dot (circle=up, triangle=warn, square=down, dash=unknown) or use `accessibilityLabel` on the row that announces the state, not just the name.
- `FeedTopTags` LOCKED vs OFFLINE: currently both orange — differentiate with a lock icon for LOCKED and wifi.slash for OFFLINE.
- `GlassChip` WAIT chips: add a different icon from NOW/DONE chips, or differ by shape (rounded vs squared).
- All status dots: ensure `.accessibilityLabel` on the parent element announces the state text, not just the color.

---

### H-3: `GlassLockChip` and `FeedTopTags` — no accessibilityLabel, VoiceOver reads nothing meaningful
**File:** `ios/WaveCam/Sources/MergedLiveView.swift:516–523`, `ios/WaveCam/Sources/LiveView.swift:660–679`

`GlassLockChip` renders `GlassChip` components with no accessibility modifier on the container or individual chips. `FeedTopTags` likewise has no label. VoiceOver will attempt to read the text inside (`"LOCKED"`, `"REC"`, etc.) but the chips are purely presentational — they are not `Button` or `Toggle` — so there is no guarantee the system will aggregate them usefully. More importantly, the tracking state is a critical safety signal: a blind operator who triggered auto-track needs to know whether the camera has a lock.

**Fix:**
```swift
// GlassLockChip body
HStack(...) { ... }
    .accessibilityElement(children: .ignore)
    .accessibilityLabel(accessibilityStatusString)

private var accessibilityStatusString: String {
    var parts: [String] = []
    parts.append("Tracking: \(lockLabel)")
    if isRecording { parts.append("Recording") }
    return parts.joined(separator: ". ")
}
```

---

### H-4: `RecordButton(compact:)` uses `stop.fill` icon for both Stop Recording and Stop PTZ (Emergency)
**File:** `ios/WaveCam/Sources/LiveView.swift:895–939`

When recording is active, the compact `RecordButton` shows `stop.fill` icon on a red background — visually identical to `EmergencyStopButton(style: .icon)`. Both appear in the same `LiveControlRail` horizontal dock (portrait) and vertical rail (landscape). A user in a hurry — or a colorblind user for whom the red background is ambiguous — cannot reliably distinguish "stop recording" from "emergency stop." Only their horizontal position in the dock separates them.

**Fix:** Use a visually distinct icon for stop-recording, e.g. `"record.circle.fill"` (filled red circle, which is the conventional stop-recording glyph) instead of `stop.fill`. The Emergency Stop's square-fill icon is the safety-critical one and should remain unique.

---

### H-5: `presetChip` delete button — touch target ~18pt, nested inside a tappable chip
**File:** `ios/WaveCam/Sources/TuneView.swift:274–286`

The `xmark` delete button inside a user-created preset chip uses `.font(.system(size: 9, weight: .bold))` and has no explicit frame. The actual tappable area is the glyph size only — approximately 16–18pt square. This is well below the 44pt minimum. The outer chip button is itself tappable, creating nested tap conflicts.

**Fix:** Add `.frame(width: 36, height: 36)` on the inner delete button's label and give it a clear `.contentShape(Rectangle())`:
```swift
Button { ... } label: {
    Image(systemName: "xmark")
        .font(.system(size: 9, weight: .bold))
        .frame(width: 36, height: 36)
        .contentShape(Rectangle())
}
```
Also add `.accessibilityLabel("Delete \(preset.name) preset")` to that inner button.

---

### H-6: `MediaFileRow` checkbox — no accessibilityLabel in select mode
**File:** `ios/WaveCam/Sources/MediaView.swift:347–374`

In selection mode the file row shows a `checkmark.circle.fill` / `circle` icon. The row itself has `.onTapGesture { if isSelecting { onToggleSelect() } }` but no `accessibilityLabel` or `accessibilityAddTraits(.isButton)`. VoiceOver will read the SF Symbol name ("checkmark circle" or "circle") and the filename, with no indication this is a selectable checkbox or what the current selection state is.

**Fix:**
```swift
// On the row's HStack or on the icon:
.accessibilityElement(children: .combine)
.accessibilityLabel(file.name)
.accessibilityAddTraits(isSelecting ? .isButton : [])
.accessibilityValue(isSelecting ? (isSelected ? "Selected" : "Not selected") : "")
.accessibilityHint(isSelecting ? "Double-tap to \(isSelected ? "deselect" : "select")" : "")
```

---

## Medium

### M-1: `faint` color at 9–11pt normal weight fails WCAG AA normal-text 4.5:1
**File:** `ios/WaveCam/Sources/Theme.swift:13`, used throughout

`WC.faint` (#5B6873) on `WC.bg` (#070B0F) yields **3.45:1** contrast ratio. This fails WCAG AA normal-text (4.5:1) and passes only large-text (3:1). `WC.faint` is used at 7–11pt throughout: `FeedMetric` label at 8.5pt (`LiveView.swift:797`), `PTZOverlayMetric` label at 7pt (`LiveView.swift:615`), `JoystickLabels` at 9pt (`JoystickPad.swift:94`), `OperatorSectionLabel` at 10pt (`Theme+Glass.swift:156`), `WCFont.label` text throughout. Outdoor bright-sun context makes this worse.

**Fix:** Darken `WC.bg` or lighten `WC.faint` to achieve 4.5:1. #6B7D8A yields ~4.5:1 on bg. Alternatively, treat `faint` as decorative-only and replace any readable label use with `WC.muted`.

---

### M-2: `TopBar` version string at `size: 9` — tiny, fails 4.5:1 at all sizes
**File:** `ios/WaveCam/Sources/ContentView.swift:84–88`

The app version `"v\(v) (\(b))"` is rendered at `.font(.system(size: 9, design: .monospaced))` in `WC.faint` on `WC.ink`. Size 9pt at normal weight is below the WCAG "large text" threshold of 18pt or 14pt bold, so it requires 4.5:1 (computed: **3.24:1**). It fails. While this is diagnostic metadata, it still renders visible text that VoiceOver would read via `.accessibilityLabel("App version \(appVersion)")`.

**Fix:** Raise to 11pt minimum, or remove the accessibilityLabel so VoiceOver ignores it completely (it is metadata, not operator-critical content). If it must be visible, use `WC.muted` instead of `WC.faint`.

---

### M-3: `Reduce Motion` — `withAnimation` calls not conditioned, KillLatchOverlay has in/out animation
**File:** `ios/WaveCam/Sources/ContentView.swift:199`, `MergedLiveView.swift:224`, `MediaView.swift:64`

Several transitions use `.animation(.easeInOut)`, `.spring()`, or `withAnimation` without checking `@Environment(\.accessibilityReduceMotion)`. The `HoldToResumeButton` fill animation (`.animation(.linear(duration: 1.2), value: pressing)`) is especially prominent — it's on the safety-critical resume path and plays every time the operator holds the button. The `MergedLiveView` fullscreen toggle uses `withAnimation(.easeInOut(duration: 0.2))`.

**Fix:** Guard animations:
```swift
@Environment(\.accessibilityReduceMotion) private var reduceMotion

// in fullscreenToggleButton:
withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.2)) { isFullscreen.toggle() }

// in HoldToResumeButton:
.animation(reduceMotion ? nil : .linear(duration: holdDuration), value: pressing)
```

---

### M-4: `Reduce Transparency` / `GlassSurface` — glass effect not conditioned
**File:** `ios/WaveCam/Sources/Theme+Glass.swift:68–96`

`GlassSurface` on iOS 26+ applies `.glassEffect(.regular, ...)`. On iOS < 26 it uses `Color.black.opacity(0.52)` + a white stroke. Neither path checks `@Environment(\.accessibilityReduceTransparency)`. When Reduce Transparency is on, translucent backgrounds should become fully opaque. The fallback path's solid black at 52% opacity is a partial fix but still partially translucent — a live video feed bleeds through all glass surfaces in the rail, potentially making text unreadable.

**Fix:**
```swift
@Environment(\.accessibilityReduceTransparency) private var reduceTransparency

// In pre-iOS26 path:
RoundedRectangle(...)
    .fill(reduceTransparency ? Color.black.opacity(0.9) : Color.black.opacity(0.52))
```
And suppress `.glassEffect` on iOS 26 when reduce-transparency is on, using `WC.panel` as a solid fill.

---

### M-5: `daySection` collapse button — no accessibilityLabel describing collapsed state
**File:** `ios/WaveCam/Sources/MediaView.swift:138–166`

The day-group header `Button` uses a `chevron.right` / `chevron.down` icon with a day label. The button has no `.accessibilityLabel` and no state announced. VoiceOver reads "chevron right" (or down) followed by the date text; it does not announce "collapsed" vs "expanded" and gives no hint about what activating the button does.

**Fix:**
```swift
Button { ... } label: { ... }
    .accessibilityLabel("\(day.label), \(day.files.count) recording\(day.files.count == 1 ? "" : "s"), \(collapsed ? "collapsed" : "expanded")")
    .accessibilityHint(collapsed ? "Double-tap to expand" : "Double-tap to collapse")
    .accessibilityAddTraits(.isButton)
```

---

### M-6: `CalibrationStepRow` — status chip color is sole state indicator, no label on parent button
**File:** `ios/WaveCam/Sources/CalibrateView.swift:294–315`

The calibration step list uses `GlassChip` with text "DONE" / "NOW" / "WAIT" in teal / teal / gray respectively. "DONE" and "NOW" are both teal — distinguished only by the text string, which is good. However the parent `Button` has no `.accessibilityLabel` synthesizing step title + state, so VoiceOver reads "Step 1, Preflight checks, DONE" out of the combined children, which works. The gap is that `StepBadge` when `.done` shows a checkmark on a teal-filled circle — a colorblind user can read the "DONE" text, so this is acceptable. **Low risk here; flagged for completeness.**

Actual concern: the `Back` / `Next` / capture buttons in `CalibrationActiveCard` are `GlassButton` components with no `.accessibilityLabel` override — they inherit the `label` param ("Back", "Next", action title). That is fine. But the disabled state (`.disabled(!canGoBack)`) is not explicitly announced. SwiftUI does announce grayed buttons as "dimmed" to VoiceOver in some versions, but this is not guaranteed.

**Fix:** Add `.accessibilityHint("Not available until current step is captured")` to the disabled-path buttons.

---

### M-7: `MockDataBanner` — no accessibility role, will not draw VoiceOver attention urgently
**File:** `ios/WaveCam/Sources/ContentView.swift:142–156`

`MockDataBanner` is a safety-critical warning (the operator must know they are watching fake data). It appears inline in the VStack but has no `.accessibilityAddTraits(.isHeader)`, no `.accessibilityLabel`, and no announcement via `AccessibilityNotification.announcement` when it appears. VoiceOver will eventually read it in tab order, but a user already navigating might miss it.

**Fix:** Post an announcement when the banner appears:
```swift
.onAppear {
    AccessibilityNotification.Announcement("Warning: offline — showing mock data. Real camera state unknown.")
        .post()
}
```
Also add `.accessibilityAddTraits(.isHeader)` and an explicit `.accessibilityLabel` so it reads clearly.

---

## Low

### L-1: Decorative elements in feed not hidden from VoiceOver
**File:** `ios/WaveCam/Sources/LiveView.swift:430–470` (`FeedReticles`, `ReticleCorner`), `FeedAimReticle`, `SurferGlyph`, `WaveBands`, `MockOceanScene`

`FeedReticles` / `ReticleCorner`, `FeedAimReticle`, `SurferGlyph`, and `WaveBands` are all purely decorative graphical elements. None has `.accessibilityHidden(true)`. VoiceOver traverses them as unnamed elements ("Path", "Image", etc.), adding noise to the element order.

**Fix:** Add `.accessibilityHidden(true)` to `FeedReticles`, `SurferGlyph`, `WaveBands`, the `MockOceanScene` gradient views. `FeedAimReticle` warrants a single accessible label rather than hiding: it communicates PTZ movement. Add `.accessibilityLabel(isMoving ? "Camera moving" : "Camera aim reticle").accessibilityHidden(!connected)`.

---

### L-2: `TopBar` brand logo elements — decorative, exposed to VoiceOver
**File:** `ios/WaveCam/Sources/ContentView.swift:75–88`

The TopBar's brand logo is a `Circle().fill(WC.brand)` plus split "WAVE"/"CAM" texts, each with their own color. VoiceOver will read these separately as unnamed shapes and two separate text elements. The version string has `.accessibilityLabel` but the brand logo does not.

**Fix:** Wrap the entire brand `HStack` in `.accessibilityElement(children: .ignore).accessibilityLabel("WaveCam").accessibilityAddTraits(.isHeader)`. The version string `.accessibilityLabel` can remain or be hidden.

---

### L-3: VoiceOver element order in portrait dock may be illogical
**File:** `ios/WaveCam/Sources/MergedLiveView.swift:325–366`

In portrait, the `horizontalDock` HStack reads: Zoom slider → Divider → Auto toggle → Home → Record → Divider → Emergency Stop. The Emergency Stop is last in tab order. When navigating with VoiceOver swipe-right, a user must traverse all dock buttons to reach KILL. While this is not incorrect (KILL is reachable), it is suboptimal for a safety control.

**Fix:** Consider adding `.accessibilitySortPriority(10)` on `EmergencyStopButton(style: .icon)` so VoiceOver reaches it first in the dock, or move it to a leading position in the HStack for both visual and VoiceOver purposes (this is also a touch-accuracy improvement — left side of dock avoids accidental tap on the home-sensor side on smaller iPhones).

---

### L-4: `GlassIconButton` disabled state — `.accessibilityLabel` not on the Button, only set at call site
**File:** `ios/WaveCam/Sources/Theme+Glass.swift:278–313`

`GlassIconButton` accepts `disabled: Bool` but does not add `.accessibilityHint("Unavailable")` or expose the disabled state beyond opacity. SwiftUI propagates `.disabled()` to accessibility in recent iOS versions, but the `disabled` var only sets `.disabled(disabled)` and `.opacity(0.45)` — the button may still appear activatable to switch-control users.

**Fix:** Add `.accessibilityHint(disabled ? "Unavailable" : "")` inside `GlassIconButton.body`.

---

### L-5: `AgentView` log rows — dense 10–11pt fixed text, no Dynamic Type
**File:** `ios/WaveCam/Sources/AgentView.swift:394–430`

`LogLineRow` uses `size: 10`, `size: 9`, and `size: 11` fixed fonts for timestamp, level badge, and message respectively. These already fail Dynamic Type generally (see H-1) but are called out specifically because the `.lineLimit(3)` on the message at size 11 will clip content for any user who has bumped up their text size manually via Settings > Display & Brightness. The log card has a `.lineLimit(3)` and `.multilineTextAlignment(.leading)` but fixed sizes mean the truncation point is always at the same character count, not based on available space.

**Fix:** Use `.font(.system(.caption, design: .monospaced))` for log lines, allowing the system to scale them. Add `.minimumScaleFactor(0.85)` to the message text if needed to prevent over-wrapping.

---

## Notes on items that were reviewed and found acceptable

- **EmergencyStopButton `.prominent` / `.compact` / `.icon`:** `.accessibilityLabel("Emergency stop")` is present and consistent across all non-chip styles. The `.icon` style is 44×44pt. The `.prominent` variant uses `frame(maxWidth: .infinity, minHeight: 44)`. These pass.
- **`KillLatchOverlay` visibility and size:** The overlay covers the full screen and the `HoldToResumeButton` has `frame(minHeight: 44)`. The problem is operability (C-1), not visibility or size.
- **`RecordButton` full variant:** 44pt minHeight, clear `accessibilityLabel`. Passes.
- **`GlassButton`:** `minHeight: 44`, `lineLimit(1)`, `minimumScaleFactor(0.8)`. Passes target size. Scaling factor mitigates (but does not fix) Dynamic Type absence at extreme sizes.
- **`MediaView` bulk bar buttons:** `Label("Download", ...)` and `Label("Delete", ...)` with `font(.system(size: 14, weight: .semibold))` — these are text+icon Labels in an HStack, not icon-only. The buttons lack explicit `frame(minHeight: 44)` but are in a `padding(.vertical, WCSpace.md)` = 12pt bar, giving effective height ≈ 14 + 24 = 38pt. Borderline but the bar spans the full width and contextShape is the full row.
- **`GuideButton` accessibility label:** `.accessibilityLabel("Open guide for this screen")` — present and clear.
- **Connection form `GlassIconButton` labels:** "Refresh status" and "Use default connection" — present.
- **`TuneView` sliders:** Use native `Slider` which exposes `accessibilityValue` and increment/decrement automatically. Labels are set via the `label` parameter. Pass.
- **`TuneView` toggles:** Use native `Toggle` — accessible by default. Pass.
- **Orientation parity:** No controls found that are conditionally removed in one orientation vs the other; landscape uses the sidebar rail, portrait uses the bottom dock, but both contain the same set of controls. Pass.
- **Contrast — primary text (`WC.txt`):** 17:1 on bg — well above requirements. Pass.
- **Contrast — `WC.muted`:** 6.3:1 — passes at all sizes. Pass.
- **Contrast — `WC.kill` text on panel backgrounds:** 4.9–5.6:1 — passes. Pass.
- **Contrast — `WC.accent` / `WC.ok` on backgrounds:** 9.2–10.4:1 — passes. Pass.
- **Contrast — white on `WC.kill` (Emergency Stop button):** 3.55:1 — this is large, bold text (16pt black weight) which qualifies as WCAG "large text" (≥14pt bold), requiring only 3:1. **Passes for large text.** Not ideal for outdoor glare; consider `black` text (5.9:1) in a future update.
