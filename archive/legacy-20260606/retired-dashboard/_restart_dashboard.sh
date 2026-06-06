#!/usr/bin/env bash
# Retired dashboard smoke check. The active web UI is wavecam.service on :8088.
set -u
echo "dashboard=$(systemctl is-active dashboard.service 2>/dev/null || true)"
echo "wavecam=$(systemctl is-active wavecam.service 2>/dev/null || true)"
echo "--- /api/v1/status ---"
curl -s --max-time 5 http://localhost:8088/api/v1/status
echo
echo "--- / ---"
curl -s --max-time 5 http://localhost:8088/ | sed -n '1,5p'
echo
echo "--- base-drift offline test on Orin ---"
cd /data/projects/gimbal && python3 scripts/test_base_drift.py 2>&1 | tail -2
