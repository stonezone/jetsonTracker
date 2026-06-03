#!/usr/bin/env bash
# Retire the legacy :8080 operator dashboard.
# WaveCam's active web UI now runs under wavecam.service on :8088.
set -e
sudo systemctl disable --now dashboard.service 2>/dev/null || true
pkill -f dashboard/dashboard.py 2>/dev/null || true
sudo systemctl daemon-reload
echo "Legacy dashboard.service disabled."
echo "WaveCam web UI: http://$(hostname -I | awk '{print $1}'):8088"
