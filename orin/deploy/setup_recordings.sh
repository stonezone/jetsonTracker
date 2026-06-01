#!/usr/bin/env bash
# One-time: create the recordings dir on the NVMe, owned by zack.
# Run on the Orin (passwordless sudo): bash /data/projects/gimbal/deploy/setup_recordings.sh
set -e
sudo mkdir -p /data/recordings
sudo chown -R zack:zack /data/recordings
ls -ld /data/recordings
