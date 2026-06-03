# WaveCam iOS — Full Review Findings (2026-06-02)

**Scope:** read-only review of all 14 Swift files in `ios/WaveCam/Sources/` for logic/correctness bugs + UX issues.
**Method:** 4 parallel `code-reviewer` subagents, then **Claude verified every CRITICAL/HIGH against the actual code** before reporting. `✓` = Claude read-verified the line. Items without `✓` are credible review findings not independently line-checked.
**Routing:** `PTZView.swift` + `WaveCamClient.swift` are Codex's open claim (`claim_6dad5a4f6b`) → those are **[Codex]**; the rest are Claude-lane (no fixes applied — this is a review).

---

## HIGH

1. **`ContentView.swift:97-104` ✓ — "HOLD TO RESUME" is a single-tap `Button`, not a hold.** After an emergency STOP latch, an accidental tap calls `client.resume()` and un-latches motion; the label lies. **Fix:** gate resume behind `onLongPressGesture(minimumDuration: 1.5, ...)` with `onPressingChanged` progress feedback (per Apple docs; the bare `pressing:` variant is deprecated). Confidence: high.

2. **`WaveCamClient.swift:619` ✓ [Codex] — POST failover catches `URLError.timedOut`.** The comment (line 602) says "fail over only on connection errors," but `catch let error as URLError` also catches `.timedOut` — a timed-out KILL/restart/record may have reached the server yet gets **re-sent to the next host**. Benign for idempotent KILL on a single Orin; bad for `system/restart` / record toggles or a misconfigured second host. **Fix:** allowlist connection-error codes (`.cannotConnectToHost`, `.networkConnectionLost`, `.notConnectedToInternet`, `.cannotFindHost`); let `.timedOut` propagate. Confidence: high.

3. **`CalibrateView.swift:7,25` ✓ — wizard lets you skip capture + pre-marks steps done.** `canGoForward` (line 25) is a pure ID compare with no `capturedStepIDs` check → "Next" advances through Heading/Tilt/Zoom without capturing. Line 7 pre-marks Preflight+BaseLock "Done" with no server verification. Result: false "calibrated" confidence (degraded GPS aim later — not an uncontrolled-motion hazard, since KILL/owner govern motion). **Fix:** `canGoForward = activeStepID < dryRun.id && capturedStepIDs.contains(activeStepID)`; init `capturedStepIDs` empty (or derive Preflight/BaseLock from live `client.status`). Confidence: high.

## MEDIUM

4. **`EmergencyStopButton.swift:30-42` ✓ — `.compact`/`.prominent` lack `frame(minHeight: 44)`.** Compact ≈ 39-42pt (sub-HIG) for an emergency control. The always-on top-bar `.chip` IS correctly 44pt (line 53), so the persistent KILL is fine. **Fix:** add `.frame(minHeight: 44)` to the prominent/compact path. (Confirm `.compact` is actually instantiated.) Confidence: high.

5. **`KeychainStore.swift:68-71` ✓ — token lost if Keychain write fails during migration.** `save(...)` is `@discardableResult` and ignored; `defaults.removeObject` runs unconditionally → a failed `SecItemAdd` permanently drops the legacy token. **Fix:** `guard save(...) else { return }` before `removeObject`. Confidence: high. (Low blast radius — auth is default-off.)

6. **`WaveCamClient.swift` getWithFallback ~493-508 [Codex] — no HTTP-status check (unlike `post()` at line 615).** A GET returning 401/502 falls through to the next candidate or decodes a garbage/HTML body silently; auth failures become undiagnosable. **Fix:** mirror the `post()` status check into `getWithFallback`. Confidence: med (consistent with verified `post()`).

7. **`WaveCamClient.swift:~568` applyControlResponse [Codex] — silent `try?` decode returns `true`.** An unparseable control response is treated as success and **clears `lastControlError`**. **Fix:** don't clear the error / don't return success when the `ok` field can't be confirmed. Confidence: med.

