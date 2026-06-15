# WaveCam Control-System Architecture + Opinion — overnight (2026-06-01)

> **Historical design doc (2026-06-14 note):** This spec predates the custom
> direct-LoRa firmware and bundled WaveCamWatch. The live stack uses
> `firmware/direct-lora/` + `DirectRadioGps` for GPS, `:8088` for the web UI/API,
> and the Apple Watch as an offline-recorder / safety-control companion (not a GPS
> source). The architecture and UI flows below remain useful context, but any
> mention of Meshtastic, Cloudflare GPS relay, Apple Watch/iPhone GPS relay, or
> `:8080`/`:8765` services is superseded.


Context: autonomous overnight session (Zack asleep, authorized; back in AM). Zack asked directly:
*"do you have a spec for the control system? what is your opinion on it?"* and wants the phone app to
**display the feed and control the whole system**, with **Codex installed on the Orin so it can run the
whole system**. This doc is the control-system layer; the iOS app spec + clickable mockup are companion
docs. **DESIGN ONLY — nothing here is built or committed as code; needs Zack's approval before
implementation** (brainstorming gate). Builds on [`2026-05-31-operator-app-ux-design.md`](2026-05-31-operator-app-ux-design.md)
and corrects it for the **LoRa GPS pivot** (Apple Watch dropped; see project memory).

---

## TL;DR opinion (Validated against the running rig where noted; confidence 8/10)

**The Orin is the authority. The phone is a remote control. The agent (Codex) is a supervisor, not a
pilot.** Everything — app, agent, dashboard — talks to **one control API** on the Orin. The
real-time vision→PTZ loop stays deterministic and is never preempted by the network, the phone, or the
LLM. The worst failure case must degrade to *"Orin keeps tracking"* or *"KILL latched"* — never *"camera
runs away."* That single principle drives every choice below.

This matches the use-case hard constraint: **Zack is in the water 50–300 m out, not holding the phone.**
The session must run **headless once started**; the phone is for setup + monitoring, not required during
the ride.

---

## What already exists (explore)

- **Deterministic core (built, HW-verified):** `orin/wavecam` pipeline — capture → color+YOLO fusion →
  visual servo → RAW VISCA, with `PtzOwner` (single PTZ writer, no auto-steal, **sticky KILL latch**,
  ~0.8 s manual deadman). Running live on `:8088`. *Validated.*
- **Dashboard (MVP):** Orin web dashboard, authority for camera/tracking/recording; `:8080`. Panels per
  `DASHBOARD_SPEC.md`: session header, health, GPS, preview, PTZ, tracking tune, media, network, logs.
  Decision already taken with Zack: **migrate dashboard backend to FastAPI**. *Validated.*
- **Agent runtime:** **Codex CLI 0.135.0 installed on the Orin** (`/home/zack/.local/bin/codex`,
  SHA-verified) per `ORIN_MAINTENANCE_RUNBOOK.md`. The `.agent-collab` bus + claims already mediate
  Claude↔Codex. *Validated (Codex recon).*
- **GPS:** pivoting to **LoRa (Wio Tracker L1 Lite)**; the Watch/iPhone-base path in the 2026-05-31 doc
  is superseded. GPS coarse-points/zooms; vision refines. *Unvalidated — hardware en route.*

So the pieces exist. The open question is **how they compose into a phone-controlled system** without
putting network/LLM nondeterminism into the safety loop.

---

## Three approaches (with trade-offs)

### A. Phone-thick (phone runs control logic, talks to camera/Orin as peers)
- **Pros:** rich native control; fewer Orin changes.
- **Cons:** moves authority off the deterministic core; the phone-in-a-drybag-on-shore problem makes it
  fragile; two brains fight over the camera; KILL semantics get murky. **Reject.** Feasibility 4/10.

### B. Orin-authority + thin web app (phone = WKWebView of the dashboard only)
- **Pros:** simplest app; zero logic duplication; the dashboard is already the authority.
- **Cons:** the live feed, KILL, and PTZ all ride the WebView — if WebKit is janky or the page is
  reloading, **the emergency-stop is delayed**. No offline degradation. Feed latency inherits browser
  buffering. **Partial — unacceptable for a moving camera near a person.** Feasibility 6/10.

### C. Orin-authority + control-API seam + hybrid app + agent-as-supervisor  ✅ RECOMMENDED
- **Pros:** one API, many clients; LLM + network + phone all OUT of the real-time loop; native
  low-latency path for the three things that must never lag (feed, KILL, PTZ); headless autonomy during
  the session; the agent can "run the system" safely as a supervisor.
