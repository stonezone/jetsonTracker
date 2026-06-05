# WaveCam — Review-Fixes Plan (2026-06-04)

Addresses **all High + Medium findings** from the iOS code review
(`2026-06-03-...` review; H1–H4 + Mediums). Lane-split **Claude (iOS)** /
**Codex (backend enablers)**. Executes **after** the `ios-review-fixes-20260603 → main`
merge lands (Codex resolves + Claude reviews → Codex merges). Fixes land on a fresh
branch off the merged `main`.

## Phase 0 — gate
- Merge `ios-review-fixes-20260603 → main` (Codex resolves the 3 backend conflicts using
  the precise map; Claude reviews iOS/docs + `/guide` + backend union + tests; Codex merges).

## Claude — iOS lane
| # | Finding | File:line | Fix |
|---|---|---|---|
| 1 | **H1** KILL overlay sticks on failed kill (safety) | `WaveCamClient.swift:496/537` | Reconcile `optimisticKilled` vs fresh status; clear on confirmed-not-killed. **First.** |
| 2 | **Dead-code sweep** (~480 LOC) | DashView 293 / LiveView ~80 / GlassSection+GlassRow 29 / PTZView 156 | Delete DashView, LiveView scaffolding, Glass\*; **rename PTZView→JoystickPad**; remove dead vars (`presetApplyRestartRequired`, `fallbackState`, `isFullscreen` param, `shortStatus`). |
| 3 | **H2** `logs()` skips route fallback | `WaveCamClient.swift:903` | Route through `getWithFallback()`. |
| 4 | **H4** non-optional Codable throws on partial responses | `WCPresetApplyResult.restartRequired/restartKeys`, `WCPreset.builtin`, `WCMediaListResponse.files` | Make optional with defaults. |
| 5 | **H3** `configHot` takes `[String: Any]` | `TuneView.swift:621` | Narrow to `[String: JSONValue]`. |
| 6 | **Dedup** | GlassLockChip/FeedLockReason, AgentMetric/CalibrationMetric, 3× `captureCalibration*`, URL-migration shim (WaveCamApp & ConnectionView) | Collapse to one source each. |
| 7 | `applyControlResponse` treats absent `ok` as failure | `WaveCamClient.swift:1121` | Treat absent `ok` as success-unless-explicit-false. |
| 8 | `KeychainStore` accessibility | `:27` | `kSecAttrAccessibleAfterFirstUnlock` → `…WhenUnlockedThisDeviceOnly`. |
| 9 | Cinematic-zoom feature-detect (iOS side) | `TuneView.swift:599` | Switch to `supported.cinematic_zoom` flag — **pairs with Codex C1 below.** |

## Codex — backend lane (enablers + parallel)
| # | Task | Why |
|---|---|---|
| C1 | Add `supported.cinematic_zoom` (+ audit any missing `supported.*`) to `GET /config` | Lets iOS feature-detect cleanly instead of value-present (Claude #9). |
| C2 | Confirm/guarantee `/logs`, `/presets`, `/media` always send the fields iOS decodes | Belt for H4 (Claude #4). |
| C3 | *(if Zack wants)* multi-agent review's open backend criticals: **C1 STOP re-send on interval**, **C4 owner/deadman thread-lock** | Command/STOP reliability over lossy UDP. |

## Sequence
1. Phase 0 merge lands.
2. Claude branch off `main`: #1 (safety) → #2 (dead code) → #3+#4 → #6 dedup → #5+#7+#8. Build to device per milestone.
3. Codex parallel: C1 + C2 (C3 if approved).
4. Claude #9 once Codex C1 is live (feature-detect).
5. Both log progress on the bus; cross-review (Claude reviews Codex backend, Codex can review Claude iOS).

## Out of scope (Low — deferred, do when the file next needs a touch)
What-narrating comments (MergedLiveView), magic numbers → `WC.*` tokens,
`autonomousPTZOwners` typed `String` (`WaveCamClient.swift:30`),
`WaveCamClient` 1,247-LOC monolith split.
