# Direct LoRa tracker firmware (secondary GPS solution — PREPARED, NOT ACTIVE)

One-way stateless GPS beacon for the 2x Seeed Wio Tracker L1, replacing
Meshtastic IF driveway shadow scoring ever shows the mesh transport is the
binding constraint. Full design + phase gates:
`docs/superpowers/specs/2026-06-12-direct-lora-tracker.md`.

## Build
```sh
./fetch_variant.sh            # once: vendor the Seeed board variant (pinned)
pio run -e tracker            # surfer node:  L76K -> 32B packet -> SX1262 TX
pio run -e base               # Orin node:    SX1262 RX -> JSONL on USB serial
# Each build auto-emits .pio/build/<env>/firmware.uf2 (post:tools/uf2_postbuild.py).
```

## Flash (UF2 only)
Double-tap reset -> a DFU drive (e.g. WIOTRACKER/NRF52BOOT) appears -> drag the
matching `.pio/build/<env>/firmware.uf2` onto it (TRACKER firmware on the
surfer unit, BASE firmware on the Orin unit — label them). The board reboots
into the new firmware and the drive disappears. **Never use nRF OTA / BLE
flashing — documented brick risk on this board (Seeed).** The base builder's
`firmware.zip` is the OTA package — do NOT use it.

## LED language
- tracker: green (LED1) heartbeats while a FRESH fix is flowing, solid when
  GNSS is stale; fast blink = radio-init failure.
- base: green (LED1) slow heartbeat.
- NOTE: LED2 is unused on purpose — it is wired to the BUZZER on this board.

## Bring-up order (do not skip)
1. Flash proof (hello UF2 boots, serial logs).
2. Radio proof A->B, no GPS: watch seq/RSSI/SNR/lost on the base.
3. GPS beacon at 2Hz; MEASURE real outdoor L76K cadence before trying 5Hz.
4. Orin `DirectRadioGps` reader (config `gps.source`, default meshtastic).
5. External MAX-M10S only if the L76K is the proven limit.
