#!/usr/bin/env bash
# Tier-0 deterministic WaveCam watchdog.
#
# This script intentionally uses simple local evidence only: localhost health,
# systemd service state, and small files in /run for consecutive-failure and
# action-rate state. It must not call LLMs, cloud services, SSH, or deploy tools.
set -euo pipefail

SERVICE_NAME="${WAVECAM_SERVICE_NAME:-wavecam.service}"
HEALTH_URL="${WAVECAM_HEALTH_URL:-http://localhost:8088/api/v1/health}"
MEDIA_STOP_URL="${WAVECAM_MEDIA_STOP_URL:-http://localhost:8088/api/v1/media/record/stop}"
STATE_DIR="${WAVECAM_WATCHDOG_STATE_DIR:-/run/wavecam-watchdog}"
CURL_MAX_TIME="${WAVECAM_WATCHDOG_CURL_MAX_TIME:-5}"
RATE_LIMIT_SEC="${WAVECAM_WATCHDOG_RATE_LIMIT_SEC:-600}"

HEALTH_FAIL_COUNT_FILE="${STATE_DIR}/health_failures"
GPS_FAIL_COUNT_FILE="${STATE_DIR}/gps_reader_failures"
LAST_ACTION_FILE="${STATE_DIR}/last_action_unix"

GPS_READER_OK_PATH="components.gps_reader.ok"
DISK_FREE_GB_PATH="components.disk.detail.free_gb"

mkdir -p "${STATE_DIR}"

log() {
  logger -t wavecam-watchdog -- "$*"
}

read_counter() {
  local path="$1"
  if [[ -f "${path}" ]]; then
    tr -cd '0-9' < "${path}"
  else
    printf '0'
  fi
}

write_counter() {
  local path="$1"
  local value="$2"
  printf '%s\n' "${value}" > "${path}"
}

reset_counter() {
  write_counter "$1" 0
}

increment_counter() {
  local path="$1"
  local current
  current="$(read_counter "${path}")"
  current="${current:-0}"
  write_counter "${path}" "$((current + 1))"
}

last_action_age_sec() {
  local now last
  now="$(date +%s)"
  if [[ ! -f "${LAST_ACTION_FILE}" ]]; then
    printf '%s\n' "${RATE_LIMIT_SEC}"
    return
  fi
  last="$(tr -cd '0-9' < "${LAST_ACTION_FILE}")"
  last="${last:-0}"
  printf '%s\n' "$((now - last))"
}

action_allowed() {
  local rule="$1"
  local evidence="$2"
  local age
  age="$(last_action_age_sec)"
  if (( age < RATE_LIMIT_SEC )); then
    log "rule=${rule} action=suppressed reason=rate_limited age_sec=${age} limit_sec=${RATE_LIMIT_SEC} evidence=${evidence}"
    return 1
  fi
  date +%s > "${LAST_ACTION_FILE}"
  return 0
}

restart_wavecam() {
  local rule="$1"
  local evidence="$2"
  if ! action_allowed "${rule}" "${evidence}"; then
    return 0
  fi
  log "rule=${rule} action=restart service=${SERVICE_NAME} evidence=${evidence}"
  if systemctl restart "${SERVICE_NAME}"; then
    log "rule=${rule} result=restart_ok service=${SERVICE_NAME}"
  else
    local rc=$?
    log "rule=${rule} result=restart_failed service=${SERVICE_NAME} rc=${rc}"
    return "${rc}"
  fi
}

stop_recording() {
  local rule="$1"
  local evidence="$2"
  if ! action_allowed "${rule}" "${evidence}"; then
    return 0
  fi
  log "rule=${rule} action=stop_recording url=${MEDIA_STOP_URL} evidence=${evidence}"
  if curl --silent --show-error --max-time "${CURL_MAX_TIME}" -X POST "${MEDIA_STOP_URL}" >/dev/null; then
    log "rule=${rule} result=stop_recording_ok"
  else
    local rc=$?
    log "rule=${rule} result=stop_recording_failed rc=${rc}"
    return "${rc}"
  fi
}

json_path() {
  local path="$1"
  python3 -c '
import json
import sys

path = sys.argv[1].split(".")
try:
    value = json.load(sys.stdin)
    for part in path:
        value = value[part]
except Exception:
    print("__missing__")
    sys.exit(0)

if isinstance(value, bool):
    print("true" if value else "false")
elif value is None:
    print("null")
else:
    print(value)
' "${path}"
}

record_health_fetch_failure() {
  local rule="$1"
  local evidence="$2"
  increment_counter "${HEALTH_FAIL_COUNT_FILE}"
  local failures
  failures="$(read_counter "${HEALTH_FAIL_COUNT_FILE}")"
  log "rule=${rule} action=observe failures=${failures} evidence=${evidence}"
  if (( failures >= 2 )); then
    restart_wavecam "${rule}" "failures=${failures};${evidence}"
  fi
}

health_json=""
if ! health_json="$(curl --silent --show-error --fail --max-time "${CURL_MAX_TIME}" "${HEALTH_URL}")"; then
  record_health_fetch_failure "health_unreachable_twice" "url=${HEALTH_URL}"
  exit 0
fi

gps_reader_ok="$(printf '%s' "${health_json}" | json_path "${GPS_READER_OK_PATH}")"
disk_free_gb="$(printf '%s' "${health_json}" | json_path "${DISK_FREE_GB_PATH}")"

if [[ "${gps_reader_ok}" == "__missing__" || "${disk_free_gb}" == "__missing__" ]]; then
  record_health_fetch_failure "health_json_invalid_twice" "gps_reader_ok=${gps_reader_ok};disk_free_gb=${disk_free_gb}"
  exit 0
fi
reset_counter "${HEALTH_FAIL_COUNT_FILE}"

if systemctl is-active --quiet "${SERVICE_NAME}"; then
  if [[ "${gps_reader_ok}" == "false" ]]; then
    increment_counter "${GPS_FAIL_COUNT_FILE}"
    gps_failures="$(read_counter "${GPS_FAIL_COUNT_FILE}")"
    log "rule=gps_reader_unhealthy_twice action=observe failures=${gps_failures} evidence=${GPS_READER_OK_PATH}=false"
    if (( gps_failures >= 2 )); then
      restart_wavecam "gps_reader_unhealthy_twice" "failures=${gps_failures};${GPS_READER_OK_PATH}=false"
    fi
  else
    reset_counter "${GPS_FAIL_COUNT_FILE}"
  fi
else
  reset_counter "${GPS_FAIL_COUNT_FILE}"
fi

if python3 -c 'import sys; raise SystemExit(0 if float(sys.argv[1]) < 5.0 else 1)' "${disk_free_gb}"; then
  stop_recording "disk_free_below_5gb" "${DISK_FREE_GB_PATH}=${disk_free_gb}"
fi
