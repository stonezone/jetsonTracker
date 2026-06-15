# Direct LoRa tracker firmware (active GPS source)

One-way stateless GPS beacon for the 2× Seeed Wio Tracker L1. This is the
**live GPS source** for WaveCam, replacing the earlier Meshtastic path.
The Orin reads the base Wio's JSONL output via `DirectRadioGps`
(`orin/wavecam/wavecam/gps_direct_lora.py`).

Full design + phase gates:
`docs/superpowers/specs/2026-06-12-direct-lora-tracker.md`.

> **Live Orin config:** `gps.source: direct_lora` is set in the rig's
> `config.local.yaml` overlay (`/data/projects/gimbal/wavecam/config.local.yaml`).
> The base Wio enumerates as `/dev/ttyACM0` on the Orin.

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
- base: green (LED1) slow heartbeat; quick blinks = LoRa packets arriving from
  the tracker.
- NOTE: LED2 is unused on purpose — it is wired to the BUZZER on this board.

## Bring-up order (do not skip)

1. Flash proof (hello UF2 boots, serial logs).
2. Radio proof A->B, no GPS: watch seq/RSSI/SNR/lost on the base.
3. GPS beacon at 2Hz; MEASURE real outdoor L76K cadence before trying 5Hz.
4. Orin `DirectRadioGps` reader (ensure `gps.source: direct_lora` in
   `config.local.yaml`).
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

## Quick live checks

```bash
# On the Orin
systemctl is-active wavecam.service
curl -s http://localhost:8088/api/v1/status | python3 -m json.tool | grep -A5 '"gps"'
# expect: "source": "direct_lora", "reader_alive": true, sats > 0
```