- **Cons:** most upfront design (one extra layer: the supervisor). Worth it. Feasibility 8/10.

**Recommendation: C.** Rationale below.

---

## Recommended architecture (C) — four layers

```
┌─────────────────────────── iOS / iPadOS app (operator window) ───────────────────────────┐
│  WKWebView(dashboard)  ·  NATIVE overlays: live feed · ⛔ KILL · PTZ joystick · status HUD │
│         (rich surface)        (low-latency, talk DIRECT to Control API — bypass WebView)    │
└───────────────────────────────────────────┬───────────────────────────────────────────────┘
                                             │  HTTPS/REST + WebSocket + MJPEG  (LAN token auth)
┌────────────────────────────────────────────▼──────────────────────────────────────────────┐
│  CONTROL API  (Orin, FastAPI)  — the single seam                                            │
│   POST start|stop|track|calibrate|record  ·  POST ptz/nudge|zoom|preset  ·  POST kill|resume │
│   GET /status (state, owner, health)  ·  WS /telemetry (push)  ·  GET /preview.mjpeg (/2)     │
│   enforces: owner model · LAN auth token · agent-enable toggle                                │
└───────┬───────────────────────────────────────────────────────────────┬─────────────────────┘
        │ in-process / IPC                                                │ same API, privileged client
┌───────▼───────────────────────────────────┐               ┌────────────▼───────────────────────┐
│  DETERMINISTIC CORE (Orin, Python)         │               │  AGENT SUPERVISOR (Codex on Orin)    │
│  capture→fusion→servo→VISCA · recorder     │  NEVER        │  systemd up/down · health watch ·    │
│  PtzOwner: single writer, no steal,        │  blocked by   │  crash-restart · diagnostics ·       │
│  STICKY KILL latch, 0.8s deadman           │  net/LLM/app  │  config apply · answer operator      │
│  *** real-time, deterministic ***          │◄──commands────│  *** async, out of the loop ***      │
└────────────────────────────────────────────┘               └──────────────────────────────────────┘
        │ UART/VISCA UDP 1259                                  LoRa GPS (Wio Tracker) ─► fusion (coarse)
┌───────▼───────────────┐
│  Prisual PTZ + steppers │
└────────────────────────┘
```

### Layer 1 — Deterministic core (safety-critical, already built)
Owns the real-time loop and `PtzOwner`. **Invariant: nothing in the phone, agent, or network path can
preempt the KILL latch or the deadman, or inject latency into the servo loop.** Already true today; the
job is to keep it true as we add clients.

### Layer 2 — Control API (FastAPI) — the single seam
The *only* way anything reaches the core. The dashboard, the iOS native overlays, and the agent are all
just clients of this API.
- **Commands:** session (start/stop/track/calibrate/record), PTZ (nudge/velocity/zoom/preset/home),
  safety (kill/resume).
- **State:** `GET /status` (state, owner, health, GPS, recording), `WS /telemetry` for push.
- **Feed:** `GET /preview.mjpeg` off the camera `/2` sub-stream (360p — fine for monitoring; the
  *deliverable* footage is the RTSP `/1` recording, not the phone feed).
- **Enforces:** the owner model, a LAN auth token, and an explicit **agent-enable** toggle.

### Layer 3 — Agent supervisor (Codex on Orin) — operator-of-last-resort, NOT a pilot
This is the safe reading of *"Codex installed on the Orin so it can run the whole system."*
- **Can:** bring services up/down (systemd), watch health + auto-restart on crash, run diagnostics,
  apply/validate config, pre-flight checks, and answer operator questions — all via the **same Control
  API** as a privileged client, plus host-level systemd.
- **Cannot:** enter the frame-by-frame control loop. The LLM's latency/nondeterminism never touches the
  servo. The agent issues *high-level* intents ("start tracking", "switch preset", "restart recorder");
  the deterministic core executes them. **Killing the agent must not affect tracking.**
- **Gated:** the agent commands the camera only when the operator flips **"agent control"** on; KILL and
  the owner model still bind it. Today this maps onto the `.agent-collab` model; on the Orin it becomes a
  small supervisor process + a `wavecam.service` systemd unit (Codex flagged that **no `wavecam.service`
  exists yet** — the servo is a bare process; making it a managed unit is the first supervisor task).

  **Refinement (per Codex, 2026-06-01):** the supervisor splits in two — a *deterministic*
  `wavecam-supervisor.service` (polls the Control API + systemd, publishes health, gated
  restart/config-apply) that is always running, and **Codex itself as an on-demand assistant**
  (diagnose / answer / apply-config), never an always-on motor daemon. Always-on Codex is 3/10
  (token / credential / nondeterminism / privilege risk). Detail in the companion
  [`2026-06-01-wavecam-supervisor-layer-design.md`](2026-06-01-wavecam-supervisor-layer-design.md).

