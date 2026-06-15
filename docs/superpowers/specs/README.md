# WaveCam Design Specs

These are **point-in-time** design specs. The **current canonical architecture** lives in `CLAUDE.md` and `README.md` — verify against those, not older specs.

## ⚠️ GPS: the Apple-Watch / Cloudflare / Meshtastic approach in the 2026-05-31 / 2026-06-01 specs is RETIRED

GPS is now **direct-LoRa, 2× SeeedStudio Wio Tracker L1 Lite** running the custom firmware in `firmware/direct-lora/`:
- **Remote** tracker (on the subject) = L76K GPS + transmitted 32-byte LoRa packets.
- **Base** tracker (on the Orin, USB-A serial) = receives LoRa packets and emits JSONL; **its own L76K GPS = the camera/tripod reference position**.

Anywhere the older specs mention **Apple Watch, BN-220, an iPhone GPS relay, a Cloudflare tunnel, or Meshtastic**, treat it as **dropped — do not reintroduce.**
- Current GPS design → [`2026-06-12-direct-lora-tracker.md`](2026-06-12-direct-lora-tracker.md)

## Active / current specs
- `2026-06-12-direct-lora-tracker.md` — Direct-LoRa tracker/base firmware (deployed)
- `2026-06-09-gps-control-loop-design.md` — GPS-in-the-control-loop design (implemented with `DirectRadioGps`)
- `2026-06-01-cinematic-zoom-design.md` — Cinematic Zoom (shipped)

## Historical (records of the design at that date)
`2026-05-31-*` and `2026-06-01-*` (operator UX, iOS app, control API, control-system architecture, supervisor layer, review findings). Their **GPS/uplink/watch sections are superseded** per the note above; everything else is point-in-time context. Each file now carries a top note reminding readers of the direct-LoRa/WaveCamWatch reality.