8. **`PTZView.swift:120-129` [Codex] — `releaseManualPTZ` doesn't cancel `zoomRepeatTask` (QUESTION, not a confirmed bug).** `holdPTZ`/`startAutoPTZ` cancel it; joystick-release doesn't. **Likely intentional** (zoom is an independent control with its own stop). Real only if `ptzStop(hold:false)` ALSO stops zoom on the backend — then the 300ms zoom-repeat re-issues zoom after the stop. **Codex: confirm backend `ptz/stop` zoom semantics.** Confidence: med.

9. **`PTZView.swift` landscape (~line 23-68) [Codex] — Emergency Stop sits inside the landscape `ScrollView`.** On a short landscape screen (iPhone SE, 375pt) the right column can overflow and scroll the Stop button off. **Fix:** pin Emergency Stop outside the ScrollView / to the safe-area bottom. Confidence: med.

10. **`ConnectionView.swift:62-70` — URL validation accepts any scheme** (`ftp://`, `file://`). **Fix:** require `scheme == http|https`. Confidence: med.

11. **`TuneView.swift:183-203` ✓ — `load()` has no in-flight guard.** Tab re-entry fires `.task` again → concurrent `GET /config` calls race; last writer wins (possible flicker). **Fix:** `guard !loaded else { return }` or a `loading` flag. Confidence: high (mild impact).

12. **`CalibrateView.swift` `CalibrationButtonStyle` ~line 355 — Back/Next buttons <44pt** (~36pt; `ConnectionButtonStyle` correctly uses 44pt). **Fix:** add `.frame(minHeight: 44)`. Confidence: high.

13. **`LiveView.swift:~82` — subject-lock overlay only shows when the live feed is ABSENT.** With a live MJPEG feed the operator loses the in-video LOCKED/SEARCH indicator (only a 10pt tag remains). **Fix:** keep a prominent LOCKED chip over live video. Confidence: med (UX).

## LOW

14. **`AgentView.swift:48-50` — mock-mode summon reports `.requested` success** with no request sent (misleading "supervisor accepted"). Use a `.skipped("mock")` state. 
15. **`AgentView.swift:67` — inline `URLSession.shared` summon duplicates token logic;** move into `WaveCamClient`. 
16. **`DashView.swift:163-174` — `dashboardURL` hardcodes port 8088 + silently returns the API URL on `URLComponents` failure;** derive port from `baseURL`, set `path="/"`. `DashView.swift:39` `.clipShape(.rect(cornerRadius:0))` is a no-op (delete).
17. **`WaveCamClient.swift:29` [Codex] — `fallbackBaseURLs` is dead** (`compactMap(\.self)` on non-optional). Delete.
18. **`PTZView.swift:319 / :541 / :313` [Codex] — double `ptzOwnerLabel` (benign today); zoom label IN signed vs OUT unsigned; hyphen vs en-dash.** Cosmetic.
19. **`LiveView.swift:249` — MJPEG 2MB buffer wipe drops a partial frame without re-syncing to the last `FFD8`.** Acceptable recovery for the small 640×360 preview; refine by preserving from the last start marker. 
20. **`ContentView.swift:7` / `WaveCamApp.swift:47` — tab is a magic-int tag; legacy `192.168.` URL→WiFi heuristic is brittle.** Style/robustness.

---

## Dropped after verification (subagent over-ratings)
- **`WaveCamClient.swift:614` markConnected-before-status-check** — NOT a bug: HTTP errors throw `WaveCamAPIError` (not `URLError`), so no failover; marking a responding host connected is correct.
- **`LiveView.swift:131` updateUIView "reconnect storm"** — NOT a bug: `start()` guards `loadedURL != url`; stable `baseURL` → no churn.

## Strengths (genuine)
- One consolidated `EmergencyStopButton`; all styles call `kill()`; persistent KILL chip in the top bar across all tabs/orientations (44pt).
- MJPEG Coordinator self-heals (didComplete reconnect + 3s stall watchdog + dismantle cleanup); `[weak self]`/`[weak imageView]` correct.
- PTZ deadman: velocity-repeat + backend `deadman_ms:800` means motion needs continuous affirmation; `stopVelocityRepeat` called on all the right paths.
- POST failover intentionally does NOT fail over on HTTP errors (only the `.timedOut` leak in #2).
- Keychain for tokens (not @AppStorage); feature-detected Cinematic Zoom card; `KeychainStore.save` does update-then-insert.
