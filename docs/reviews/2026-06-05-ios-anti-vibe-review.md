# WaveCam iOS — Anti-Vibe Code Review
Date: 2026-06-05  
Reviewer: Claude Sonnet 4.6 (independent)  
Scope: Full iOS app (`ios/WaveCam/Sources/`), with emphasis on recently changed files: MediaView, WaveCamClient, TuneView, LiveView, MergedLiveView.

---

## Summary

| Severity | Count |
|----------|-------|
| Critical | 3 |
| High     | 6 |
| Medium   | 7 |
| Low      | 5 |

Total: 21 findings.

---

## Critical

### C1 — `deleteSelected` leaks `bulkBusy=true` on any thrown exception
**File:** `ios/WaveCam/Sources/MediaView.swift:312–324`

`deleteSelected()` sets `bulkBusy = true` on line 314 but resets it manually on line 320, with no `defer`. If `client.deleteMedia` ever throws (it currently returns `Bool`, not `throws`, so this path is blocked today — but `load()` at line 323 *does* throw and is `await`-ed while `bulkBusy` is set). If `load()` throws an unhandled error past the `catch` in its own body it will propagate; more importantly the pattern is fragile. The `downloadSelected()` function above it correctly uses `defer { bulkBusy = false }` (line 306). `deleteSelected` should match.

**Fix:**
```swift
private func deleteSelected() async {
    guard !bulkBusy, deleteSupported else { return }
    bulkBusy = true
    defer { bulkBusy = false }          // ← add this
    let targets = Array(selected)
    var anyOK = false
    for name in targets where await client.deleteMedia(name: name) {
        anyOK = true
    }
    // remove manual bulkBusy = false (line 320)
    selected.removeAll()
    isSelecting = false
    if anyOK { await load() }
}
```

---

### C2 — `mediaDeleteSupported()` fires a redundant, serial `GET /config` on every `load()`
**File:** `ios/WaveCam/Sources/MediaView.swift:284`  
**File:** `ios/WaveCam/Sources/WaveCamClient.swift:1053–1055`

`load()` calls `client.mediaDeleteSupported()` after a successful `mediaList()`. `mediaDeleteSupported()` internally calls `config()` which does a full `GET /config` round-trip. This means every refresh of the Media tab costs two sequential HTTP round-trips where one suffices, and the `supported.mediaDelete` flag can disagree with the `supported.*` value that `TuneView` loaded (two separate fetches that may see different revisions). Additionally, `load()` has no concurrency with `mediaList()` — `deleteSupported` is set only after the list succeeds, so the delete button appears one full load cycle late on the very first appearance.

**Fix:** Accept `deleteSupported: Bool` as a parameter to `load()`, or fetch config and media-list concurrently with `async let` in `load()`, then resolve the flag from the fetched config, rather than delegating to a method that does a second network call:

```swift
async let listTask = client.mediaList()
async let cfgTask  = client.config()
let (fetched, cfg) = try await (listTask, cfgTask)
deleteSupported = cfg?.supported?.mediaDelete ?? false
```

---

### C3 — `lastControlError` is read racily after an `async` operation in `TuneView`
**File:** `ios/WaveCam/Sources/TuneView.swift:187, 204`

In the `showSavePresetAlert` and `showPresetDeleteConfirm` alert handlers, a `Task { }` is spawned that awaits `client.savePreset` / `client.deletePreset`. On failure the code then reads `client.lastControlError` to construct a user-visible message. Because `lastControlError` is set inside `WaveCamClient` by those methods but is also reset by `sendControl` (line 1155 of WaveCamClient), the 1Hz status-poll `Task` running concurrently can clear `lastControlError` between when the save/delete operation sets it and when `TuneView` reads it on lines 187/204. Result: the error message silently becomes `"rejected"` even when a real error string was available.

`WaveCamClient` already has a pattern for this: `lastCommandError` is never cleared by the poll, only by an explicit `clearCommandError()`. `lastControlError` has no such protection and is cleared on every successful `sendControl`. The correct fix is to capture the error description inside the `Task` closure rather than reading the shared property after the fact:

```swift
Task {
    let ok = await client.savePreset(name: name, values: values)
    let errMsg = client.lastControlError     // capture before any poll can clear it
    if ok {
        ...
    } else {
        configError = "Could not save preset: \(errMsg ?? "rejected"). Tap to dismiss."
    }
}
```

