# Wio Tracker L1 Lite — GPS / LoRa Optimization


## Problem

The original Meshtastic-based GPS path defaulted to very slow position updates and required brittle per-session configuration (`SHORT_FAST` preset, smart-broadcast intervals, `power.ls_secs` disable). Settings reverted on power-cycle, preset mismatches silently broke the link, and the base Wio on the Orin USB rail often failed to acquire fixes due to host RF noise.

The current stack replaces Meshtastic with the custom direct-LoRa firmware in `firmware/direct-lora/`. This page documents how to get the best GPS/LoRa performance from the Wio hardware on the live stack.

## Devices

| Role | Node ID (config) | Location | Connection | Firmware |
|---|---|---|---|---|
| Base (camera reference) | `!38c3f1fd` | Orin, USB-A data | `/dev/ttyACM0` serial | `firmware/direct-lora/` base env |
| Remote (on surfer) | `!9f5802d5` | Subject, in a waterproof case | LoRa direct to base | `firmware/direct-lora/` tracker env |

## Direct-LoRa firmware knobs

The radio and beacon behavior are **compile-time constants** in the firmware. Re-flash to change them.

| Setting | Typical value | Effect |
|---|---|---|
| Beacon interval | 1000 ms (1 Hz) default | How often the tracker sends a LoRa packet. Can be lowered to 200 ms (5 Hz) only after outdoor validation that the L76K GNSS delivers fresh fixes that fast. |
| LoRa region | `US915` | 902–928 MHz, legal in the US. |
| LoRa config | SF7 / BW250 / CR4:5 | ~35 ms airtime for a 32-byte payload at SF7/BW250. Fast, short-range (50–300 m). |
| GNSS protocol | CASIC/PCAS at 9600 baud | The L76K module default. Earlier PMTK/57600 attempts caused a baud mismatch and zero-fix. |
| GNSS nav rate | 1 Hz default; up to 5 Hz | Set via `PCAS11`. Must be matched to beacon interval and verified outdoors. |

**Net effect:** The tracker sends a 32-byte packet on every beacon tick. The base emits a JSONL line for each packet plus a 1 Hz `{"base":1,...}` camera-position line. There is no mesh, no smart-broadcast threshold, and no preset to mismatch.

## Power / RF setup

### Base Wio
- **Battery-powered for acquisition.** The base Wio now has a battery installed. The Orin USB rail injects enough RF noise that the L76K can report 0 sats, so let the base acquire its fix on battery power with clear sky, then connect USB data to the Orin.
- Keep the base U.FL antenna vertical and clear of the Orin/camera metal case.
- After any base Wio reboot, re-enumeration of `/dev/ttyACM0` can leave the ingest handle stale. Restart `wavecam.service` if `reader_alive` goes false.

### Tracker Wio
- Wear it in a waterproof case with the antenna oriented upward/outward. Body shadowing and case losses dominate link budget.
- Keep the cell charged. Low voltage (<3500 mV on a 1S LiPo) reduces radio TX power and can brown-out the GPS/radio.

## Build / flash

```bash
cd firmware/direct-lora
pio run -e tracker   # produces .pio/build/tracker/firmware.uf2
pio run -e base      # produces .pio/build/base/firmware.uf2
```

Flash via UF2 drag-drop (double-tap reset to enter bootloader). **Never use nRF OTA flashing** — documented brick risk on the Wio. See `firmware/direct-lora/README.md` for the full procedure.

## Verification

On the Orin:

```bash
journalctl -u wavecam.service | grep -i DirectRadioGps
```

Expected:
- `DirectRadioGps connected`
- Periodic `{"seq":N,...}` tracker packets
- Periodic `{"base":1,...}` base position lines

Live status:

```bash
curl -s http://192.168.1.155:8088/api/v1/status | python3 -m json.tool
```

Expected:
- `gps.source` = `direct_lora`
- `reader_alive` = `true`
- `target_sats` > 0
- `target_age_sec` low and steady

## Gotchas (hard-won)

### 1. Orin won't boot with Wio plugged in

U-Boot stalls on USB enumeration — it sees the Wio's USB-ACM serial gadget and tries to boot from it.

**Fix:** Unplug the base Wio from the Orin's USB port during cold boots. Plug back in after boot completes, then restart `wavecam.service`. Warm reboots are usually fine.

### 2. USB rail noise kills base GPS acquisition

A base Wio powered solely from the Orin USB-A port can show 0 sats even outdoors.

**Fix:** Use the Wio's battery port for GPS acquisition. Once the fix is stable, USB data to the Orin is fine.

### 3. L76K speaks CASIC/PCAS at 9600 baud

The Quectel L76K module used on the Wio Tracker L1 expects CASIC/PCAS commands and defaults to 9600 baud. Using PMTK/57600 results in no NMEA output and zero fixes.

**Fix:** Use the live firmware's `gps_l76k.h` implementation. Do not copy old PMTK snippets.

### 4. 5 Hz must be measured, not assumed

The L76K accepts a 5 Hz navigation-rate command, but real-world fresh-fix cadence depends on sky view, multi-GNSS selection, and NMEA sentence load. Always measure the actual `update dt` from tracker serial logs outdoors before declaring 5 Hz operational.

### 5. Antenna orientation matters more than SF

At surf range, body shadowing and antenna orientation matter far more than spreading factor. SF7/BW250 is the default; raise SF only after confirming the link budget is the actual limiting factor.

### 6. Mismatched firmware between base and tracker

The two Wios must run firmware compiled with matching radio constants (frequency, SF, BW, CR). If one is re-flashed with different constants, the other stops receiving packets with no visible error.

**Fix:** Flash both Wios from the same firmware revision when changing radio config.

## Rollback

To revert to the archived Meshtastic path (not recommended; kept only for reference):
- Re-flash both Wios with Meshtastic firmware.
- Re-apply the old config from `archive/legacy-20260606/`.
- Set `gps.source: meshtastic` in the Orin config overlay.

The direct-LoRa path is the supported live configuration.
