#!/usr/bin/env bash
# Restart the dashboard service and probe the new endpoints.
set -u
sudo systemctl restart dashboard
sleep 2
echo "active=$(systemctl is-active dashboard)"
echo "--- /api/session ---"
curl -s --max-time 5 http://localhost:8080/api/session
echo
echo "--- /api/calibration/state (drift block) ---"
curl -s --max-time 5 http://localhost:8080/api/calibration/state
echo
echo "--- base-drift offline test on Orin ---"
cd /data/projects/gimbal && python3 scripts/test_base_drift.py 2>&1 | tail -2
