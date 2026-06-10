#!/usr/bin/env bash
# Read-only pre-session check for a Wio Tracker. Run against BOTH devices
# before every session and after any Orin cold boot.
#   ./verify_wios.sh /dev/cu.usbmodem1101     (remote on the Mac)
#   ./verify_wios.sh /dev/ttyACM0             (base on the Orin; stop wavecam.service first)
set -euo pipefail
PORT="${1:?usage: verify_wios.sh <serial-port>}"

echo "== position (want: gps_mode ENABLED, smart on, interval 2/5, min-int 2, min-dist 5, broadcast 30) =="
meshtastic --port "$PORT" --get position

echo "== lora (want: modem_preset SHORT_FAST on BOTH devices) =="
meshtastic --port "$PORT" --get lora.modem_preset

echo "== device =="
meshtastic --port "$PORT" --get device.node_info_broadcast_secs

echo
echo "Checklist: [ ] gps_mode ENABLED   [ ] presets match on both   [ ] broadcast secs 30"
echo "Then watch /api/v1/status gps block for 2 min: target_age_sec low, base_age_sec present, reader_alive true."
