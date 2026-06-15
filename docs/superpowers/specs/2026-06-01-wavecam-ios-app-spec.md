# WaveCam iOS / iPadOS App — Spec (overnight, 2026-06-01)

> **Historical design doc (2026-06-14 note):** This spec predates the custom
> direct-LoRa firmware and bundled WaveCamWatch. The live stack uses
> `firmware/direct-lora/` + `DirectRadioGps` for GPS, `:8088` for the web UI/API,
> and the Apple Watch as an offline-recorder / safety-control companion (not a GPS
> source). The architecture and UI flows below remain useful context, but any
> mention of Meshtastic, Cloudflare GPS relay, Apple Watch/iPhone GPS relay, or
> `:8080`/`:8765` services is superseded.


Context: autonomous overnight session (Zack asleep; back in AM). Zack wants **the phone app to display the
feed and control the whole system**. This is the app-layer spec; the control-system layering is in
[`2026-06-01-wavecam-control-system-architecture.md`](2026-06-01-wavecam-control-system-architecture.md)
and the operator-UX groundwork in [`2026-05-31-operator-app-ux-design.md`](2026-05-31-operator-app-ux-design.md).
Corrected for the **LoRa GPS pivot** (Apple Watch dropped). **DESIGN/SPEC ONLY — no Swift built or
committed; needs Zack's approval before implementation** (brainstorming gate). A clickable HTML mockup is
a companion deliverable (next). Current-API choices validated with Context7; exact Swift APIs to be
re-confirmed with swift-expert + Context7 at build time.

---

## 1. Design principles (inherited from the control-system doc)

1. **The Orin is the authority; the app is a remote control.** No tracking logic on the phone.
2. **The native safety path bypasses everything.** Feed, **emergency-stop (KILL)**, and PTZ talk to the
   Control API *directly*, never through the WebView — so KILL fires even if WebKit is wedged.
3. **The session is headless once started.** Zack is 50–300 m offshore, *not holding the phone*. The app
   is for **setup + monitoring**; losing the phone mid-session must not stop tracking.
4. **One Control API, many clients.** The app calls the same FastAPI surface the dashboard + agent use.

---

## 2. App architecture (SwiftUI; confirm exact APIs with swift-expert + Context7 at build)

- **Pattern:** SwiftUI app, `@Observable` (Observation framework) view-models, one per surface. A single
  `WaveCamClient` actor wraps all Control-API I/O (`URLSession` async/await for REST; `URLSessionWebSocketTask`
  for `/telemetry`). A `SessionStore` holds the last-known status so the UI degrades gracefully when the
  socket drops.
- **Connectivity:** `WaveCamClient` targets the Orin on the LAN by IP + a bearer token (entered/scanned at
  setup). Bonjour/`NWBrowser` discovery is a nice-to-have; manual IP is the v1 fallback.
- **WebView bridge:** the Dashboard tab is a `WKWebView` of the Orin dashboard. A `WKScriptMessageHandler`
  lets the page raise events to native (e.g., "calibration step done") but the **native KILL/PTZ never
  depend on the WebView**. `WKWebView` gets the auth token via a request header / cookie set natively.
- **Persistence:** `camera_pose.json` and connection profile cached locally; the Orin remains the source
  of truth (the app re-fetches on connect).
- **Lifecycle:** a persistent KILL control lives in a top bar visible on **every** screen. Background/locked
  phone does not stop a running session (the Orin owns it).
- **Platforms:** iPhone (primary, at the beach) + iPad (bigger monitor surface). One universal SwiftUI app;
  layout adapts (NavigationStack on iPhone, NavigationSplitView on iPad).

---

## 3. Screens

| Screen | Purpose | Native or WebView | Key elements |
|---|---|---|---|
| **Live / Monitor** | the operator's main view during a session | **Native** | live feed (see §4), status HUD overlay (state, owner, conf, FPS, GPS, REC dot, battery/uplink), one-tap **manual PTZ override**, persistent **KILL** |
| **Control** | start/stop the session + mode | Native | big Start/Stop Tracking; mode picker (vision / vision+GPS); record toggle; preset recall; home |
| **PTZ joystick** | manual framing | **Native** | on-screen joystick (pan/tilt velocity) + zoom slider + nudge buttons + STOP; deadman (release = stop) |
| **Calibration wizard** | guided post-power-up setup | Native (drives Orin) | Preflight → Base-lock → Heading (landmark) → Tilt → (optional YOLO walk) → Zoom/FOV → Dry-run; writes `camera_pose.json` |
| **Dashboard** | the rich operator surface | **WebView** | `WKWebView` of the Orin dashboard (health, GPS, tracking tune, media, network, logs) — no duplication |
| **Status / Health** | at-a-glance system health | Native (or WebView) | services up/down, uplink, GPS-fix age, storage free, temps/power, supervisor state |
| **Agent panel** | the supervisor + Codex | Native | supervisor service health + recent restarts/actions; **"summon Codex"** on-demand assistant (diagnose/answer/apply-config) gated by an "agent control" toggle |
| **Footage** | grab the deliverable | Native (calls API) | recorded segments list, scrub/preview, share/export, eject the removable microSD |
| **Settings** | connection + profile | Native | Orin IP/token, transport choice, units, re-cal, about |

---

## 4. Feed transport — decision (Context7-validated)

