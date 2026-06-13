# Direct LoRa tracker — custom Wio firmware (secondary GPS solution)

**Status: PREPARED, NOT ACTIVE.** The Meshtastic/Wio setup remains the
solution until driveway shadow scoring proves otherwise. This spec + the
firmware under `firmware/direct-lora/` exist so that, if the data says we
need more rate/freshness, we start from a running prototype instead of a
blank page. Scope and feasibility incorporate GPT's review (2026-06-12),
which we adopt: phased bring-up, measure-the-GNSS-first, regulatory as an
engineering requirement.

## Architecture (UDP-style, one-way, stateless)

```
Tracker Wio:  L76K GNSS (2→5 Hz) ──> 32-byte packet ──> SX1262 LoRa TX
Base Wio:     SX1262 LoRa RX ──> JSONL frame over USB serial ──> Orin
Orin:         DirectRadioGps reader ──> NormalizedFix seam (unchanged)
```

No mesh, no ACKs, no retries, no routing: for live tracking a dropped
packet beats an old retransmitted one. The camera wants the newest fix
with a sequence number and timestamp; loss is measured, not corrected.

## Verified facts (2026-06-12)

- **L76K max nav rate: 5 Hz** (PMTK220-settable; 1 Hz default) — so the
  stock-hardware ceiling is 5 Hz; 10 Hz REQUIRES external GNSS.
  Source: Quectel L76K docs via Seeed/Waveshare wikis.
- **Wio Tracker L1 pin map** (from Meshtastic
  `variants/nrf52840/seeed_wio_tracker_L1/variant.h`, pinned fetch in
  `firmware/direct-lora/fetch_variant.sh`):
  SX1262 CS=D4 DIO1=D1 BUSY=D3 RESET=D2 RXEN=D5 TXEN=none(DIO2 ctl);
  SPI SCK=8 MOSI=10 MISO=9; GPS on Serial1 TX=D6 RX=D7 @9600,
  STANDBY=D0; VBAT=D16 (x2.0 divider, 12-bit, 3.6 Vref);
  LEDs 11(green)/12(blue); button D13 active-low.
- **External GNSS part (Phase 5 only): SparkFun MAX-M10S breakout
  (GPS-18037)**, ~$22–45, in stock at DigiKey/SparkFun/Amazon. 10 Hz
  (up to 25 Hz single-constellation), UART @38400 default, 3.3 V,
  <25 mW. Wires to the same Serial1 pins; L76K held in STANDBY (D0).
- Bootloader: UF2 drag-drop DFU (double-tap reset). **Never use nRF OTA
  flashing — documented brick risk (Seeed).**

## Packet format (32 bytes, little-endian, CRC16-CCITT)

| field | bytes | unit |
|---|---|---|
| magic 0x57 'W', version | 2 | — |
| sequence | 2 | wraps |
| tracker_ms | 4 | ms since boot |
| lat_e7, lon_e7 | 4+4 | deg × 1e7 |
| speed_cm_s | 2 | cm/s |
| course_cdeg | 2 | deg × 100 |
| hacc_cm | 2 | cm (sat-derived est.) |
| flags(fix,valid)+sats | 2 | bitfield |
| battery_mv | 2 | mV |
| reserved | 4 | future (heading source, temp) |
| crc16 | 2 | CCITT over bytes 0..29 |

At SF7/BW250 a 32-byte LoRa payload ≈ 35 ms airtime → 5 Hz ≈ 17% duty:
legal in US915 (100% duty allowed) with margin; the firmware still
carries an airtime guard so a config error cannot flood the band.

## Radio plan (test in this order)

1. LoRa SF7 / BW250 / CR4:5 / US915 @ 2 Hz  ← bring-up target
2. Same @ 5 Hz (after L76K PMTK220 5 Hz verified outdoors)
3. SF7/BW500 or GFSK only if airtime/jitter demands it (still legal US)

**Regulatory is an engineering requirement, not a checkbox** (GPT
correction, adopted): region/frequency/power/duty are compile-time
constants in `radio_config.h` with a US915 default (902–928 MHz,
+22 dBm max on SX1262, 100% duty) and a hard airtime guard.

## Phases (GPT scope, adopted)

1. **Build+flash proof** — stock variant builds, hello-world UF2 boots,
   serial log reliable. (Needs Zack: DFU button + USB.)
2. **Radio proof** — fixed packet @5 Hz A→B, seq/RSSI/SNR/loss printed.
   No GPS.
3. **GPS beacon** — tracker parses own L76K (measure real cadence
   outdoors FIRST; PMTK220 to 2 Hz then 5 Hz), base emits JSONL.
4. **Orin integration** — `DirectRadioGps` reader behind the existing
   NormalizedFix seam (same non-blocking snapshot contract as
   MeshtasticGps); `gps.source: meshtastic|direct_lora` config switch,
   default meshtastic. Events carry packet age + loss.
5. **External M10 GNSS** — ONLY if measured L76K rate/quality is the
   binding constraint. Same packet, same radio, new UART source.
6. **Field hardening** — 100/300/800 m over water, on-body antenna
   orientation, wet-case, packet-age histogram at the Orin. This phase,
   not firmware, is where "works" becomes "surf-ready" (GPT correction,
   adopted: sessions 1–4 ≠ production reliability).

## Hurdles ledger

- L76K cadence above 1 Hz must be MEASURED outdoors before any 5 Hz
  promise (PMTK220 accepted ≠ fresh fixes delivered).
- Variant pin aliases (D0..D16) resolve only with Seeed's variant.cpp —
  vendored at build time by `fetch_variant.sh` (pinned commit), not
  committed (GPL provenance kept out of this repo).
- Antenna: stock U.FL whip on a moving body; body shadowing dominates
  link budget long before SF7 sensitivity does. Field phase decides.
- One radio config error can jam the band for other users — the airtime
  guard + fixed compile-time region constants are non-negotiable.
- Losing Meshtastic's tooling means we own debugging: base firmware
  prints loss/RSSI/SNR per packet from day one for exactly this reason.

## Shopping list

| item | when | part | est. |
|---|---|---|---|
| nothing | Phases 1–4 | existing 2× Wio L1 + 1100 mAh | $0 |
| 10 Hz GNSS | Phase 5 only | SparkFun MAX-M10S (GPS-18037) | ~$22–45 |
| spare U.FL antenna | Phase 6 | 915 MHz whip, flexible | ~$8 |
