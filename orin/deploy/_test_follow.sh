#!/usr/bin/env bash
# RETIRED: legacy :8080 dashboard follow endpoints no longer exist.
# Use the WaveCam web UI on :8088 or the native iOS PTZ/Live tabs.
set -u
echo "Retired. Probe active WaveCam status instead:"
curl -s --max-time 5 http://localhost:8088/api/v1/status
