# Next full-review kickoff prompt (for a fresh Fable session)

Paste the block below into a new chat to pull latest `main` and run a full adversarial
review + fix pass. Written to focus on the changes that most need scrutiny and to avoid
input-classifier false positives (defensive framing, personal device, no security-hardening focus).

> **Why this exists:** the previous session implemented a two-round audit (57 findings, then 22
> re-review findings) via sonnet subagents and merged it to `main` (PR #116) plus an additive
> estimator helper (#102). The round-2 fixes and the merges were **never independently re-audited**
> (only unit-tested + spot-checked), and **nothing was verified on the Mac or the Jetson/camera** —
> all "green" is CI + local pytest. Everything below is a candidate for being wrong.

---

```
This is my personal vision-based auto-filming PTZ camera project (WaveCam / jetsonTracker) — a hobby rig that films me foiling. Pull the latest `main` (it was just heavily updated) and do a full, adversarial code review. Skip security/auth hardening entirely — it's my own device on my own LAN; focus on correctness, tracking effectiveness at 50–300 m, performance (30+ FPS), and robustness.

Context you need: over the previous session a two-round audit (57 findings, then 22 re-review findings) was implemented by sonnet subagents and merged to main via PR #116, plus an additive estimator helper (#102). Two things make this review important: (a) the round-2 fixes and the merges were NEVER independently re-audited — only unit-tested and spot-checked; (b) NOTHING was verified on the actual Jetson/camera or built on a Mac — all "green" is CI + local pytest only.

Prioritize scrutinizing these specific, higher-risk changes now in main (verify each against the real code and the tracking mission; assume they may be wrong):

BACKEND (orin/wavecam/wavecam/):
- pipeline.py R1: GPS pointing "lead_s" is frozen per fix.ts. It deliberately does NOT restore a command_min_interval floor on changed sends. Verify a moving subject doesn't re-starve the PointingVerifier or spam pan_tilt_absolute at frame rate.
- controller.py R2: FOV-scaled deadzone = min(cfg.deadzone/fov_scale, 0.25). The 0.25 cap is a hand-picked constant — verify the servo actually centers the subject at full 20x tele and doesn't hunt.
- gps_direct_lora.py R6: get_camera_position() was reverted to the settled MEAN; a new get_camera_position_raw() feeds ONLY the drift monitor. Verify every consumer (calibration base-lock, pointing fallback, snapshots) gets the right one and tripod-bump detection still works.
- control_ptz.py R10: _bump_revision() was moved outside the ptz lock in the manual/zoom deadman callbacks to break an ABBA deadlock that could wedge KILL. Verify the deadlock is truly gone AND no new race was introduced. Also: the identical nesting in control_calibration.py (session-start takeover) was only documented, NOT restructured — check whether it still deadlocks.
- agent_session.py R11 (bounded reap after timeout) and R14 (per-provider lock); recorder.py R13 (new lock + identity-check on stop) and R22 (start() now returns ok:false on instant ffmpeg death) — verify no record start/stop regressions or hangs.
- control_calibration.py: a new base-lock freshness gate treats a None camera-age as "fresh" for back-compat. That's lenient — confirm the real DirectRadioGps always reports an age so a stale/rebooted base position can't still be latched.
- config.orin.servo.yaml: a dead `dev_path` key was renamed to `direct_dev_path`. Confirm the live rig config picks up the base Wio port correctly.

iOS (ios/WaveCam/) — these were NEVER compiled locally, only CI-built; the app uses iOS 26 Liquid Glass APIs:
- WaveCamClient.swift R20 (applyControlResponse now gates on status.revision) — verify it doesn't drop legitimate updates. R21 skipped reconciling agentArmed in applyControlResponse (relies on refresh()) — verify no stale ARM display. R16 Summon-button reset, R17 chat-route pinning, R18/R19 watch STOP retry/dismiss — verify on device.

Also do a fresh full sweep for anything the prior audit missed — don't limit yourself to the list above.

Then: save all findings, severity-sorted, with a concrete per-item fix, to a dated markdown file under docs/superpowers/plans/. After that, spawn a handful of Sonnet-5 agents on disjoint file sets to implement the fixes while you supervise: run the full pytest + mypy after they land, check the branch's GitHub Actions CI (backend + iOS build + firmware) BEFORE declaring done — a prior session found tests that passed locally but failed in CI due to a hardcoded path and an Xcode-version mismatch — and commit on a fresh feature branch off main.

Separately, note (do not auto-merge): PR #115 (map-based base placement) is still open and NOT integrated. Merging it into the post-audit main has ~9 conflicts, chiefly the GPS-pointing lead: keep main's age-aware lead_margin_s/lead_cap_s, preserve #115's max_tilt_up_deg sky-clamp, and drop the now-superseded fixed gps.lead_s. Flag it for me to finish with map/rig verification.
```

---

## Session provenance (what produced the code under review)

- **In `main` now:** PR #116 (audit rounds 1+2: findings C1/C2, H1–H14, M1–M24, L1–L17 and re-review R1–R22, plus deferred-tail cleanup M23/L16/base-lock-gate/dev_path) and PR #102 (additive `estimator_mode.py`, a pure helper, NOT wired into the pipeline).
- **CI at merge:** backend 742 tests + mypy clean; iOS build + firmware build green (after fixing a hardcoded-test-path break and pinning the newest Xcode for the iOS 26 SDK).
- **Not done / open:** PR #115 (map-based base placement) — analyzed, ~9 conflicts, not merged. On-device (Mac build) and on-rig verification of everything remain outstanding.
- **Known deviations to re-check:** R1 (no command_min_interval floor), R2 (0.25 deadzone cap), R10 (calibration deadlock instance documented not fixed), R21 (agentArmed reconcile skipped), base-lock freshness gate (lenient None-age = fresh).
