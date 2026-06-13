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

## Phase 3 — outdoor GPS + rate measurement

`tools/read_base.py` is the instrument. Plug the base into the Orin (it
enumerates as a CDC ACM on Linux), power the tracker, take the tracker
outside with sky view, then:

```sh
python3 tools/read_base.py --port /dev/ttyACM0   # Ctrl-C to stop
```

It prints rolling link + GPS stats: delivered packet rate, loss, RSSI/SNR
spread, the remote's `fix` + GPS age, and the base's settle (`stable`/`hold`).
What to confirm outdoors:
- **Remote fix flips to 1**, `gps_age` drops from 65535 to small ms, and on
  the tracker's own USB serial the `[gps] update dt=` lines show the **real
  L76K cadence** (the open "is it really 5 Hz?" question — measure it, don't
  assume).
- **Base reaches `stable:1`** (~20 s of good fix) — that's the cue a settled
  camera position is ready for `base_lock`.
- **Loss stays low and RSSI holds** as the tracker moves away (the range/
  body-shadowing test toward <2 km).
