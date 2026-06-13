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
  `variants/nrf52840/seeed_wio_tracker_L1/variant.h`, fetched by
  `fetch_variant.sh` PINNED to commit `88137c6` + `variants.lock` sha256s —
  the firmware uses the `SX126X_*` macros, not hardcoded pins):
  SX1262 CS=D4 DIO1=D1 BUSY=D3 RESET=D2 RXEN=D5, **TX gated by DIO2**
  (`SX126X_DIO2_AS_RF_SWITCH`), **1.8 V TCXO on DIO3**
  (`SX126X_DIO3_TCXO_VOLTAGE 1.8` — MUST be passed to `begin()` or the radio
  won't start; this was the review's highest-value catch);
  GPS on Serial1 TX=D6 RX=D7 @9600, STANDBY=D0; VBAT=PIN_VBAT (x2 divider,
  12-bit, AR_INTERNAL); button D13 active-low.
- **PIN_LED2 IS THE BUZZER** (`PIN_LED2 == PIN_BUZZER == D12 / P1.00`,
  verified in variant.h) — the firmware drives ONLY PIN_LED1 (`STATUS_LED`);
  touching LED2 would chirp the buzzer every packet.
- **External GNSS part (Phase 5 only): SparkFun MAX-M10S breakout
  (GPS-18037)**, ~$22–45, in stock at DigiKey/SparkFun/Amazon. 10 Hz
  (up to 25 Hz single-constellation), UART @38400 default, 3.3 V,
  <25 mW. Wires to the same Serial1 pins; L76K held in STANDBY (D0).
- Bootloader: UF2 drag-drop DFU (double-tap reset). **Never use nRF OTA
  flashing — documented brick risk (Seeed).**
- **SoftDevice layout: the Wio bootloader runs S140 7.3.0 → app base 0x27000**
  (per INFO_UF2.TXT: `UF2 Bootloader 0.9.2`, `S140 7.3.0`). The PlatformIO
  `adafruit_feather_nrf52840` board def links for S140 6.1.1 (app base
  0x26000); flashing that boot-looped the board on first bring-up (orange LED
  fast-flash, no USB enumeration, no DFU catchable during the loop). Fixed
  with `board_build.ldscript = ld/nrf52840_s140_v7.ld` (the framework's own
  nrf52840 v6 script with FLASH ORIGIN 0x26000→0x27000; RAM/sections
  unchanged). VERIFY the built UF2's first address is 0x27000, not 0x26000.

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
| flags(fix,valid)+sats | 2 | bitfield (fix = age-gated FRESH, not sticky) |
| battery_mv | 2 | mV |
| gps_age_ms | 2 | ms since GNSS commit; 0xFFFF = stale/unknown |
| reserved | 2 | future (heading source, temp) |
| crc16 | 2 | CCITT over bytes 0..29 |

**Freshness is carried, not inferred** (review F1): the beacon fires on a
timer regardless of GNSS, and TinyGPS `isValid()` is sticky-true forever
after the first fix. So `PKT_FLAG_FIX_VALID` is set only when the fix age is
< 2 s, and `gps_age_ms` rides along so the Orin judges freshness from the
GNSS commit time, not the packet's `tracker_ms`.

At SF7/BW250 a 32-byte LoRa payload ≈ 35 ms airtime → 5 Hz ≈ 17% duty:
legal in US915 (100% duty allowed) with margin; the firmware still
carries an airtime guard so a config error cannot flood the band.

## GNSS bring-up (PMTK, verified against the Quectel protocol spec)

Implemented in `src/common/gps_l76k.h`; re-runs on EVERY boot because
PMTK251/PMTK220 revert on cold restart/standby.

1. Open at 9600 (module default) → `$PMTK251,57600` (NO ACK by design —
   the port speed changes underneath) → reopen at 57600. Even at 2 Hz,
   57600 keeps NMEA buffer pressure at zero; 5 Hz REQUIRES ≥57600.
2. `$PMTK314,0,1,0,1,...` — RMC+GGA only, every fix (lat/lon/speed/
   course + quality/sats/HDOP is everything the packet needs).
3. `$PMTK220,<ms>` — beacon-matched, clamped to 200 ms (the L76K module
   max is 5 Hz per Seeed, even though generic PMTK can express 100 ms —
   do NOT assume 10 Hz from this module).
4. ACKs (`$PMTK001,<cmd>,3` = success) are logged for 1.5 s, never
   blocking: measured outdoor NMEA cadence is the real verification.

## Radio plan (test in this order)

1. LoRa SF7 / BW250 / CR4:5 / US915 @ 2 Hz  ← bring-up target
2. Same @ 5 Hz (after L76K PMTK220 5 Hz verified outdoors)
3. SF7/BW500 or GFSK only if airtime/jitter demands it (still legal US)

**Regulatory is an engineering requirement, not a checkbox** (GPT
correction, adopted): region/frequency/power/duty are compile-time
constants in `radio_config.h` (US915: 902–928 MHz, +22 dBm max, 100% duty).
The airtime guard is **computed at boot** from `radio.getTimeOnAir(PKT_LEN)`
and held to ≥3× airtime, so changing SF/BW/payload can't silently blow the
duty budget — the compile-time 100 ms constant is only the 10 Hz backstop
the runtime guard is `max()`'d with (review F8).

