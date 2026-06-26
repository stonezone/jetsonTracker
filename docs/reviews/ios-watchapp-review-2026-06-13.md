# WaveCam iOS & watchOS — Deep Review

**Date:** 2026-06-13  
**Scope:** `ios/WaveCam` iPhone app + `Sources-Watch` watchOS app, build config, plists, entitlements. Cross-checked against `docs/superpowers/specs/2026-06-01-wavecam-control-api-spec.md` and `orin/wavecam/wavecam/control_api.py`.  
**Method:** Manual code review, `xcodegen` + `xcodebuild` verification, anti-vibe-engineering discipline, and Context7/web verification of iOS 26 Liquid Glass APIs (`GlassEffectContainer`, `.glassEffect`).  
**Status:** Build succeeds with no warnings. No code was modified for this review.

---

## Executive Summary

The WaveCam iOS/watchOS codebase is well-organized, type-safe, and ships a clean dark/glass operator UI. The safety model (optimistic KILL latch, hold-to-resume, auth token in Keychain, failover between tether/Wi-Fi) is mostly sound. However, I found **one critical safety race** in the KILL latch logic, **two high-severity functional bugs** (watch route caching and MJPEG auth), and a number of UX/reliability/maintainability issues that should be addressed before field use.

This document lists findings in priority order, with exact file/line references and step-by-step fix instructions. **Stop here and review before making changes.**

---

## Build Verification

```bash
cd ios/WaveCam
xcodegen generate
xcodebuild -project WaveCam.xcodeproj -scheme WaveCam \
  -destination 'platform=iOS Simulator,name=iPhone 16' \
  -configuration Debug build
```

**Result:** `** BUILD SUCCEEDED **` — no compiler errors or warnings. Watch app also builds and embeds correctly.

---

## Findings Matrix

| # | Severity | Area | File(s) | Issue | Effort |
|---|----------|------|---------|-------|--------|
| 1 | 🔴 Critical | Safety / KILL latch | `WaveCamClient.swift` | Optimistic KILL latch can clear before backend confirms, briefly hiding the stop state while the camera may still move. | Small |
| 2 | 🟠 High | Watch connectivity | `WatchClient.swift` | Caching `resolvedBase` prevents command/status failover to the alternate route in the same request. | Small |
| 3 | 🟠 High | Auth / feed | `WaveCamClient.swift`, `FeedComponents.swift` | MJPEG preview stream is requested without the bearer token; fails when Orin auth is enabled. | Small |
| 4 | 🟡 Medium | Media download | `WaveCamClient.swift` | Downloaded recordings are saved to the temp directory, not Documents/Caches, and may be purged. | Small |
| 5 | 🟡 Medium | Watch auth | `WatchClient.swift`, `WatchSessionReceiver.swift` | Watch app never sends the bearer token; all commands fail if Orin auth is enabled. | Medium |
| 6 | 🟡 Medium | Watch config | `WatchClient.swift` | Orin base URLs are hardcoded; no way to sync iPhone connection settings. | Medium |
| 7 | 🟡 Medium | Backend parity | `TuneView.swift`, `orin/wavecam/...` | `tracking.mode` UI is feature-detected via `supported.trackingMode`, which the backend never advertises, so controls never appear in live mode. | Backend / Small iOS |
| 8 | 🟢 Low | Accessibility | `ContentView.swift` | `GuideButton` applies two `accessibilityLabel` modifiers; the second overrides the first. | Tiny |
| 9 | 🟢 Low | UX copy | `AgentView.swift` | Summon button always reads "Summon Codex" even when Claude or DeepSeek is selected. | Tiny |
| 10 | 🟢 Low | Performance / correctness | `ConnectionView.swift`, `CalibrateView.swift` | `Dictionary.keys.sorted()` is computed twice per render in a few places. | Tiny |
| 11 | 🟢 Low | Code health | `WaveCamClient.swift` | File is 1,610 lines and mixes models, decoding, transport, and commands. | Medium refactor |
| 12 | 🟢 Low | Code health | `MergedLiveView.swift`, `TuneView.swift`, `AgentView.swift` | Several views exceed 500 lines and mix layout, state, and actions. | Medium refactor |
| 13 | 🟢 Low | Watch UI | `WatchStatusView.swift` | Resume hold progress uses `* .infinity` width hack instead of proportional fill. | Small |
| 14 | 🟢 Low | UX | `EmergencyStopButton.swift` | No haptic feedback on KILL press. | Tiny |
| 15 | 🟢 Low | Validation | `ConnectionView.swift` | URL validation only parses strings; it does not verify the endpoint is reachable. | Small |
| 16 | 🟢 Low | Test gap | Project | No unit or UI tests exist. | Medium |
| 17 | 🟢 Low | Naming | `SessionLogView.swift` | `sinCursor` is likely a typo for `sinceCursor`. | Tiny |
| 18 | 🟢 Low | Maintainability | `WaveCamClient.swift` | Comment refers to non-existent `WaveCamMediaListUnavailable` sentinel. | Tiny |
| 19 | 🟢 Low | Cleanup | `WatchSessionRecorder.swift` | Recorded JSONL files are never deleted from the watch after transfer. | Small |
| 20 | 🟢 Low | Config | `project.yml`, `InfoWatch.plist` | Hardcoded `DEVELOPMENT_TEAM` and watch app version strings. | Tiny |

