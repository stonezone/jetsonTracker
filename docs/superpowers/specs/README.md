# WaveCam Design Specs

These are **point-in-time** design specs. The **current canonical architecture** lives in `CLAUDE.md` and the `.claude` memory `wavecam-architecture-pivot` — verify against those, not older specs.

## ⚠️ GPS: the Apple-Watch / Cloudflare approach in the 2026-05-31 / 2026-06-01 specs is RETIRED

GPS is now **LoRa-only, 2× SeeedStudio Wio Tracker L1 Lite**:
- **Remote** tracker (on the subject) = L76K GPS + an **IMU** (heading/speed/motion → leads the surfer).
- **Base** tracker (on the Orin, USB-A serial) = receives the mesh; **its own GPS = the camera/tripod reference position**.

Anywhere the older specs mention **Apple Watch, BN-220, an iPhone GPS relay, or a Cloudflare tunnel**, treat it as **dropped — do not reintroduce.**
- Current GPS design → [`2026-06-05-gps-lora-cueing-design.md`](2026-06-05-gps-lora-cueing-design.md)

## Active specs
- `2026-06-05-gps-lora-cueing-design.md` — GPS/LoRa coarse-cueing (decisions locked 2026-06-06)
- `2026-06-01-cinematic-zoom-design.md` — Cinematic Zoom (shipped)

## Historical (records of the design at that date)
`2026-05-31-*` and `2026-06-01-*` (operator UX, iOS app, control API, control-system architecture, supervisor layer, review findings). Their **GPS/uplink sections are superseded** per the note above; everything else is point-in-time context.