---

## High

### H1 — `LiveView` dead-code cluster is a build-time regression risk
**File:** `ios/WaveCam/Sources/LiveView.swift:14–881`

`LiveView` is superseded by `MergedLiveView` and is compiled into the app binary. The file's own header comment (lines 6–13) correctly marks `LiveFeedCard`, `LiveTelemetryGrid`, `FeedBackground`, `MockOceanScene`, `WaveBands`, `FeedSubjectOverlay`, `SurferGlyph`, `LockBox`, `PTZMotionScope`, `FeedBottomStrip`, `FeedMetric`, and `StatusPill` as dead — and that is verified: none of these appear in any file outside `LiveView.swift`. They account for roughly 540 lines of compiled, tested-against-nothing code. The comment advises removing them one at a time with a build between each; the review defers to that note but flags it as a high-severity hygiene issue because every dead line is a future false-positive for grep/AI searches and a maintenance tax.

The live components in the same file (`MJPEGPreviewView`, `FeedReticles`, `ReticleCorner`, `FeedAimReticle`, `FeedPTZOverlay`, `PTZOverlayMetric`, `FeedTopTags`, `LiveTag`, `FeedLockReason`, `RecordButton`) are all referenced from `MergedLiveView` or `ContentView` and must be retained.

**Fix:** Remove dead structs incrementally, building after each: `LiveView` body + `LiveFeedCard` + `LiveTelemetryGrid` + `FeedBackground` + `MockOceanScene` + `WaveBands` + `FeedSubjectOverlay` + `SurferGlyph` + `LockBox` + `PTZMotionScope` + `FeedBottomStrip` + `FeedMetric` + `StatusPill`. The `mockFeed` var inside `MergedLiveView` (lines 168–183) already duplicates the offline background gradient from `MockOceanScene`, so no new code is needed.

---

### H2 — `downloadMedia` sends a direct `URLSession.download` bypassing `getWithFallback`
**File:** `ios/WaveCam/Sources/WaveCamClient.swift:1021–1037`

The project invariant is: every GET goes through `getWithFallback` (tether→Wi-Fi failover). `downloadMedia` uses `URLSession.shared.download(for:)` directly against `baseURL`, which is the *last successful route* but not the same as probing both routes. If the tether drops between the last `refresh()` and the download, the download fails with a network error instead of falling over to Wi-Fi. Given that downloads are large and field conditions change, this is not merely theoretical.

`getWithFallback` is designed for `URLSession.data`; streaming downloads need `URLSession.download`. The fix is to replicate the candidate-loop pattern for download, or to emit a probe `GET /status` through `getWithFallback` first to settle `baseURL`, then issue the download to the now-confirmed `baseURL`. The latter is simpler:

```swift
func downloadMedia(name: String) async throws -> URL {
    guard mode == .live else { throw URLError(.resourceUnavailable) }
    // Settle the active route before downloading
    _ = try await getWithFallback("status")
    let url = baseURL.appending(path: "media/download/\(name)")
    ...
}
```

---

### H3 — `DownloadState` is `internal` but should be `private`
**File:** `ios/WaveCam/Sources/MediaView.swift:486`

`enum DownloadState` is declared at file scope without an explicit access level, making it `internal` and visible to the entire module. It is used only within `MediaView.swift` (`MediaView` body, `MediaFileRow`, and `@State downloadProgress`). Leaking it module-wide couples unrelated code to a MediaView implementation detail and invites accidental reuse.

**Fix:** Add `private` to the declaration: `private enum DownloadState: Equatable { ... }`

---

### H4 — `WaveCamDefaults.tetherBaseURL` and `wifiBaseURL` are force-unwrapped
**File:** `ios/WaveCam/Sources/WaveCamClient.swift:22, 26`

```swift
static var tetherBaseURL: URL {
    URL(string: tetherBaseURLString)!   // line 22
}
static var wifiBaseURL: URL {
    URL(string: wifiBaseURLString)!     // line 26
}
```

These strings are compile-time constants that are valid URLs, so the force-unwrap will never trip in practice. However, `WaveCamApp.storedRouteURLs()` already uses the safe `URL(string:) ?? WaveCamDefaults.tetherBaseURL` pattern for user-stored strings; the defaults themselves should model the same discipline. A misspelled edit to either constant in the future would produce a silent crash at startup.