## Review corrections applied (2026-06-12)

A read-only review (GPT) + a 4-agent adversarial verification against the
actual `variant.h`, RadioLib 7.1.2, and TinyGPSPlus sources confirmed and
fixed: F1 age-gated freshness + `gps_age_ms`; F2 reboot-safe loss accounting
(authoritative signal = `tracker_ms` going backwards; out-of-order ≠ loss);
F3 the 1.8 V TCXO voltage now passed to `begin()`; F4 `fetch_variant.sh`
pinned to a SHA + lockfile (the "pinned" claim is now true); F5 LED2/buzzer
collision avoided; F6/F7 integer-scaled base JSON (the Orin checks `fix`
before trusting coords, no phantom 0,0, no float-printf dependency); F8
computed airtime guard; F9 checked `startReceive()` + `tx_fail` counter;
plus per-commit GNSS cadence logging (so the bench measures the L76K's real
rate, not the PMTK ACK). Both envs recompile green.

### Round 2 (blocking before Phase 3 — GPS-rate testing)

The first round fixed everything *around* the blocking-transmit bug but left
`radio.transmit()` itself blocking — the highest-severity finding was named,
not fixed. Round 2 closes it:
- **Non-blocking TX** (`startTransmit()` + `setPacketSentAction()` DIO1 ISR +
  `finishTransmit()`): the loop pumps `gps.encode()` continuously through the
  background airtime. A blocking transmit would have dropped NMEA bytes into
  the 64-byte nRF52 ring mid-sentence and made the L76K's cadence look worse
  than it is — which would have poisoned the exact 5 Hz measurement Phase 3
  exists to make. `last_tx_ms` is stamped at TX start; the boot-computed
  airtime guard guarantees the radio is always idle before the next beacon.
- **Cadence logger fixed**: `isUpdated()` is NOT self-clearing (it clears when
  `lat()`/`lng()` are read — only at the beacon interval), so the old logger
  re-fired every loop between beacons. Now keyed on commit identity
  (`millis()-age()`), logging once per real fix without touching `lat()`.
- **hacc/sats zeroed unless the fix is fresh** — the JSON can't imply quality
  for a position we won't vouch for.
RadioLib 7.1.2 TX API verified against source before writing. Both envs green.

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

## Base position & session hard-lock (reviewed + designed 2026-06-13)

How the camera's location and heading are set today (reviewed in-code), and
how direct-LoRa feeds the same path so calibration/pointing are unchanged.

**The existing mechanism (Meshtastic path):**
- **Camera position** = the base Wio's own GPS fix, exposed as
  `MeshtasticGps.get_camera_position()`. Hard-locked once at the `base_lock`
  calibration step → `CameraPose.lat/lon/alt`, persisted to the calibration
  store. One capture per session ("base_locked", pose-latched).
- **Camera heading** (`reference_heading`) = solved from GPS geometry, NOT a
  magnetometer: aim the camera at the visible remote, and `calibrate_pan_aim`
  pairs the pan encoder with the **base→remote bearing** (computed from base
  position + remote position, surfaced as `status.gps.bearing_deg`). iOS
  refuses the heading capture unless that bearing is live (needs BOTH fixes).
- **The phone is NOT in the calibration path.** `PhoneSensorPublisher` →
  `/sensors/phone` feeds the SensorHub drift/bump monitor (anchor-suspect:
  "did the tripod get knocked?"). It does not set the camera location/heading.

**What direct-LoRa adds (this firmware):** the base now reads its own L76K and
emits a `{"base":1,fix,lat_e7,lon_e7,alt_m,sats,hdop_x10,stable,hold_s}` line
at 1 Hz, alongside the relayed `{"seq":...}` tracker packets. It runs a
running-mean over each continuous good-fix run (HDOP ≤ 2.5) and sets
`stable:1` after `BASE_SETTLE_MS` (20 s) — the Orin's cue that a settled
camera position is ready to latch.

**Orin side (Codex's lane, Phase 4, not yet built):** `DirectRadioGps` must
mirror the `MeshtasticGps` contract — `get_fix()` from `{"seq":...}` lines
(the subject), `get_camera_position()` from `{"base":1,...}` lines (the
camera, only when `stable:1`). Same `NormalizedFix` seam, `gps.source:
meshtastic|direct_lora`. With that, `base_lock` AND the heading solve work
**unchanged** — both just need base + remote fixes, which direct-LoRa now
provides.

**Wio vs iPhone — decision:**
- **Camera POSITION: base Wio L76K** (primary, self-contained, matches the
  existing design). The iPhone is *not* needed for position.
- **Camera HEADING: GPS base→remote geometry** (aim at remote). No compass.
- **iPhone: the drift/bump monitor only** — optional, confirms the tripod
  hasn't moved mid-session. Not a location/heading source.

**Session hard-lock flow (direct-LoRa, at session start):**
1. Power base (on Orin USB) + remote; let the base GPS settle outdoors until
   `stable:1` (~20 s+ with sky view).
2. `base_lock` → latches the settled camera position into `CameraPose`.
3. Aim camera at the visible remote → `heading` capture solves
   `reference_heading` from the base→remote bearing + pan encoder.
4. Both persist to the calibration store; pose stays latched for the session.
