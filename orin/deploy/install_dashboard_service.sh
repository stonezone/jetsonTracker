#!/usr/bin/env bash
# Install + start the operator dashboard as a systemd service on the Orin.
# Run on the Orin (passwordless sudo):
#   bash /data/projects/gimbal/deploy/install_dashboard_service.sh
set -e
sudo systemctl stop dashboard 2>/dev/null || true
pkill -f dashboard/dashboard.py 2>/dev/null || true
sleep 1
sudo install -m 0644 /data/projects/gimbal/deploy/dashboard.service /etc/systemd/system/dashboard.service
sudo systemctl daemon-reload
sudo systemctl enable --now dashboard
sleep 1
sudo systemctl --no-pager status dashboard || true
echo "Dashboard: http://$(hostname -I | awk '{print $1}'):8080"
