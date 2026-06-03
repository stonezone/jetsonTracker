# WaveCam — Multi-Agent Blind Review Synthesis (2026-06-03)

**Peers:** Claude Opus, Gemini, Codex, Kimi — each read the real repo files read-only (blind: no prior conclusions shared). **DeepSeek excluded** (API key 401). Plus Claude's own prior reviews.
**Validation:** Claude validated cited lines against the current files; peer line numbers occasionally drifted (noted where corrected). Corroboration count = how many independent peers raised it.

## Dominant theme (the high-value signal)
Every peer independently converged on **command / STOP reliability over lossy VISCA-UDP + Wi-Fi**, and **the operator can't always tell the true state**. Those two clusters are where the real risk is.

---

## CRITICAL

**C1 — Backend de-dupes STOP commands → a lost UDP stop leaves the camera moving.** `orin/wavecam/wavecam/pipeline.py` `_send_cmd`/`_send_zoom` cache the last key and suppress repeats; once STOP is cached, later STOPs are dropped. VISCA/UDP is fire-and-forget. **Corroborated: Codex, Kimi.** Lane: **Codex**. Fix: never de-dupe a safety stop; re-send STOP on a bounded interval (~250ms) while the computed command stays STOP, on KILL/release/deadman.

**C2 — POST failover double-applies on `.networkConnectionLost`.** `WaveCamClient.swift` `isWriteRouteFailoverAllowed` (~:652) includes `.networkConnectionLost`/`.notConnectedToInternet`, which can fire *after* the server received the command → the retry re-applies it on the other host. `kill`/`velocity` are idempotent, but `media/record/*` and `config/hot` are not. **Corroborated: Opus, Codex, Gemini, Kimi (4×).** Lane: **Claude**. Fix: writes fail over only on pre-connection errors (`cannotConnectToHost`/`cannotFindHost`/`dnsLookupFailed`); reads keep the timeout/connection set. (Refines f21c8b9.)

**C3 — iOS PTZ in-flight commands aren't sequenced / stop isn't retried.** `PTZView.swift` joystick/zoom each spawn independent POST tasks; release/`holdPTZ` send a single `ptzStop` with no retry and reset the UI immediately. A stale nonzero command can land *after* release, or a dropped stop coasts until the 800ms backend deadman. **Corroborated: Codex (critical), Gemini, Kimi.** Lane: **Claude**. Fix: single command queue with a generation/sequence number so stop fences older commands; keep sending `velocity=0` until confirmed (or short stop-burst).

**C4 — PtzOwner + deadman mutate across threads with no lock.** `ptz_owner.py` + `control_api.py` deadman/owner are touched by the pipeline thread, FastAPI worker threads, and `threading.Timer` callbacks; `_lock` guards only revision/restart. A stale expired timer can release a newer manual owner or restore automation at the wrong time; KILL latch can be read torn. **Corroborated: Opus, Codex, Kimi (3×).** Lane: **Codex**. Fix: one lock around all owner/deadman/restore transitions + a monotonic generation id timers check before acting.

---

## HIGH

