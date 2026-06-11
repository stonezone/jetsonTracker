#!/usr/bin/env bash
# Install WaveCam Tier-0 watchdog systemd units on the rig.
#
# Intended deploy-window usage on the Orin:
#   sudo bash /data/projects/gimbal/wavecam/ops/install-watchdog.sh
set -euo pipefail

[[ "$(id -u)" -eq 0 ]] || { echo "ERROR: must run as root" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WAVECAM_ROOT="${WAVECAM_ROOT:-/data/projects/gimbal/wavecam}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
SERVICE_DROPIN_DIR="${SYSTEMD_DIR}/wavecam.service.d"

install -d -m 0755 "${WAVECAM_ROOT}/ops"
install -d -m 0755 "${SERVICE_DROPIN_DIR}"

TARGET_WATCHDOG="${WAVECAM_ROOT}/ops/watchdog.sh"
if [[ "${SCRIPT_DIR}/watchdog.sh" == "${TARGET_WATCHDOG}" ]]; then
  chmod 0755 "${TARGET_WATCHDOG}"
else
  install -m 0755 "${SCRIPT_DIR}/watchdog.sh" "${TARGET_WATCHDOG}"
fi
install -m 0644 "${SCRIPT_DIR}/wavecam.service.d/override.conf" "${SERVICE_DROPIN_DIR}/override.conf"
install -m 0644 "${SCRIPT_DIR}/wavecam-watchdog.service" "${SYSTEMD_DIR}/wavecam-watchdog.service"
install -m 0644 "${SCRIPT_DIR}/wavecam-watchdog.timer" "${SYSTEMD_DIR}/wavecam-watchdog.timer"

systemctl daemon-reload
systemctl enable --now wavecam-watchdog.timer
systemctl list-timers wavecam-watchdog.timer --no-pager