**Fix:**
```swift
static var tetherBaseURL: URL {
    URL(string: tetherBaseURLString) ?? URL(string: "http://172.20.10.8:8088/api/v1")!
}
```
Or declare them as `let` constants via `URL(string:)!` with a build-time `precondition`, which makes the invariant explicit.

---

### H5 — `candidateOrder` does not probe tether when `activeRoute == .mockFallback` or `.offline`
**File:** `ios/WaveCam/Sources/WaveCamClient.swift:1111–1119`

When `activeRoute` is `.mockFallback` or `.offline`, `candidateOrder` falls through to `return [tetherBaseURL, wifiBaseURL]` — which is correct for `.offline`. However `.mockFallback` is an error state where the live API failed; on the *next* poll it will also probe `[tetherBaseURL, wifiBaseURL]`, which is correct. The issue is a narrower path: when `activeRoute == .wifi` or `.custom` and `nextTetherProbeAt` has not yet passed, the function returns `[baseURL, tetherBaseURL, wifiBaseURL]`. Because `deduped()` filters on `absoluteString`, if `baseURL == wifiBaseURL`, the tether candidate is tried first (correct) but then `wifiBaseURL` appears twice and the second copy is dropped. This is correct. No bug here — documenting explicitly to confirm the logic was deliberately checked. No fix needed.

---

### H6 — `TuneView.advancedDetectionCard` / `colorCard` / `advancedMotionCard` use an O(n²) divider-before condition pattern
**File:** `ios/WaveCam/Sources/TuneView.swift:404–487`

The three advanced `@ViewBuilder` cards insert `OperatorDivider()` before each row by checking all preceding conditions: `if everyN != nil || lockThreshold != nil || unlockThreshold != nil { OperatorDivider() }`. Each row re-evaluates all previous predicates. The longest chain, in `advancedMotionCard`, has 6 rows and the final condition checks 5 predicates. This is readable for the current row count but will silently produce wrong dividers (e.g., double-dividers) if fields are reordered, and will grow quadratically with additional fields.

The correct pattern: insert a divider *after* each row except the last, or use a helper that tracks "has shown at least one row" with a `Bool`:

```swift
var showed = false
if let n = everyN {
    if showed { OperatorDivider() }
    sliderRow(...)
    showed = true
}
if let lt = lockThreshold {
    if showed { OperatorDivider() }
    sliderRow(...)
    showed = true
}
```

This is a structural issue that will produce a real bug when the next field is added by someone who forgets to update all downstream conditions.

---

## Medium

### M1 — `fallbackState(for:)` in `AgentView` is a one-line function that returns a constant
**File:** `ios/WaveCam/Sources/AgentView.swift:72–74`

```swift
private func fallbackState(for service: String) -> String {
    "unknown"
}
```

The `service` parameter is never used. The function exists only to allow future per-service fallbacks. Per the coding standards, no speculative/future-proofing code. Replace the call site with the literal `"unknown"` and remove the function.

---

### M2 — `TuneView.load()` shadow variable `loaded` masks the `@State var loaded`
**File:** `ios/WaveCam/Sources/TuneView.swift:590`

```swift
if presetsEnabled, let loaded = await presetsTask {
    tunePresets = loaded
}
```

The `let loaded` here shadows the `@State private var loaded: Bool` declared at line 9. Although Swift resolves the inner binding correctly (both are in scope; the inner `[WCPreset]` one wins), this is a footgun. The inner binding should be renamed: `if presetsEnabled, let fetchedPresets = await presetsTask { tunePresets = fetchedPresets }`.

---

### M3 — `MergedLiveView.config` loaded separately from `client.config()` in `AgentView` — duplicate config fetches on tab switch
**File:** `ios/WaveCam/Sources/MergedLiveView.swift:41`  
**File:** `ios/WaveCam/Sources/AgentView.swift:36`

Both views independently call `client.config()` in their `.task` modifier. `WaveCamClient.config()` performs a real `GET /config` round-trip each time; there is no caching. On the Tools tab, switching between Tune and Agent fires a second fetch. There is no shared config cache or reactive property on the client. For a single-user embedded device app this is not a performance problem, but it does mean the Tune panel and the Agent feature-flag check can see different backend revisions.