**H1 — Calibrate wizard is a backend no-op.** `CalibrateView.swift` `captureActiveStep` only mutates local `@State`; `control_api.py` has **no `/calibration` endpoints**. Operator sees green "DONE" but nothing was solved on the rig (heading/tilt/FOV) — false confidence that will break GPS pointing later. **Corroborated: Opus, Gemini, Kimi (3×).** Lane: **joint** (Codex adds endpoints; Claude wires them) **or** relabel as a read-only checklist. (My earlier capture-gating fix prevents *skipping* but doesn't make it *do* anything.)

**H2 — Mock-fallback is visually indistinguishable from live.** With `mockFallbackEnabled`, an API outage shows fake perfect tracking + identical telemetry; only a tiny route chip says MOCK. Operator could believe it's tracking/recording while fully offline. **Corroborated: Gemini, Kimi (2×).** Lane: **Claude**. Fix: persistent high-contrast "OFFLINE — MOCK DATA" banner across all tabs when `activeRoute == .mockFallback` (or drop mock-fallback from device builds).

**H3 — Legacy `/resume` reclaims the testbed owner (auto-restarts motion).** `web.py`/`pipeline.kill(False)` vs `control_api.py resume_without_autostart`. A legacy client can clear KILL and restart autonomous movement without explicit Start Auto. **Corroborated: Opus, Codex (2×).** Lane: **Codex**. Fix: legacy `/resume` → the no-autostart path, or remove legacy mutation routes.

**H4 — Legacy zoom endpoints bypass owner-gating + deadman.** `web.py` zoom routes call `pipeline.ptz.zoom()` directly. Codex. Lane: **Codex**.

**H5 — PTZ & Tune tabs have no live video.** Operator can't see the feed while nudging the joystick or while changing color/require-person/motion. Filming alone, this makes manual framing + tuning practically blind. **Corroborated: Kimi (both tabs).** Lane: **Claude**. → directly supports the **Live+PTZ merge + preview-in-Tune** Zack already wants.

**H6 — MJPEG coordinator data races.** `LiveView.swift` `Coordinator.stop()` (main) mutates `buffer`/`task`/`session`/`loadedURL` while the URLSession delegate (bg queue) appends to `buffer`/reads `loadedURL`. Risk: torn buffer / use-after-free / leaked stream. Kimi. Lane: **Claude**. Fix: serialize coordinator state on a private serial queue; hop to main only for `imageView.image`.

**H7 — TuneView swallows hot-config errors.** `TuneView.send` fires `configHot` but never surfaces `lastControlError`; the operator on Tune never sees a rejected setting. Kimi. Lane: **Claude**. Fix: error banner in TuneView bound to `lastControlError`.

**H8 — Zoom-release cancels the pan/tilt deadman.** `control_api.py ptz_zoom` when `value==0` unconditionally cancels the manual deadman + releases the manual owner — even if pan/tilt is still active → camera could pan unbounded if the network drops. Gemini. Lane: **Codex**. Fix: only release the manual owner when pan AND tilt are also zero.

---

## MEDIUM / LOW (condensed)
- **M — iOS `sendControl` eager `refresh()` blocks ~3s offline** before a command POSTs (Kimi). Lane: Claude. Fix: skip the pre-flight refresh.
- **M — AgentView uses raw `URLSession`**, bypassing failover/token (Kimi, Opus). Lane: Claude. Route through `WaveCamClient`.
- **M — TuneView hardcodes presets/classes** instead of `WCConfig.supported` (Kimi). Lane: Claude. (Pairs with the Tune consolidation.)
- **M — Cinematic suppression returns without sending zoom-stop** → auto-zoom keeps driving through the manual-override window (Kimi). Lane: Codex.
- **M — status snapshot reads racy owner state** (Kimi). Lane: Codex (covered by C4's lock).
- **M — no optimistic KILL feedback** (1–3s before the latch overlay shows; operators mash) (Gemini). Lane: Claude. Fix: local optimistic latch on `kill()`.
- **M — Emergency Stop + Record adjacent** in portrait → fat-finger (Kimi). Lane: Claude. Separate them.
- **M — joystick deadzone not drawn** (Kimi); **no haptics** on KILL/lock/refusal (Kimi); **no per-route reachability test** on Connect (Kimi, Codex). Lane: Claude.
- **M — after Resume, no Start-Auto affordance on Live** → camera sits idle, operator thinks it's tracking (Codex). Lane: Claude.
- **L — redundant manual Refresh buttons** (1Hz poll already refreshes) (Codex). **L — VISCA inquiry not atomic** (Kimi, Codex). **L — `candidateOrder` mutates probe state as a side effect** (Kimi, Codex). **L — Web dashboard webview carries no bearer token** (Kimi). **L — auth fail-open + non-constant-time token compare** (Codex; documented LAN tradeoff). **L — dead `build_network` fields** (Codex).

---

## Strengths (peer-confirmed)
- **Safety core is well-modeled + tested:** `PtzOwner` is pure/unit-tested; KILL cancels both deadmans + forces stop; two-phase hot-config validates before mutating; KILL always reachable (top-bar chip + full-screen overlay) and Resume needs a deliberate ~1.2s hold. (Opus, Codex, Kimi)
- **Failover respects idempotency** (no retry on a reached host's HTTP error; read/write predicates split) — the one gap is C2. (Opus, Kimi)
- **`FeedLockReason` HUD** translates real `hasColor`/`hasPerson` into plain English and stays silent when unreported — won't fake a lock. (Opus, Kimi) ← the clarity fix from earlier today.
- **MJPEG self-healing** (stall watchdog + backoff reconnect); **fusion EMA temporal continuity** prevents target-snapping. (Kimi)

---

## Lane split
- **Claude (iOS):** C2, C3, H2, H5, H6, H7, + the MEDIUMs (eager-refresh, AgentView transport, dynamic presets, optimistic KILL, Stop/Record spacing, deadzone, haptics, Connect route-test, Live Start-Auto affordance).
- **Codex (backend):** C1, C4, H3, H4, H8, cinematic-suppress-stop, snapshot lock, VISCA atomicity, auth notes.
- **Joint:** H1 calibration (backend endpoints + iOS wiring, or relabel).

## Coverage / exposure
- 4 of 5 peers ran; DeepSeek excluded (key 401). All read the real repo read-only. External transmission: Gemini/Codex/Kimi are external services that read the repo (no plaintext secrets present — the leaked credential was already redacted; `.agent-collab`/keys not in scope). Opus is Anthropic-family.
- Not covered: runtime/behavioral testing (all source-read); the `:8088` web HTML UI; nucleo firmware.

## Bottom line
The architecture's safety *intent* is sound and was praised, but **delivery reliability of stop/commands over lossy links** (C1–C4, H8) is the real exposure, and **state honesty** (H1 calibrate no-op, H2 mock-fallback, H5 no-video-on-control) is the real UX risk. None of this is a rewrite — they're targeted fixes, split cleanly by lane.
