# WaveCam Operator Guide — Design (2026-06-04)

Replaces `docs/wavecam_v2.html` ("v2 Current Build Spec") with `docs/WaveCam_Guide.html`,
an **operator field guide** organized by iOS app tab, deep-linkable from the app, hosted on the Orin.

## Audience & framing
Field operator (Zack), on the phone, 50–300m offshore. Answers: *what does this screen/control do,
when do I adjust it, and what will I see the camera/tracking do?* Not a build spec — the architecture/
hardware detail from v2 is distilled into reference sections, not the primary frame.

## Structure (section id = deep-link anchor)
Anchors map 1:1 to the 5 app tabs so the in-app Guide button jumps to the right place.

| Anchor | Section | Source tab |
|---|---|---|
| `#overview` | What WaveCam is, tracking idea (orange cue + YOLO + fusion + cinematic zoom), safety-first | — |
| `#live` | Feed, joystick, zoom, home, record, lock HUD, KILL; manual flying + what AUTO does | Live |
| `#calibrate` | 6-step wizard, what each step solves, when to recalibrate | Calibrate |
| `#tune` | **Core.** Tracking Trigger + PTZ Tuning + Cinematic Zoom controls; presets; restart note | Tools→Tune |
| `#agent` | Supervise-only model, health, logs | Tools→Agent |
| `#connect` | Tether vs Wi-Fi, token, mock mode | Connect |
| `#media` | Browse/download/share recordings | Media |
| `#hardware` | Orin, Prisual PTZ, LoRa GPS (planned), uplink | — |
| `#safety` | KILL / Stop PTZ / Resume / Start-Auto hierarchy; emergency reachability | — |
| `#troubleshooting` | Won't lock, jittery, lost subject, zoom stuck, can't connect | — |

## Control reference (the screenshot — key deliverable)
Per control: **what it does · when to adjust (raise/lower → effect) · what you'll see**. Grouped
Tracking Trigger / PTZ Tuning / Cinematic Zoom. Flag HOT (live) vs RESTART-REQUIRED. Describe for the
field use-case (orange rashguard / person); note the screenshot shows a testbed config (blue / "41 cup").
Backend facts (keys, ranges, defaults, hot/restart, lock-unlock + deadband semantics) → **Codex verifies**.

## Look & feel (cross-product consistency with the app's Liquid Glass identity)
- Dark base (~#081018, keeps v2's palette which already aligns).
- **Teal `#36D1C4`** = interactive/links/active. **Orange `#FF6A1F`** = brand + subject-cue semantics.
  **Red `#FF443D`** = KILL/danger. (Same role split as the iOS theme.)
- Glass cards: translucent panels, subtle blur, hairline borders, ~18px radius, soft shadow.
- Inter (sans) + mono for config keys. Sticky section nav, smooth scroll, honors anchors.
- Responsive: single-column on phone (portrait + landscape), multi-column control grid when wide.
  Verified at mobile + desktop widths.

## Deep-linking & hosting
- Stable `id` anchors per section.
- **iOS (Claude):** Guide affordance (book/`?` icon, top bar) → `@Environment(\.openURL)` opens
  `http://<orin-host>:8088/guide#<currentTab>` (host from `WaveCamClient.baseURL`, strip `/api/v1`).
  Plus a "full guide" entry. Feature-safe: button always works (opens Safari); no backend flag needed.
- **Orin (Codex):** static route `GET /guide` → `WaveCam_Guide.html` + `/guide_assets/*` on FastAPI :8088.
  Confirm reachable at `http://<orin>:8088/guide`.

## Assets
Single self-contained HTML (CSS inlined) + a `guide_assets/` folder for screenshots (git-friendly, Codex
serves the folder). iOS screenshots from the simulator (mock mode for standalone screens; live Orin for
Live/Tune/Media) + the provided web-tuning screenshot. No repo bloat (assets are a handful of PNGs).

## Codex tasks (backend — out of Claude's lane)
1. Serve `/guide` + `/guide_assets/*` on the Orin FastAPI; confirm live URL.
2. Review the control reference for backend accuracy (config keys / ranges / defaults / hot-vs-restart /
   lock-unlock + deadband semantics); correct anything wrong.

## Verification
- Render HTML in a browser at phone (portrait/landscape) + desktop widths.
- Build iOS with the Guide button; screenshot it open the right anchor.
- Codex confirms the Orin route + reviews control accuracy.

## Out of scope (YAGNI)
No search, no i18n, no dynamic config fetch (static doc; values are illustrative + Codex-verified),
no auth on the guide route (same trust boundary as the rest of :8088).
