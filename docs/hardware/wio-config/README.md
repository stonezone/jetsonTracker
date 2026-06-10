# Wio Tracker Canonical Configs

The two `*.intended.yaml` files are the single source of truth for how both Wio
Tracker L1 Lites must be configured. Settings have reverted in the field
(power-cycles + one-at-a-time `--set` writes, which commit/reboot per call), so:

**Rule: apply config as ONE operation, verify after every power event.**

## Apply (restore a device to canonical)

```bash
# Remote: plug into the Mac (USB-C). Base: on the Orin AFTER
#   sudo systemctl stop wavecam.service   (frees /dev/ttyACM0)
meshtastic --port <PORT> --configure wio-remote.intended.yaml   # or wio-base
# Base only: sudo systemctl start wavecam.service when done
```

If `--configure` rejects the file (firmware-version field drift), fall back to a
SINGLE batched command — all `--set` flags in one invocation so the device
commits once:

```bash
meshtastic --port <PORT> \
  --set position.position_broadcast_smart_enabled true \
  --set position.gps_update_interval 2 \
  ... (every value from the intended file)
```

## After first successful apply: replace intended with a real export

```bash
meshtastic --port <PORT> --export-config > wio-remote.canonical.yaml
```

Commit the export next to the intended file. From then on the export is
canonical and `--configure` restores it byte-for-byte.

## Pre-session verification

```bash
./verify_wios.sh <PORT>     # read-only; prints the keys that matter
```

Run on BOTH devices before every session, and after any Orin cold boot
(the base Wio power-cycles with the Orin and can revert).

## Known facts / gotchas

- **Base firmware must be upgraded to match the remote (2.6.10).** The base is
  on 2.5.x; Wio L1 Lite GPS support matured in 2.6.x — suspected cause of the
  base's no-fix problem. Flash first, then re-apply config, then check
  `position.gps_mode` is ENABLED before suspecting the antenna.
- `power.ls_secs` is ESP32-only; the nRF52840 ignores it. The "300 readback"
  is cosmetic — stop chasing it.
- Both devices MUST share `lora.modem_preset` (SHORT_FAST). Mismatch = silent
  mesh failure (climbing `target_age_sec`, no errors).
- The remote's `position_broadcast_secs: 30` is a deliberate safety floor: if
  smart broadcast ever reverts/misbehaves, worst case is 30 s updates instead
  of 1 hour.
- The base's `position_broadcast_secs: 30` is required until the latched-base
  backend change is deployed (pipeline currently needs a continuously fresh
  base position). After that deploy it can be relaxed (e.g. 600).
