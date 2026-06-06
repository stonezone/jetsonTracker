#!/usr/bin/env bash
# Install + start the GPS server as a systemd service on the Orin.
#
# Run on the Orin AS ROOT (elevated), e.g.:
#     sudo bash /data/projects/gimbal/deploy/install_gps_service.sh
#
# It stops any dev/nohup gps_server instance (to free :8765), installs the unit,
# and enables it so it auto-restarts and survives reboot.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Must run as root (prefix with the privilege-escalation command)." >&2
  exit 1
fi

SRC=/data/projects/gimbal/deploy/gps-server.service
DST=/etc/systemd/system/gps-server.service

# Stop the managed service first (no-op on first install), then kill any
# non-systemd / nohup instance still on :8765. Match relative AND absolute
# cmdlines (a nohup'd start shows up as "python3 gps_server.py", not the path).
systemctl stop gps-server 2>/dev/null || true
pkill -f gps_server.py 2>/dev/null || true
sleep 1

install -m 0644 "$SRC" "$DST"
systemctl daemon-reload
systemctl enable --now gps-server
sleep 1
systemctl --no-pager status gps-server || true
echo
echo "Installed. Follow logs with: journalctl -u gps-server -f"