---

## 1. Critical: Optimistic KILL Latch Can Clear Prematurely

**File:** `ios/WaveCam/Sources/WaveCamClient.swift`  
**Lines:** `785`, `826–848`

### Problem

`WaveCamClient.refresh()` unconditionally clears `optimisticKilled` on every successful status poll:

```swift
// WaveCamClient.swift ~L785
optimisticKilled = false
```

`kill()` sets `optimisticKilled = true` so the UI can show the stop overlay immediately, then it awaits the POST and a follow-up refresh. But the 1 Hz background polling task can call `refresh()` *between* the operator pressing KILL and the backend actually processing the command. If that poll returns `killed: false` (because the kill is still in flight), the latch is cleared, the overlay disappears, and the operator may believe the camera is stopped when it is not.

This violates the safety invariant that the UI must never falsely report "not killed."

### Fix

Gate clearing of the optimistic latch so it is only dropped when:
1. The backend explicitly reports `killed == true`, or
2. The operator explicitly resumes, or
3. The KILL POST itself fails.

Introduce a `killInFlight` flag to distinguish "we asked for a kill and are waiting for confirmation" from normal polling.

**Step-by-step:**

1. Add a new private flag in `WaveCamClient`:

```swift
/// True while a KILL request is in flight and the backend has not yet confirmed it.
private var killInFlight = false
```

2. Replace the unconditional clear in `refresh()` (~L785) with:

```swift
connected = true
lastError = nil
if status?.safety.killed == true {
    // Backend has confirmed the kill (or another client resumed and we re-killed).
    optimisticKilled = false
    killInFlight = false
} else if !killInFlight {
    // No pending kill request; trust fresh status.
    optimisticKilled = false
}
```

3. Update `kill()` (~L826):

```swift
func kill(reason: String = "operator") async {
    optimisticKilled = true
    killInFlight = true
    if mode == .mock { mockKilled = true; await refresh(); return }
    do {
        _ = try await post("safety/kill", body: ["reason": reason, "source": "ios_native"])
        lastCommandError = nil
    } catch {
        lastCommandError = "Safety stop not confirmed by Orin: \(error.localizedDescription)"
        // The request never reached the server; do not leave a false latch.
        optimisticKilled = false
        killInFlight = false
    }
    await refresh()
    if killed {
        optimisticKilled = false
        killInFlight = false
    }
}
```

4. Update `resume()` (~L838) to reset the in-flight flag:

```swift
func resume() async {
    optimisticKilled = false
    killInFlight = false
    ...
}
```