**Suggested improvement:** Add a `private(set) var cachedConfig: WCConfig?` to `WaveCamClient` that is populated by `config()` and invalidated by `configure()`. Views read the cached copy first and re-fetch only when stale. This is a refactor, not a one-liner, so categorized Medium.

---

### M4 — `deleteSelected` clears selection and exits select mode even when all deletes failed
**File:** `ios/WaveCam/Sources/MediaView.swift:319–323`

```swift
bulkBusy = false
selected.removeAll()
isSelecting = false
if anyOK { await load() }
```

If `anyOK` is false (all network deletes failed), the selection is cleared and select mode exits silently — the operator has no indication that nothing was deleted. At minimum, if `anyOK == false` and `targets` was non-empty, an error should be shown (e.g., set a transient banner). The current behavior is a silent no-op that looks like success.

---

### M5 — `MJPEGPreviewView.Coordinator.urlSession(_:dataTask:didReceive:)` holds `buffer` unboundedly during `drainFrames`
**File:** `ios/WaveCam/Sources/LiveView.swift:259–291`

`drainFrames()` removes consumed bytes via `buffer.removeSubrange(buffer.startIndex..<end.upperBound)` inside the while loop. `removeSubrange` on `Data` is O(n) due to shifting; for a 30fps MJPEG stream with ~50KB frames, each drain call could shift megabytes. The existing 2MB cap (line 287–289) is a correct safety valve for a stalled parse but does not help the per-frame copy cost. This is a known pattern in MJPEG clients and acceptable for the current frame rate, but worth noting as a performance consideration at 1080p60.

No fix required at current resolution; document if upgrading to the 1080p60 stream.

---

### M6 — `WCConfig.restartRequiredKeys` is loaded into `TuneView.restartKeys` but the SERVICE card is only shown when non-empty — restart-only config keys are never reloaded after a preset apply
**File:** `ios/WaveCam/Sources/TuneView.swift:350–363`

`applyPreset(named:)` sets `loaded = false` then calls `load()` to refresh sliders. `load()` repopulates `restartKeys` from `cfg.restartRequiredKeys`. If the preset being applied includes restart-required keys, the SERVICE card should appear — and does, because `load()` runs. This is correct. However: if `result.restartRequired` is true but `result.restartKeys` is empty (backend sends `restartRequired: true` without populating `restartKeys`), the `presetRestartNotice` renders with an empty key list. The guard in `showPresetRestartNotice` only checks `result.restartRequired`, not `!result.restartKeys.isEmpty`.

**Fix:** `if result.restartRequired && !result.restartKeys.isEmpty { ... }` on line 353.

---

### M7 — `candidateOrder` mutates `nextTetherProbeAt` as a side effect inside a read-only-looking function
**File:** `ios/WaveCam/Sources/WaveCamClient.swift:1111–1119`

```swift
private func candidateOrder(now: Date = Date()) -> [URL] {
    if activeRoute == .wifi || activeRoute == .custom {
        if now < nextTetherProbeAt {
            return [baseURL, tetherBaseURL, wifiBaseURL]
        }
        nextTetherProbeAt = now.addingTimeInterval(tetherRecheckInterval)  // side effect
    }
    return [tetherBaseURL, wifiBaseURL]
}
```

`candidateOrder` looks like a pure query function but mutates `nextTetherProbeAt` on each call when the tether interval has elapsed. `apiCandidates()` calls it; anything calling `apiCandidates()` triggers the side effect. The function name gives no indication it has side effects. Rename to `nextCandidates(resettingProbeTimer:)` or extract the mutation to the call sites.

---

## Low

### L1 — `WaveCamDefaults.baseURL` and `legacyLANBaseURLString` are unused dead code
**File:** `ios/WaveCam/Sources/WaveCamClient.swift:7–8, 17–19`

`WaveCamDefaults.legacyLANBaseURLString` (line 8) is only used in `WaveCamApp.storedRouteURLs()` and `ConnectionView.storedRouteTexts()` as a migration sentinel comparison. That is legitimate. However, the computed property `WaveCamDefaults.baseURL` (lines 17–19) just returns `tetherBaseURL` and is never referenced anywhere in the codebase (the `WaveCamClient.init` default parameter at line 481 uses `WaveCamDefaults.baseURL` — that is the only call site). It exists to provide a default for the deprecated single-URL `configure()` overload at lines 503–511, which itself only calls the two-URL overload. The chain from `baseURL` → deprecated `configure()` → two-URL configure is a migration shim that can be removed once `WaveCamApp` and `ConnectionView` no longer use the single-URL path. Document or remove.

