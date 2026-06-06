#!/usr/bin/env bash
# Restart the gps-server systemd service (clears in-memory seq/offset state).
# Run on the Orin:  bash /data/projects/gimbal/deploy/restart_gps.sh
# (uses sudo internally; passwordless sudo must be configured)
set -e
sudo systemctl restart gps-server
sleep 1
echo -n "gps-server: "
systemctl is-active gps-server
