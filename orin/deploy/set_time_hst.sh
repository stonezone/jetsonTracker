#!/usr/bin/env bash
# Align the Orin clock: Hawaii timezone + NTP time sync.
# UTC timestamps drive logs, media metadata, and future GPS cueing, so this
# mainly fixes display timezone and keeps the UTC clock NTP-correct.
# Run on the Orin:
#   bash /data/projects/gimbal/deploy/set_time_hst.sh
set -e
sudo timedatectl set-timezone Pacific/Honolulu
sudo timedatectl set-ntp true || true
sudo systemctl restart systemd-timesyncd 2>/dev/null || true
sleep 3
echo "--- timedatectl ---"
timedatectl status 2>/dev/null | grep -iE "Local time|Universal|Time zone|System clock|NTP" || timedatectl
echo "--- date ---"
date