### Layer 4 — iOS/iPadOS app — operator window (detail in the companion app spec)
- **WKWebView** of the dashboard = the rich operator surface (no logic duplication; the dashboard is the
  authority).
- **Native overlays** for the three things that must be low-latency and must work even if the WebView is
  wedged: **(a) live feed, (b) the big red KILL, (c) the PTZ joystick.** These call the Control API
  **directly**, never through the WebView — so KILL fires even mid-page-reload.
- **Native shell** also owns: connectivity (**USB-C↔USB-C tether** to give the Orin its uplink — more
  reliable than a hotspot for the GPS relay + stream), app lifecycle, and a persistent KILL in the nav
  bar visible on every screen.

---

## Control authority / ownership model (extends the built `PtzOwner`)

Owners: `idle · manual(operator) · vision_follow · gps_tracker · agent(supervisor) · testbed`.

| Actor | May KILL | May grab manual | May auto-track | May command camera |
|---|:--:|:--:|:--:|:--:|
| Operator (app) | always | always | starts/stops | yes |
| Vision/GPS auto | — | — | default session mode | yes (owns loop) |
| Agent (Codex) | — | — | — | only if operator enables "agent control" |

Rules (already partly enforced): **no auto-steal**, **sticky KILL** (latches until explicit RESUME),
manual deadman ~0.8 s. KILL is reachable from every surface and bypasses the WebView and the agent.

---

## Degradation behavior (the use-case test)

| Failure | Required behavior |
|---|---|
| Phone disconnects mid-session | Tracking **continues** (Orin is authority). Session is headless once started. |
| WebView wedged / reloading | Native KILL + PTZ + feed still work (direct API). |
| Agent (Codex) hangs/killed | No effect on tracking; supervisor restarts independently. |
| GPS dropout (LoRa) | Hold last framing → widen zoom → slow visual search (no slew-to-nowhere). |
| Network/uplink drops | Local control still works on LAN; relay/stream auto-reconnect; consider a local (no-Cloudflare) GPS path over the USB-C link. |
| Sun in the slew path | No-slew-into-sun keep-out (sun azimuth from time+location) to protect the sensor. |

---

## Why this is the right call (the opinion, explicit)

1. **Safety by construction.** The LLM, the network, and the phone are *structurally* outside the
   deterministic loop. You cannot get a runaway camera from an app glitch or an agent hallucination —
   the worst case is "keeps tracking" or "latched stop." `Validated` design property, confidence 8/10.
2. **One seam, many clients.** A single Control API means the dashboard, the app's native overlays, and
   the agent never invent competing control paths. Less code, fewer race conditions. `Validated`, 8/10.
3. **The agent can genuinely "run the system" — safely.** Supervisor scope (up/down/health/restart/
   diagnose/answer) is exactly "run the system" minus the one thing an LLM must never do: drive a motor
   in real time. `Unvalidated` (supervisor not built), 7/10.
4. **It fits the sport.** Headless autonomy during the ride; phone for setup/monitor; KILL always one
   tap away. `Validated` against the use-case, 9/10.

---

## What's already done vs. open

- **Done:** deterministic core + PtzOwner + KILL (live); dashboard MVP; Codex CLI on Orin; NVMe recon +
  migration runbook (`ORIN_MAINTENANCE_RUNBOOK.md`, by Codex).
- **Open (needs Zack's approval, then implementation):** FastAPI Control API surface (formalize the
  endpoints above); `wavecam.service` systemd unit + supervisor process; iOS app (companion spec +
  mockup); LoRa GPS integration when hardware lands.

## Companion docs (this overnight, in progress)
- iOS/iPadOS app spec — screens, native-vs-web split, feed-transport decision, connectivity.
- Clickable HTML UI mockup (frontend-design / notugly) so Zack can *see* it.

## Open questions for Zack (AM)
1. Agent autonomy ceiling: should "agent control" ever be allowed to **move the camera**, or strictly
   supervise (up/down/health/restart) and leave all motion to vision/GPS/operator?
2. Feed transport priority: is 360p MJPEG monitoring enough for the phone, or do you want a
   low-latency WebRTC feed later (heavier to build)?
3. Confirm the **USB-C tether** as the Orin's primary uplink at the beach.
