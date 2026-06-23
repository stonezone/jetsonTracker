# Calibration: transactional commit + bail-exit + named sites + step-status strip

- **Created:** 2026-06-23
- **Status:** Item 1 **SHIPPED 2026-06-23** (backend `106d3c3` deployed + verified on the live rig; iOS build **598** installed). Items 2 (named sites) + 3 (status strip) **PLANNED**.
- **Owner / lane:** Claude (backend `orin/wavecam` + iOS `ios/WaveCam`)
- **Severity / why:** HIGH for item 1 — a partial/abandoned calibration **overwrites the live pose** and you **can't bail out** of the menu. Field feedback 2026-06-23 (testing inside, tracker off): at aim/capture it (correctly) can't get a fix, but trying to exit hit *"validation must pass and be confirmed before confirm=true."*

## Item 1 — transactional calibration (the real fix)

**Problem (grounded in code):**
- `WaveCamClient.calibrateSessionExit` ([:1399](../../ios/WaveCam/Sources/WaveCamClient.swift)) hardcodes `confirm:true` → `exit_session` refuses without a passing validation. **No discard/bail exit exists.**
- Every step **writes through to the live `pipeline.pose` and persists immediately**: `_commit_location` ([control_calibration.py:516](../../orin/wavecam/wavecam/control_calibration.py)) sets lat/lon/subject_alt; `heading_lock` (:628) + `offset_calibrate` (:724) re-anchor pan/tilt; each `_persist_step` → `camera_pose.json`. So a half-done calibration **destroys the previous good calibration**, and exit doesn't restore it. (Tracking isn't actively broken mid-session because `calibration_valid=False` gates the arbiter — but the old VALID pose is gone.)

**Goal / done-when:** you can leave the Calibrate menu at any step with **zero change** to the live values; the new values **commit (overwrite live + persist) only when the full flow completes (Confirm)**.

**Approaches:**
- **A — snapshot / rollback (recommended, minimal):** snapshot the pose (+ `camera_pose.json`) on session start; keep the current write-through during the session; on **Confirm** keep it; on **bail/exit** restore the snapshot. The `.bak` roll in `calibration_store.save()` is a partial foundation. Immediate sub-fix: the header **Exit** sends `confirm:false` (bail → rollback); **"Confirm & finish"** stays `confirm:true` (commit). Low risk; the transient in-session pose change is harmless (gated by `valid=False`).
- **B — full staging (cleaner, bigger):** never touch `pose` until Confirm; each step computes against staged session values and commits all-at-once. Rewires the offset encoder/anchor math to read staged-not-live; more surface.

→ Recommend **A**. The Exit-button `confirm:false` fix is worth doing first regardless (it unblocks bailing).

## Item 2 — named sites + load-and-partial-update
`SavedSpotsStore.swift` already exists (the unwired v2 piece). Name the calibration site; **load an existing profile to pre-fill** the steps; only set what changed (e.g. tripod moved slightly → re-aim, but height unchanged → keep saved height; don't require re-entry). Pairs naturally with the staged/snapshot values in item 1. Feasibility ~7/10.

## Item 3 — step-status strip (optional)
A minimal top/bottom strip, one circle per step/value: **hollow = no value, solid = defined + ready to commit**; colors **red = old/unchanged, green = new/revised** (the red/green old-vs-new needs item 2 to know "old"; solid/hollow works standalone). Small footprint. Feasibility ~8/10; the user explicitly said "save for later if too much for just a visual."

## Open questions (resolve before building)
1. Item 1: approach **A (snapshot/rollback)** vs **B (full staging)**?
2. Site identity: operator-named label (multiple per beach) vs GPS-spot?
3. Strip placement (top/bottom) + ship colors now (needs item 2) or solid/hollow first?

## Test / verify
- Bail mid-flow → assert live `pose` + `camera_pose.json` are byte-unchanged from session start (a regression test) + on-device: start calibrate, do a step, Exit, confirm the prior calibration still loads.
- Confirm → committed + `calibration_valid` true.

## Worklog
- _2026-06-23 (built)_ — **Item 1 SHIPPED via approach A.** Snapshot pose + store metadata at CALIBRATE entry (`control_calibration.py` `_snapshot_pose`/`_restore_pose_snapshot`); a bail (`confirm:false`) / cancel / KILL restores it (in-memory + `camera_pose.json` byte-for-byte); only "Confirm & finish" (`confirm:true`) commits. iOS `calibrateSessionExit` defaults to `confirm:false` so the header **Exit** bails; `confirmAndFinish` passes `confirm:true`. Bundled the **`gps.lead_s` hot key** (field lead-tuning, 0–3s, also exposed in `/config`). Backend `cdae6ab`+`106d3c3` (623 pytest + mypy gate green, deployed, `/version` verified, fps ~26). iOS `c2d0d21` build **598** installed. **Verified on the live rig:** start → mutate location → exit(`confirm:false`) restored pose + ref_heading exactly, owner→idle. **NOT yet on-device-observed:** the header-Exit tap-through (do this in the field walk). Items 2 & 3 deferred — not blockers.
- _2026-06-23_ — created from field feedback; bug grounded (Exit sends confirm:true; steps write-through). Recommended approach A. Not built (brainstorm only).
