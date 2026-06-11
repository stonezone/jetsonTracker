# Wio Tracker L1 Lite — GPS Frequency Optimization (2026-06-09)

## Problem

The Meshtastic firmware defaults to sending position updates **once per hour** (`position_broadcast_secs: 3600`) with **smart broadcast disabled**. This meant the remote tracker on the surfer was sending GPS fixes at intervals of 15+ minutes, making the GPS data in the WaveCam API stale and useless for coarse-pointing.

The Meshtastic app shows a 15-second minimum for broadcast interval in its dropdown, but this is purely a **UI limitation** — **the firmware itself has no minimum**. Any positive value is accepted. The nRF52840 firmware versions 2.3.x through 2.6.x use a simple pass-through:

```cpp
uint32_t getConfiguredOrDefaultMs(uint32_t configuredInterval) {
    if (configuredInterval > 0)
        return configuredInterval * 1000;  // accepts ANY value: 5, 2, even 1
    return default_broadcast_interval_secs * 1000;
}
```

(The congestion-scaling variant `getConfiguredOrDefaultMsScaled` only activates above 40 online nodes and was introduced in later firmware — irrelevant for a 2-node mesh.)

## Devices

| Role | Node ID | Location | Connection | Firmware |
|---|---|---|---|---|
| Base (camera reference) | `!38c3f1fd` | Orin, USB-A port | `/dev/ttyACM0` serial | 2.5.x |
| Remote (on surfer) | `!9f5802d5` | Subject, battery-powered | LoRa mesh to base | 2.6.10 |

## Changes Applied (final, as of 2026-06-09 session 2)

### Position Settings

| Setting | Old Value | New Value (base) | New Value (remote) | Why |
|---|---|---|---|---|
| `position_broadcast_smart_enabled` | `false` | `true` | `true` | Without this, position sends only at `position_broadcast_secs` interval (1 hour). |
| `gps_update_interval` | 30s | **5s** | **2s** | How often GPS chip polls. Remote at 2s for fastest tracking; base at 5s (USB-powered, can afford it). |
| `broadcast_smart_minimum_interval_secs` | 15s | **2s** | **2s** | Floor between sends when moving. 2s on a surfer at 10 m/s = 20m max position error. |
| `broadcast_smart_minimum_distance` | 10m | **5m** | **5m** | Sends when moved this far. 5m catches small movements. |
| `position_broadcast_secs` | 3600s | **30s** | **10s** | **Critical for BOTH:** base is stationary (tripod), so smart broadcast never triggers — 30s keeps the camera reference position flowing. Remote at **10s** (field test 2026-06-11): a surfer standing still waiting to be re-found is ALSO stationary, and a 1h fallback starved GPS reacquire. Canonical values: `docs/hardware/wio-config/`. |

**Net effect:** Remote sends position every 2s when moving >5m and every 10s even when still; base sends every 30s even when stationary. Previously: once per hour for both.

### LoRa Radio Settings (both devices must match)

| Setting | Old Value | New Value | Why |
|---|---|---|---|
| `lora.modem_preset` | `MEDIUM_FAST` (4) | **`SHORT_FAST`** (6) | Optimized for short-range (50-300m), highest data rate. Faster packet airtime (~40-60ms) → more bandwidth for 2s position updates. |

**CRITICAL: Both nodes must use the same preset.** If they drift out of sync (e.g., one power-cycles and reverts, one gets reconfigured), they cannot communicate. The symptom is "last heard" going stale in the app and GPS age climbing in the API.

### Power Settings (remote only)

| Setting | Old Value | New Value | Why |
|---|---|---|---|
| `power.ls_secs` | 300s | **4294967295** (disabled) | Light sleep after 5 min inactivity would kill GPS polling. Disabled. Battery trade-off accepted. |

### Device Settings (both devices)

| Setting | Old Value | New Value | Why |
|---|---|---|---|
| `device.node_info_broadcast_secs` | 10800s (3h) | **3600s** (1h) | Battery/uptime visibility without meaningful airtime cost. |

### Position Flags (unchanged)

Remote: `938` = POS_HEADING (512) + POS_TIMESTAMP (256) + POS_SEQ_NOS (128) + POS_BATTERY (32) + POS_DOP (8) + POS_ALT_MSL (2). Includes heading and speed for lead prediction in `gps_pointing.compute_target()`.

Base: `10` = POS_DOP (8) + POS_ALT_MSL (2). Minimal — Orin reads directly from base's nodeDB.

## How Settings Were Applied

### Remote Wio (on Mac, USB-C)

1. Plugged into Mac USB-C — detected at `/dev/cu.usbmodem1101`
2. Installed Meshtastic CLI via pipx: `pipx install meshtastic`
3. Applied settings