5. Verify: temporarily add unit tests (see #16) that simulate a status poll returning `killed: false` immediately after `kill()` is called; the overlay must remain latched until a subsequent poll returns `killed: true` or the user resumes.

---

## 2. High: Watch Route Cache Prevents Failover

**File:** `ios/WaveCam/Sources-Watch/WatchClient.swift`  
**Lines:** `135`, `170`

### Problem

Both `get(_:)` and `post(_:body:)` build the candidate list like this:

```swift
let candidates = resolvedBase.map { [$0] } ?? [tetherBase, wifiBase]
```

Once `resolvedBase` is set, the watch tries **only** that route. If the cached route flakes (e.g., tether IP black-holes while Wi-Fi is healthy), the current request fails even though the other route may work. The next poll will try both because `resolvedBase = nil` is set on failover-allowed errors, but **commands** (`kill`, `resume`, `toggleRecording`) are not retried and will fail outright.

### Fix

Always include both tether and Wi-Fi as fallbacks, with the resolved route preferred.

**Step-by-step:**

1. Add a small helper at the bottom of `WatchClient.swift`:

```swift
private extension WatchClient {
    /// Prefer the cached route, but keep both hardcoded fallbacks so a single
    /// cached-route failure does not prevent failover in the same request.
    func routeCandidates(preferred: URL? = nil) -> [URL] {
        let fallbacks = [tetherBase, wifiBase]
        guard let preferred else { return fallbacks }
        var seen = Set<String>()
        var result: [URL] = []
        for url in [preferred] + fallbacks {
            guard seen.insert(url.absoluteString).inserted else { continue }
            result.append(url)
        }
        return result
    }
}
```

2. In `get(_:)` replace:

```swift
let candidates = resolvedBase.map { [$0] } ?? [tetherBase, wifiBase]
```

with:

```swift
let candidates = routeCandidates(preferred: resolvedBase)
```

3. In `post(_:body:)` do the same replacement.

4. Verify with a unit test that stubs `URLSession` to fail the first candidate and succeed on the second.

---

## 3. High: MJPEG Preview Stream Does Not Send Auth Token

**File:** `ios/WaveCam/Sources/WaveCamClient.swift` (~L1365), `ios/WaveCam/Sources/FeedComponents.swift` (~L67)  
**Backend:** `orin/wavecam/wavecam/control_api.py` L229

### Problem

`previewURL` is a plain URL, and `MJPEGPreviewView.Coordinator.connect(url:)` creates a `URLSessionDataTask` directly from that URL:

```swift
let task = session.dataTask(with: url)
```

The Orin endpoint is protected by `Depends(require(READ))`. When auth is enabled, the preview stream returns `401 Unauthorized` and the feed stays blank. The iOS app already stores the token in Keychain and sends it on REST calls, but never for the MJPEG stream.

### Fix

Pass the token into the MJPEG preview coordinator and set it as an `Authorization` header.

**Step-by-step:**

1. In `MJPEGPreviewView`, add a token property:

```swift
struct MJPEGPreviewView: UIViewRepresentable {
    let url: URL
    let token: String?   // new
    ...
}
```

2. In `makeUIView(context:)` and `updateUIView(_:context:)`, pass the token to the coordinator. The coordinator already stores `loadedURL` and `imageView`; add `token`:

```swift
final class Coordinator: NSObject, URLSessionDataDelegate {
    ...
    var token: String?
    ...
}
```

3. In `connect(url:)`, build a `URLRequest` instead of using the bare URL:

```swift
private func connect(url: URL) {
    ...
    var request = URLRequest(url: url)
    request.timeoutIntervalForRequest = 10
    if let token = token {
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
    }
    let task = session.dataTask(with: request)
    ...
}
```

4. In `MergedLiveView.feedCard(fullscreen:)`, pass the token from the client:

```swift
if let url = client.previewURL {
    MJPEGPreviewView(url: url, token: client.token)
}
```

5. Verify by enabling auth on the Orin and confirming the feed renders.

---

## 4. Medium: Downloaded Media Is Saved to the Temp Directory

**File:** `ios/WaveCam/Sources/WaveCamClient.swift`  
**Lines:** `1331–1348`

### Problem

`downloadMedia(name:)` moves the downloaded file into `FileManager.default.temporaryDirectory`:

```swift
let dest = FileManager.default.temporaryDirectory
    .appendingPathComponent("WaveCam-\(name)", conformingTo: .mpeg4Movie)
```

The comment claims it moves to a "durable Documents file," but `temporaryDirectory` is not durable. iOS can purge temp files, and the share sheet may fail if the file is cleaned up before the user completes the share action.

### Fix

Save to the app Documents directory (which is already file-sharing enabled in `Info.plist`).

**Step-by-step:**

1. Replace the destination construction in `downloadMedia`:

```swift
let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
let dest = docs.appendingPathComponent("WaveCam-\(name)", conformingTo: .mpeg4Movie)
```

2. Leave the rest of the move logic unchanged.

3. Optionally, add a cleanup routine in `MediaView` or `App` to remove files older than N days from Documents to avoid unbounded growth.

---

## 5. Medium: Watch App Has No Auth Token Support

**File:** `ios/WaveCam/Sources-Watch/WatchClient.swift`  
**Related:** `ios/WaveCam/Sources/WatchSessionReceiver.swift`, `ios/WaveCam/Sources-Watch/WaveCamWatchApp.swift`

### Problem

`WatchClient.get(_:)` and `post(_:body:)` never set an `Authorization` header. If the Orin has auth enabled, every watch command (KILL, resume, record) fails. This is especially serious because the watch is intended as a wrist-worn safety device.

### Fix

Sync the token from the paired iPhone via `WCSession.updateApplicationContext`, then use it in watch requests.

**Step-by-step:**

1. Add a token store on the watch side. The simplest place is a small `@Observable` or singleton that `WatchClient` reads:

```swift
// Sources-Watch/WatchAuthStore.swift
@MainActor
final class WatchAuthStore: ObservableObject {
    static let shared = WatchAuthStore()
    var token: String?
}
```

2. In `WatchClient`, set the header in both `get` and `post`:

```swift
var request = URLRequest(url: base.appending(path: path))
request.timeoutInterval = 3
if let token = WatchAuthStore.shared.token {
    request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
}
```

3. On the iPhone side, broadcast the token whenever it changes. In `ConnectionView.applySettings()`, after saving to Keychain:

```swift
if WCSession.isSupported() {
    let tokenPayload: [String: Any] = ["wavecam_auth_token": tokenText]
    WCSession.default.updateApplicationContext(tokenPayload)
}
```

Also broadcast it from `WaveCamApp.applyStoredSettings()` at launch.

4. In `WaveCamWatchApp` (or `WatchSessionDelegate`), implement:

```swift
func session(_ session: WCSession, didReceiveApplicationContext applicationContext: [String: Any]) {
    if let token = applicationContext["wavecam_auth_token"] as? String, !token.isEmpty {
        WatchAuthStore.shared.token = token
    }
}
```

5. Verify by enabling Orin auth and confirming watch KILL/resume/record succeed.

---

## 6. Medium: Watch App Uses Hardcoded Orin URLs

**File:** `ios/WaveCam/Sources-Watch/WatchClient.swift`  
**Lines:** `61–62`

### Problem

```swift
private let tetherBase = URL(string: "http://172.20.10.8:8088/api/v1")!
private let wifiBase   = URL(string: "http://192.168.1.155:8088/api/v1")!
```

These match the iOS defaults, but if the user edits the URLs on the phone, the watch stays on the defaults. There is no watch UI to change them.

### Fix

Sync the active URLs from the iPhone alongside the token (see #5). Extend the application context payload:

```swift
[
    "wavecam_auth_token": tokenText,
    "wavecam_tether_url": tetherURLText,
    "wavecam_wifi_url": wifiURLText,
]
```

On the watch, receive and store these in `WatchAuthStore` (rename to `WatchConnectionStore`), and replace the hardcoded `tetherBase`/`wifiBase` in `WatchClient` with stored values that fall back to the defaults.

---

## 7. Medium: Tracking Mode UI Never Appears in Live Mode

**File:** `ios/WaveCam/Sources/TuneView.swift` (~L676), `ios/WaveCam/Sources/WaveCamClient.swift` (~L231)  
**Backend:** `orin/wavecam/wavecam/control_snapshots.py` (~L107)

### Problem

The Tune panel feature-detects tracking mode with:

```swift
if client.mode == .mock || cfg.supported?.trackingMode == true {
    trackingModeAvailable = true
}
```

But the backend `supported` block advertises `calibration`, `cinematic_zoom`, `media`, `media_delete`, `presets`, `logs`, `ptz_home`, `show_hud`, `gps`, etc. — **not** `tracking_mode`/`trackingMode`. The `tracking.mode` key is also absent from `HOT_CONFIG_KEYS` in the backend. As a result, the tracking-mode picker never appears when connected to a real Orin.

### Fix

This is primarily a backend feature gap. Two options:

1. **Backend-first:** Add `tracking_mode` to the config snapshot's `supported` block and add `tracking.mode` to `HOT_CONFIG_KEYS` with the appropriate adapter logic. Then the existing iOS feature detection will work.
2. **iOS-only mitigation:** If the intent is to ship without tracking-mode control, remove the mock-only branch so the UI does not mislead testers into thinking the feature is missing from the iOS build:

```swift
if cfg.supported?.trackingMode == true {
    trackingModeAvailable = true
    trackingMode = cfg.current.tracking?.mode ?? "auto"
}
```

---

## 8. Low: Duplicate Accessibility Label on Guide Button

**File:** `ios/WaveCam/Sources/ContentView.swift`  
**Lines:** `123`, `127`

```swift
.accessibilityLabel("Operator guide")
...
.accessibilityLabel("Open guide for this screen")
```

The second label overrides the first. Keep the more descriptive one and remove the other.

**Fix:** Delete line 123 (`"Operator guide"`).

---

## 9. Low: Agent Summon Button Ignores Selected Provider

**File:** `ios/WaveCam/Sources/AgentView.swift`  
**Lines:** `186–192`

```swift
var buttonTitle: String {
    switch self {
    case .requesting: "Requesting..."
    default: "Summon Codex"
    }
}
```

The picker allows Claude / Codex / DeepSeek, but the button always says "Summon Codex."

**Fix:** Pass the selected provider into `AgentRequestCard` and compute the label there:

```swift
Text(state.isRequesting ? "Requesting..." : "Summon \(provider.label)")
```

---

## 10. Low: Repeated `keys.sorted()` Calls

**Files:** `ios/WaveCam/Sources/ConnectionView.swift` (~L348, L351), `ios/WaveCam/Sources/CalibrateView.swift` (similar pattern)

### Problem

In `HealthCard`:

```swift
ForEach(components.keys.sorted(), id: \.self) { name in
    if let comp = components[name] {
        HealthRow(...)
        if name != components.keys.sorted().last { ... }
    }
}
```

`components.keys.sorted()` is recomputed for every row.

### Fix

Cache the sorted array once:

```swift
let sortedNames = components.keys.sorted()
ForEach(sortedNames, id: \.self) { name in
    if let comp = components[name] {
        HealthRow(...)
        if name != sortedNames.last { Divider()... }
    }
}
```

Apply the same pattern in `CalibrationStepsCard`.

---

## 11. Low: Monolithic Client File

**File:** `ios/WaveCam/Sources/WaveCamClient.swift` (1,610 lines)

### Problem

`WaveCamClient.swift` contains models (`WCStatus`, `WCConfig`, etc.), tolerant decoding extensions, the `WaveCamClient` class, transport helpers, and error types. This makes reviews and merge conflicts harder than necessary.

### Fix

Split into focused files without changing behavior:

```
Sources/
  Models/
    WCStatus.swift
    WCConfig.swift
    WCCalibration.swift
    WCPreset.swift
    WCEvent.swift
    WCLogLine.swift
    WCHealth.swift
    WCAgentReport.swift
  WaveCamClient.swift          // client + transport only
  WaveCamClient+Transport.swift
  WaveCamAPIError.swift
  WaveCamDefaults.swift
```

Use `internal` access for shared types. Preserve `@MainActor` and `@Observable` annotations.

---

## 12. Low: Large Views Mix Layout, State, and Actions

**Files:** `MergedLiveView.swift` (~776 lines), `TuneView.swift` (~717 lines), `AgentView.swift` (~526 lines)

### Problem

These files mix root layout, reusable subviews, action handlers, and model mapping. They are not bugs, but they are hard to test and review.

### Fix

Refactor incrementally:
- Extract each major subview into its own file (e.g., `LiveControlRail.swift`, `GlassZoomSlider.swift`, `GPSDetailCard.swift`, `TunePresetsSection.swift`, `AgentLogsCard.swift`).
- Keep only the root layout and `@State` in the parent view.
- Pass dependencies via initializer or environment, not by reaching across a large file.

This is a low-priority refactor; do it after the safety and auth fixes are verified.

---

## 13. Low: Watch Resume Hold Progress Is Not Proportional

**File:** `ios/WaveCam/Sources-Watch/WatchStatusView.swift`  
**Lines:** `148–157`

### Problem

```swift
.frame(width: holdProgress > 0
       ? max(0, holdProgress) * .infinity
       : 0)
```

Multiplying by `.infinity` causes the green fill to snap to full width as soon as `holdProgress > 0`. The operator does not see a smooth progress bar.

### Fix

Wrap the fill in a `GeometryReader`:

```swift
ZStack(alignment: .leading) {
    RoundedRectangle(cornerRadius: 10)
        .fill(Color.gray.opacity(0.25))
    GeometryReader { geo in
        RoundedRectangle(cornerRadius: 10)
            .fill(Color.green.opacity(0.6))
            .frame(width: max(0, min(1, holdProgress)) * geo.size.width)
    }
    Text(...)
}
```

---

## 14. Low: No Haptic Feedback on Emergency Stop

**File:** `ios/WaveCam/Sources/EmergencyStopButton.swift`

### Problem

A silent KILL press is easy to miss in bright sun or with gloves. Haptics make the action feel deliberate.

### Fix

Add a haptic generator in the button action:

```swift
Button {
    let generator = UINotificationFeedbackGenerator()
    generator.notificationOccurred(.error)
    Task { await client.kill() }
} label: { ... }
```

Use `.error` type because KILL is an emergency/stop action.

---

## 15. Low: Connection Settings Are Not Verified Against the Endpoint

**File:** `ios/WaveCam/Sources/ConnectionView.swift` (~L84)

### Problem

`applySettings()` only checks `URL(string:) != nil`. A malformed string is caught, but an unreachable or wrong IP is accepted silently.

### Fix

After saving settings and calling `client.configure(...)`, trigger a connectivity check and surface the result:

```swift
Task {
    await client.refresh()
    if !client.connected, let error = client.lastError {
        validationError = "Cannot reach Orin: \(error)"
    }
}
```

This reuses the existing refresh path with no new network code.

---

## 16. Low: No Automated Tests

**Project:** `ios/WaveCam/`

### Problem

There are no unit tests, UI tests, or snapshot tests. Safety-critical logic like the KILL latch, route failover, and PTZ state machine is unverified in CI.

### Fix

Add an `ios/WaveCam/Tests` target in `project.yml` and start with these focused tests:

1. `WaveCamClientTests`:
   - KILL latch remains set when a poll returns `killed: false` immediately after KILL.
   - Route failover tries Wi-Fi after tether fails.
   - `effectiveKilled` returns true when backend `killed` is true.
   - Mock fallback activates on network error when enabled.

2. `PTZManualControllerTests`:
   - `sendVelocity` starts repeat timer; `releaseManualPTZ` stops it and retries stop.
   - `syncCommandState` transitions `.manual` → `.auto` when owner becomes autonomous.

3. `WatchClientTests`:
   - Failover to the alternate route when `resolvedBase` fails.

Use protocol-injected `URLSession` or a simple stub to avoid hitting the real Orin.

---

## 17. Low: Typo in Session Log Cursor

**File:** `ios/WaveCam/Sources/SessionLogView.swift`  
**Line:** `10`

```swift
@State private var sinCursor: Double = 0
```

This is almost certainly meant to be `sinceCursor`. Rename to avoid confusion.

---

## 18. Low: Stale Comment in `mediaList()`

**File:** `ios/WaveCam/Sources/WaveCamClient.swift`  
**Lines:** `1318–1325`

The comment mentions throwing a `WaveCamMediaListUnavailable` sentinel, but the code actually catches `WaveCamAPIError` with status code `503`. Update the comment to match the implementation.

---

## 19. Low: Watch Session Files Are Never Cleaned Up

**File:** `ios/WaveCam/Sources-Watch/WatchSessionRecorder.swift` (~L213)

### Problem

`tearDown()` transfers the file and sets `outputURL = nil`, but the JSONL file remains in the watch's Documents directory. Over many sessions this will consume storage.

### Fix

After confirming transfer success (or after a grace period), delete the local file. The cleanest place is in `WatchSessionDelegate` or by observing `WCSession.outstandingFileTransfers`:

```swift
private func cleanUpTransferredFile(_ url: URL) {
    DispatchQueue.global().asyncAfter(deadline: .now() + 5) {
        try? FileManager.default.removeItem(at: url)
    }
}
```

Call this from `tearDown()` after `transferFileToPhone(url)`.

---

## 20. Low: Hardcoded Team ID and Watch Version

**Files:** `ios/WaveCam/project.yml`, `ios/WaveCam/InfoWatch.plist`

### Problem

- `DEVELOPMENT_TEAM: "78725QX6PZ"` is committed in `project.yml`.
- `CFBundleShortVersionString` in `InfoWatch.plist` is hardcoded to `1.0` while the iOS target uses `$(MARKETING_VERSION)`.

### Fix

- Move the team ID to a user-specific `project.user.yml` or environment variable, or leave it but document that external contributors must override it.
- Replace the watch plist version string with `$(MARKETING_VERSION)` so it tracks the iOS app version.

---

## Additional Notes

### Liquid Glass Usage

`GlassEffectContainer` (iOS 26+) is a real SwiftUI API and is used correctly in `MergedLiveView.swift` with a pre-iOS 26 fallback. The fallback `GlassSurface` uses custom blur/material, which is appropriate for a deployment target of iOS 17. No changes required, but verify on a physical device with **Reduce Transparency** enabled to ensure the fallback remains legible in direct sunlight.

### Watch App Lifecycle

`WatchSessionRecorder` correctly uses `HKWorkoutSession` with `surfingSports` / `outdoor` to keep sensors alive while the wrist is down. The `InfoWatch.plist` includes the required `NSHealthShareUsageDescription`, `NSHealthUpdateUsageDescription`, and `WKBackgroundModes: workout-processing`. This matches the implementation.

### Backend Endpoint Parity

All iOS endpoints used by the app exist in `orin/wavecam/wavecam/control_api.py`:

- `GET /api/v1/status`, `/api/v1/config`, `/api/v1/preview.mjpeg`
- `POST /api/v1/safety/kill|resume`
- `POST /api/v1/ptz/velocity|stop|auto|home|zoom`
- `POST /api/v1/media/record/start|stop`
- `GET/POST /api/v1/calibration/*`, `/api/v1/presets/*`
- `GET /api/v1/health`, `/api/v1/events`, `/api/v1/logs`
- `POST /api/v1/sensors/phone`, `POST /api/v1/system/restart`

The only notable gap is `tracking.mode`, which the iOS UI expects but the backend does not yet expose (see #7).

---

## Recommended Fix Order

1. **#1 Optimistic KILL latch** — safety-critical; must be verified with tests.
2. **#3 MJPEG auth** and **#5 Watch auth** — without these, auth-enabled Orins are unusable for feed and wrist control.
3. **#2 Watch route failover** — improves command reliability.
4. **#4 Downloaded media temp dir** — prevents lost recordings.
5. **#6 Watch connection settings sync** — pairs with #2 and #5.
6. **#7 Tracking mode parity** — requires backend change or iOS UI cleanup.
7. **#16 Add tests** — start with #1 and #2.
8. Remaining low-priority UI/quality items (#8–#15, #17–#20).

---

## What Was Not Tested

- Physical device runtime (only simulator build).
- Actual Orin network calls.
- watchOS simulator runtime.
- HealthKit permission flows on real hardware.
- Liquid Glass rendering under Reduce Transparency / Increase Contrast.

**Next step awaiting your instructions.**
