#!/usr/bin/env bash
# Align the Orin clock with the Watch/iPhone: Hawaii timezone + NTP time sync.
# Epoch (UTC) timestamps drive the GPS pipeline, so this mainly fixes display TZ
# and ensures the UTC clock is NTP-correct. Run on the Orin (passwordless sudo):
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