Commands (session 2 — final values):
```bash
# First session (5s baseline)
meshtastic --port /dev/cu.usbmodem1101 --set position.position_broadcast_smart_enabled true
meshtastic --port /dev/cu.usbmodem1101 --set position.gps_update_interval 5
meshtastic --port /dev/cu.usbmodem1101 --set position.broadcast_smart_minimum_interval_secs 5
meshtastic --port /dev/cu.usbmodem1101 --set position.broadcast_smart_minimum_distance 5
meshtastic --port /dev/cu.usbmodem1101 --set lora.modem_preset SHORT_FAST
meshtastic --port /dev/cu.usbmodem1101 --set power.ls_secs 4294967295
meshtastic --port /dev/cu.usbmodem1101 --set device.node_info_broadcast_secs 3600
meshtastic --port /dev/cu.usbmodem1101 --set position.gps_enabled true

# Second session (2s intervals)
meshtastic --port /dev/cu.usbmodem1101 --set position.broadcast_smart_minimum_interval_secs 2
meshtastic --port /dev/cu.usbmodem1101 --set position.gps_update_interval 2
```

### Base Wio (on Orin, USB serial)

1. Stop `wavecam.service` to free `/dev/ttyACM0`: `sudo systemctl stop wavecam.service`
2. Apply settings via `python3 -m meshtastic --set`
3. Restart: `sudo systemctl start wavecam.service`

Commands (session 2 — re-applied after config reversion):
```bash
ssh orin
sudo systemctl stop wavecam.service

python3 -m meshtastic --set position.position_broadcast_smart_enabled true
python3 -m meshtastic --set position.gps_update_interval 5
python3 -m meshtastic --set position.broadcast_smart_minimum_interval_secs 2
python3 -m meshtastic --set position.broadcast_smart_minimum_distance 5
python3 -m meshtastic --set position.position_broadcast_secs 30
python3 -m meshtastic --set lora.modem_preset SHORT_FAST
python3 -m meshtastic --set device.node_info_broadcast_secs 3600

sudo systemctl start wavecam.service
```

### Why CLI, not Python library

The Meshtastic Python library's `Node.writeConfig()` failed on certain protobuf fields (`position_broadcast_smart_enabled` returned "No valid config" errors). The CLI tool (`python3 -m meshtastic --set`) uses a different code path that successfully writes all fields.

## Verification (final, both sessions)

### Base Wio
```
position.position_broadcast_secs: 30           ← CRITICAL: sends when stationary
position.position_broadcast_smart_enabled: True
position.gps_update_interval: 5
position.broadcast_smart_minimum_interval_secs: 2
position.broadcast_smart_minimum_distance: 5
lora.modem_preset: 6 (SHORT_FAST)
device.node_info_broadcast_secs: 3600
```

### Remote Wio
```
position.position_broadcast_secs: 3600         ← OK: smart broadcast handles it when moving
position.position_broadcast_smart_enabled: True
position.gps_update_interval: 2
position.broadcast_smart_minimum_interval_secs: 2
position.broadcast_smart_minimum_distance: 5
lora.modem_preset: 6 (SHORT_FAST)
power.ls_secs: 4294967295 (disabled)
device.node_info_broadcast_secs: 3600
position.gps_mode: ENABLED
```

## Gotchas (hard-won)

### 1. Config reversion on power-cycle

If the Orin cold-boots with the base Wio plugged in, the Wio may power-cycle and **revert all settings to defaults**. This happened once — the base lost SHORT_FAST, smart broadcast, and all intervals. 

**Mitigation:** After any Orin cold boot, verify: `python3 -m meshtastic --get position` and `--get lora`. Re-apply if reverted.

### 2. LoRa preset mismatch = silent communication failure

If one Wio is on SHORT_FAST and the other on MEDIUM_FAST, they **cannot communicate** with no visible error. Symptoms:
- "Last heard" timestamps climb in the app
- `target_age_sec` in the API climbs past 10+ minutes
- No error messages anywhere

**Fix:** Both must match. Verify with `--get lora` on both.

### 3. Orin won't boot with Wio plugged in

U-Boot stalls on USB enumeration — it sees the Wio's USB-ACM serial gadget and tries to boot from it. 

**Fix:** Unplug the base Wio from the Orin's USB port during cold boots. Plug back in after boot completes, then restart wavecam. Warm reboots are fine.

### 4. Stationary base + smart broadcast = no broadcasts

Smart broadcast only triggers on movement. The base is stationary on a tripod, so it would never send its position — falling back to once per hour. The Meshtastic app showed the remote "1.3 miles away" because it had a stale base position.

**Fix:** Set `position_broadcast_secs: 30` on the base so it sends position even when stationary.

### 5. The app's 15-second minimum is a UI limit, not firmware

The Meshtastic app dropdown bottoms out at 15 seconds, but the firmware accepts any positive integer. The CLI bypasses the app's UI constraint. No firmware downgrade is needed.

### 6. Admin key mismatch blocks remote config over mesh

The base Wio cannot configure the remote over the LoRa mesh (`ADMIN_PUBLIC_KEY_UNAUTHORIZED`). Each Wio has its own admin key from initial pairing. Remote config must be done via USB (plug into Mac) or via the Meshtastic app over Bluetooth.

## Rollback

```bash
meshtastic --set position.position_broadcast_smart_enabled false
meshtastic --set position.gps_update_interval 30
meshtastic --set position.broadcast_smart_minimum_interval_secs 15
meshtastic --set position.broadcast_smart_minimum_distance 10
meshtastic --set position.position_broadcast_secs 3600
meshtastic --set lora.modem_preset MEDIUM_FAST
meshtastic --set power.ls_secs 300
meshtastic --set device.node_info_broadcast_secs 10800
```