The Prisual exposes **RTSP `/1`** (1080p60 H.264, the *recording* deliverable) and **RTSP `/2`** (640×360
H.264, the monitor feed). **iOS `AVPlayer` cannot play RTSP or WebRTC natively** — it speaks HLS. So the
question is what the Orin republishes and how the app renders it. (The Orin has **NVDEC but no NVENC**, so
any path must be **codec passthrough / no re-encode**.)

| Transport | Latency | iOS rendering | Orin work | Verdict |
|---|---|---|---|---|
| **MJPEG** (re-JPEG of `/2` via Control API) | low (~0.1–0.3 s) | trivial — `<img>` in a small WebView, or URLSession multipart → `UIImage` stream | already in the dashboard plan; CPU JPEG of 360p is cheap | **v1 — ship this** |
| **WebRTC / WHEP** (mediamtx republish of `/2`) | lowest (<0.5 s) | a WHEP player (mediamtx ships a browser page; or LiveKit/WebRTC pod) | run `mediamtx`, passthrough, no re-encode | **v2 — best monitor feed** |
| **LL-HLS** (mediamtx) | ~1–2 s | native `AVPlayer` (cleanest native API) | run `mediamtx`, passthrough | good for **viewers/livestream**, too laggy for a control feed |
| **HLS (standard)** | 3–6 s | native `AVPlayer` | re-mux only | public **livestream** path, not control |
| **RTSP direct** | low | needs MobileVLCKit (heavy dep) | none | avoid — third-party decode burden |

**Recommendation (Validated against the rig + Context7; confidence 8/10):**
- **v1 control feed = MJPEG off `/2`** through the Control API. Trivial, low-latency, no new infra; good
  enough to *frame* and *monitor*. The footage that matters is the RTSP `/1` recording, not the phone feed.
- **v2 = add `mediamtx` on the Orin** (Context7: zero-dep, proxies RTSP and republishes **WebRTC/WHEP**
  with **passthrough/no re-encode**) for a crisp **<0.5 s WebRTC** monitor feed, and **HLS** for a public
  livestream to viewers. One server covers both.
- The app abstracts this behind a `FeedPlayer` protocol (MJPEG impl now, WHEP impl later) so swapping
  transports doesn't touch the screens.

---

## 5. Connectivity

- **Orin uplink = USB-C↔USB-C tether** to the phone (more reliable than a hotspot for the Cloudflare GPS
  relay + livestream at the beach). The app surfaces uplink health + auto-reconnect.
- **Local control = LAN** (the Orin's wired/AP address). Manual PTZ, KILL, status, and the MJPEG feed work
  **on the LAN with no internet** — critical if the tether/relay drops.
- **GPS = LoRa** (Wio Tracker on the subject → Orin). Consider a **local (no-Cloudflare) GPS path** over the
  USB-C link as a fallback. (No Apple Watch in the loop.)

---

## 6. Degradation behavior (the use-case test)

| Failure | App behavior |
|---|---|
| Phone disconnects mid-session | Session continues on the Orin; on reconnect the app re-syncs from `/status`. |
| WebView wedged | Native KILL / PTZ / feed unaffected (direct API). |
| Telemetry socket drops | UI shows last-known state greyed + "reconnecting"; controls still POST over REST. |
| Uplink/relay down | LAN control + local feed still work; banner shows degraded connectivity. |
| GPS (LoRa) dropout | Hold framing → widen zoom → slow visual search (Orin-side); app shows "GPS stale". |
| Sun in slew path | App shows the keep-out; Orin refuses the slew. |

---

## 7. Agent panel (reflects Codex's supervisor split)

Two distinct things, surfaced clearly:
- **Deterministic supervisor** (`wavecam-supervisor.service`) — shows service health, recent
  restarts/config-applies; this is automatic and always running.
- **Codex (on-demand assistant)** — a "summon Codex" action for diagnosis / config help / answering
  operator questions. **Not always-on, never a motor-control daemon** (always-on Codex = 3/10 per Codex's
  own assessment). Any camera-affecting action stays behind the **"agent control" toggle** + the owner
  model + KILL.

---

## 8. Feasibility + open questions

**Feasibility:** MJPEG feed + native controls + WebView dashboard 8/10 (`Unvalidated`, standard iOS).
WebRTC v2 via mediamtx 7/10. Calibration wizard UI 7/10 (depends on Orin endpoints). USB-C tether uplink
6/10 (needs on-device validation).

**Open questions for Zack (AM):**
1. iPhone-only first, or iPhone+iPad together? (Recommend universal app, iPhone-first polish.)
2. v1 MJPEG monitor feed acceptable, or is WebRTC required for day one? (Recommend MJPEG v1.)
3. App distribution: personal dev build (free, 7-day re-sign) vs TestFlight (needs paid Apple Developer
   account)? Affects how you install it.
4. Confirm the **agent autonomy ceiling** (same question as the control-system doc): supervise-only, or
   ever allowed to move the camera under the toggle?

## 9. Implementation notes (deferred — needs approval)
- At build time, re-confirm exact SwiftUI/AVFoundation/WebRTC APIs with **swift-expert + Context7**.
- Reuse the existing GPS-relay app's `Track` surface if still relevant post-LoRa; otherwise a clean SwiftUI
  app keyed to the Control API.
- Next deliverable this overnight: a **clickable HTML mockup** (frontend-design / notugly) of the Live,
  PTZ, Calibration, and Agent screens so Zack can *see* the layout before any Swift is written.
