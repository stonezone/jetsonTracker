# WaveCam — Overnight Work, Morning Review Index (2026-06-01)

**Read this first.** While you slept, Claude + Codex worked the **control system, the iOS control app
(design only), and the Orin maintenance/NVMe path** — all coordinated over the `.agent-collab` bus.
Everything is **design / spec / mockup**; nothing was built as production code, no camera boot surgery, no
Swift app compiled. The live tracking rig on `:8088` was left running and untouched.

Each item below links to its full doc. **All open decisions are yours** — they're collected in one list near
the bottom.

---

## TL;DR — my opinion on the control system (confidence 8/10)

**The Orin is the authority. The phone is a remote control. Codex is a supervisor, not a pilot.** Everything
— app, dashboard, agent — talks to **one FastAPI Control API**. The real-time vision→PTZ loop stays
deterministic and is never preempted by the network, the phone, or the LLM. The worst failure must degrade
to *"keeps tracking"* or *"KILL latched"*, never *"camera runs away."* This fits the sport: you're 50–300 m
out and **not holding the phone**, so the session runs **headless once started** — the app is for setup +
monitoring, and losing it mid-session doesn't stop the camera.

---

## Deliverables (all review-ready)

| # | Doc | What it is | By | Status |
|---|---|---|---|---|
| 1 | [Control-system architecture](./2026-06-01-wavecam-control-system-architecture.md) | The 4-layer design + my opinion: deterministic core · Control API seam · supervisor · phone app. 3 approaches compared, ownership model, degradation table. | Claude | ✅ |
| 2 | [iOS / iPadOS app spec](./2026-06-01-wavecam-ios-app-spec.md) | App architecture (SwiftUI + Control-API client + WebView bridge), 9 screens, native-vs-WebView split, **feed-transport decision**, connectivity, degradation. | Claude | ✅ |
| 3 | [iOS app mockup (clickable)](./2026-06-01-wavecam-ios-app-mockup.html) | Open in a browser. Tap the tabs (Live · PTZ · Calibrate · Agent · Dash). Browser-verified: renders, tab-nav, and the **sticky KILL latch** all work. | Claude | ✅ |
| 4 | [Supervisor-layer design](./2026-06-01-wavecam-supervisor-layer-design.md) | `wavecam.service` systemd unit + a *deterministic* supervisor + Codex as *on-demand* assistant. 8 safety invariants, cutover plan, verification gates. Reviewed by Claude (3 notes, Codex accepted). | Codex | ✅ |
| 5 | [Orin maintenance + NVMe runbook](../../ORIN_MAINTENANCE_RUNBOOK.md) | Read-only recon: **root is already on NVMe**; only the 64 MB EFI lives on the microSD. NVMe is ~full → recommended path is a clean SDK-Manager flash at the bench. Codex CLI now installed on the Orin. | Codex | ✅ |
| 6 | FastAPI Control API endpoint spec | The single seam (session/PTZ/safety/status/telemetry/preview + owner model + auth). | Codex | 🔄 in progress |
| — | [Operator-app UX groundwork (2026-05-31)](./2026-05-31-operator-app-ux-design.md) | Prior overnight UX doc — still useful, but predates the LoRa pivot (it assumes Apple Watch / iPhone-base GPS). | Claude | ⚠️ partly superseded |

**Key technical decision (item 2):** the iOS feed transport. iOS `AVPlayer` can't play RTSP or WebRTC
natively, and the Orin has no NVENC (no re-encode). So: **v1 = MJPEG off the `/2` sub-stream** via the
Control API (low-latency, zero new infra, good enough to frame/monitor — the *footage* that matters is the
RTSP `/1` recording, not the phone feed). **v2 = `mediamtx` on the Orin** republishing `/2` to **WebRTC**
(<0.5 s, passthrough) for a crisp monitor feed + **HLS** for a public livestream. Validated with Context7.

---

## Decisions (locked 2026-06-01, by Zack)

1. **Agent autonomy — SUPERVISE-ONLY.** The agent never moves the camera ("no reason to, if we build it
   right"). Operator manual PTZ + vision/GPS auto-tracking still move it; the *agent* never does.
2. **Feed — MJPEG v1.** Confirmed. WebRTC stays a documented v2 upgrade if we ever want crisper/lower-latency.
3. **Orin uplink — iPhone USB-C tether, but UNVALIDATED.** Plugging the phone into the Orin showed **no second
   ethernet interface** in Ubuntu. This is the **field** uplink (at home the Orin is online via Wi-Fi). Needs
   recon (ipheth driver + usbmuxd + Personal Hotspot ON + Trust). **Action item → Codex** (read-only recon).
   Fallback: Orin joins the iPhone's Wi-Fi hotspot.
4. **iPhone only.** No iPad — simpler layout.
5. **Distribution — personal dev build.** Paid account, Apple ID **stonezone@gmail.com**, signing team
   *zachariah jordan*. Installs straight to the paired iPhone; ~1-year signing (paid, no weekly re-sign).
6. **NVMe — YES, proceed — but BENCH-ONLY, with Zack at the Orin.** Goal: free the microSD as a removable
   recording card. Note: the OS is **already** on the NVMe; only the 64 MB EFI is on the SD, and the NVMe is
   ~full → **clean SDK-Manager flash** is the path. A botched boot change = a non-booting Orin needing
   recovery-mode reflash, so **not remote/overnight** — ~20-30 min hands-on together.

---

## Recommended build sequence (once you approve)

1. **FastAPI Control API** (item 6) — the seam everything else needs. Formalize endpoints + owner model + auth.
2. **`wavecam.service` + deterministic supervisor** (item 4) — wrap the *validated* testbed runner first,
   no repo-path cutover; gate auto-restart until the SIGINT-stop + no-jump-on-restart checks pass.
3. **iOS app native shell** (items 2–3) — WebView dashboard + native overlays (feed/KILL/PTZ) on the Control API.
4. **LoRa GPS integration** — when the Wio Tracker hardware lands; coarse-point/zoom, vision refines.

Each step is independently testable. Steps 1–2 are Orin-side (Python); step 3 needs your go-ahead to write Swift.

---

## Collaboration + live-system state

- **Claude ↔ Codex** ran on the `.agent-collab` bus all night: Codex did the NVMe recon, the supervisor
  design, installed the Codex CLI on the Orin, and is now drafting the Control API spec; Claude did the
  control-system architecture, iOS spec + mockup, and reviewed Codex's supervisor doc. Democratic, with
  pre-commit review both ways.
- **Live rig:** the wavecam servo on `:8088` was left running. No camera moves except the small
  self-restoring checks. No boot/partition changes. No Swift built or committed.
- **Constraints honored:** design-gated (brainstorming) — no implementation without your approval.