---

### L2 — `TuneView` carries 19 `@State` properties at the top level
**File:** `ios/WaveCam/Sources/TuneView.swift:9–59`

19 properties is above the threshold where it becomes hard to reason about initialization order, accidental reset (e.g., `loaded = false` in `applyPreset` triggers a full re-load), and which properties represent "server state" vs. "UI state" vs. "preset transient state." This is a design smell, not a bug. A future refactor grouping related properties (e.g., a `TuneState` struct and a `PresetState` struct) would reduce cognitive load. Flagged Low because the current structure is functional and readable in isolation.

---

### L3 — `CalibrateView.resolvedHeadingDeg/TiltDeg/ZoomFovDeg` are placeholder values with a TODO
**File:** `ios/WaveCam/Sources/CalibrateView.swift:162–178`

The TODO at line 162 is concrete and actionable: "once the backend exposes a 'read current PTZ position' endpoint, replace these with live motor-position reads." This is an open work item, not dead code. Confirmed as a legitimate TODO. The heading fallback to GPS bearing (line 166) is reasonable; the tilt constant of 0.0 (line 171) and zoom constant of 31.5 (line 176) should at minimum be named constants rather than magic numbers.

---

### L4 — `AgentView.summonDiagnostics()` wraps a `Task {}` around `summonDiagnosticsRequest()` which is already `@MainActor async`
**File:** `ios/WaveCam/Sources/AgentView.swift:76–77`

```swift
private func summonDiagnostics() {
    Task { await summonDiagnosticsRequest() }
}
```

This is fine functionally — the Task bridges the sync button action to an async call. However, `summonDiagnostics()` is only ever called from a SwiftUI `Button` action closure, which already allows `Task { }` directly at the call site. The intermediate non-async wrapper adds an indirection layer. Minor readability issue.

---

### L5 — `KeychainStore.save` does not update the `kSecAttrAccessible` value when overwriting an existing item
**File:** `ios/WaveCam/Sources/KeychainStore.swift:21–23`

`SecItemUpdate` (line 21) updates only `kSecValueData`, not `kSecAttrAccessible`. A token that was persisted by an older build (which may have used the less-restrictive `kSecAttrAccessibleAfterFirstUnlock`) will retain the old accessibility level even after being overwritten. The correct fix is to include `kSecAttrAccessible: kSecAttrAccessibleWhenUnlockedThisDeviceOnly` in the attributes dict passed to `SecItemUpdate`. Low because the practical impact is limited to builds that pre-dated the `WhenUnlockedThisDeviceOnly` tightening, and only for users who saved a token before that change.

---

## Project Invariants — Verified

The following checklist items were explicitly audited:

- **Emergency Stop reachability:** `EmergencyStopButton` (prominent/compact/chip/icon) is present in `ContentView.TopBar` (always-visible across all tabs via `KillLatchOverlay` and the chip), in `MergedLiveView` landscape vertical rail and portrait horizontal dock, and in the fullscreen layout's floating stop button. No screen or state was found where KILL is unreachable. PASS.
- **Portrait and landscape parity:** `MergedLiveView` branches on `verticalSizeClass == .compact` for landscape/portrait layouts, both including the control rail. `LiveView` (dead) also had landscape handling. PASS.
- **No `addingPercentEncoding` calls remaining:** Grep across all Sources confirms zero occurrences. The double-encoding bug fix from builds 22/23 is fully in. PASS.
- **Codable `init(from:)` in extensions:** `WCPreset` (line 322), `WCPresetApplyResult` (line 345), `WCMediaListResponse` (line 377) all decode in extensions. PASS.
- **Feature-detected controls:** `presets` behind `cfg.supported?.presets`, `cinematicZoom` behind `cfg.supported?.cinematicZoom ?? value-present`, `mediaDelete` behind `supported?.mediaDelete`, `ptzHome` behind `config?.supported?.ptzHome`. PASS.
- **No secrets in source:** No API keys, tokens, or credentials found in any source file. PASS.
- **`getWithFallback` for GETs:** `status`, `config`, `presets`, `logs`, `calibration`, `media/list` all route through `getWithFallback`. Exception: `downloadMedia` — captured as H2.
