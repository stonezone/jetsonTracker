# Legacy Archive - 2026-06-06

This archive preserves retired project code and documentation without keeping it
in the active WaveCam project tree.

Current WaveCam scope is:

- Jetson Orin running `orin/wavecam/`
- Prisual PTZ camera over RTSP plus RAW VISCA UDP
- iOS WaveCam operator app
- Future Wio/LoRa GPS cueing

The old Apple Watch/iPhone/Cloudflare GPS relay, STM32/Nucleo stepper control,
DRV8825 wiring, and custom 3D printed gimbal are not active runtime paths.

## Contents

| Path | What it contains |
|---|---|
| `apple-gps-cloudflare/` | Watch/iPhone GPS relay docs, Cloudflare config, old `gps_server.py`, GPS replay/monitor/test scripts, old `run_tracker.py`, GPS service files, and archived `gps-relay-framework` submodule pointer |
| `stm32-nucleo-stepper/` | Nucleo firmware, DRV8825 wiring docs, serial protocol, limit switch docs, and old Orin UART gimbal controller |
| `custom-printed-gimbal/` | Old mechanical gimbal notes and calibration docs |
| `retired-dashboard/` | Old `:8080` dashboard service, dashboard Python files, follow probe, and dashboard drift test |
| `retired-docs/` | Superseded architecture, end-to-end test, Orin setup, failure-mode, and BOM docs |

## Restore Notes

These files were moved with `git mv`, so normal Git history still tracks them.

The archived GPS relay is still a Git submodule. To populate it after checkout:

```bash
git submodule update --init archive/legacy-20260606/apple-gps-cloudflare/gps-relay-framework
```

Do not copy archived service files back onto the Orin without checking the
current `wavecam.service` runtime first. The active backend is `orin/wavecam/`
on port `8088`; the old dashboard and GPS services are intentionally retired.
