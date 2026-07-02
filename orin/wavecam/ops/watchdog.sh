#!/usr/bin/env bash
# Tier-0 deterministic WaveCam watchdog.
#
# This script intentionally uses simple local evidence only: localhost health,
# systemd service state, and small files in /run for consecutive-failure and
# action-rate state. It must not call LLMs, cloud services, SSH, or deploy tools.
set -euo pipefail

SERVICE_NAME="${WAVECAM_SERVICE_NAME:-wavecam.service}"
HEALTH_URL="${WAVECAM_HEALTH_URL:-http://localhost:8088/api/v1/health}"
STATUS_URL="${WAVECAM_STATUS_URL:-http://localhost:8088/api/v1/status}"
MEDIA_STOP_URL="${WAVECAM_MEDIA_STOP_URL:-http://localhost:8088/api/v1/media/record/stop}"
STATE_DIR="${WAVECAM_WATCHDOG_STATE_DIR:-/run/wavecam-watchdog}"
CURL_MAX_TIME="${WAVECAM_WATCHDOG_CURL_MAX_TIME:-5}"
RATE_LIMIT_SEC="${WAVECAM_WATCHDOG_RATE_LIMIT_SEC:-600}"
# Optional bearer token (H3): when the control API has auth enabled, set
# WAVECAM_WATCHDOG_TOKEN (e.g. via the systemd unit's Environment=) and every
# curl below sends it. Unset = legacy no-auth behavior.
WATCHDOG_TOKEN="${WAVECAM_WATCHDOG_TOKEN:-}"

CURL_AUTH=()
if [[ -n "${WATCHDOG_TOKEN}" ]]; then
  CURL_AUTH=(-H "Authorization: Bearer ${WATCHDOG_TOKEN}")
fi

HEALTH_FAIL_COUNT_FILE="${STATE_DIR}/health_failures"
GPS_FAIL_COUNT_FILE="${STATE_DIR}/gps_reader_failures"
LOOP_FAIL_COUNT_FILE="${STATE_DIR}/loop_failures"
LAST_ACTION_FILE="${STATE_DIR}/last_action_unix"
HEALTH_BODY_FILE="${STATE_DIR}/last_health_body"

GPS_READER_OK_PATH="components.gps_reader.ok"
DISK_FREE_GB_PATH="components.disk.detail.free_gb"
LOOP_OK_PATH="components.loop.ok"
CAPTURE_FPS_PATH="components.capture.detail.fps"

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

# H2: a restart while the operator KILL latch is set would clear the latch
# (it does not persist across restarts) and hand motion authority back to
# autonomy. Returns 0 (killed) only on a confirmed safety.killed=true; an
# unreachable /status must NOT block a restart (the whole point of the
# watchdog is a wedged service).
service_killed() {
  local status_json killed
  if ! status_json="$(curl --silent --fail --max-time "${CURL_MAX_TIME}" \
      ${CURL_AUTH[@]+"${CURL_AUTH[@]}"} "${STATUS_URL}")"; then
    return 1
  fi
  killed="$(printf '%s' "${status_json}" | json_path "safety.killed")"
  [[ "${killed}" == "true" ]]
}

restart_wavecam() {
  local rule="$1"
  local evidence="$2"
  if service_killed; then
    log "rule=${rule} action=skipped reason=safety_killed detail=restart_would_clear_kill_latch evidence=${evidence}"
    return 0
  fi
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
  # H3: --fail catches HTTP-level errors (401/403/5xx), and the body must say
  # "ok": true — the control API returns refusals as 200s with ok:false.
  local stop_body
  if stop_body="$(curl --silent --show-error --fail --max-time "${CURL_MAX_TIME}" \
      ${CURL_AUTH[@]+"${CURL_AUTH[@]}"} -X POST "${MEDIA_STOP_URL}")"; then
    if printf '%s' "${stop_body}" | grep -q '"ok"[[:space:]]*:[[:space:]]*true'; then
      log "rule=${rule} result=stop_recording_ok"
    else
      log "rule=${rule} result=stop_recording_failed reason=ok_not_true body=${stop_body:0:200}"
      return 1
    fi
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

# H3: capture the HTTP status so an auth failure (401/403 — the service IS up,
# the watchdog's token is wrong/missing) is never treated as "unreachable" and
# never escalates to a restart, which would not fix auth anyway.
http_code="$(curl --silent --max-time "${CURL_MAX_TIME}" \
    ${CURL_AUTH[@]+"${CURL_AUTH[@]}"} \
    -o "${HEALTH_BODY_FILE}" -w '%{http_code}' "${HEALTH_URL}")" || http_code="000"

case "${http_code}" in
  401|403)
    log "rule=health_auth result=auth_error http_code=${http_code} url=${HEALTH_URL} hint=check_WAVECAM_WATCHDOG_TOKEN"
    reset_counter "${HEALTH_FAIL_COUNT_FILE}"
    exit 0
    ;;
  2*)
    health_json="$(cat "${HEALTH_BODY_FILE}")"
    ;;
  *)
    record_health_fetch_failure "health_unreachable_twice" "url=${HEALTH_URL};http_code=${http_code}"
    exit 0
    ;;
esac

gps_reader_ok="$(printf '%s' "${health_json}" | json_path "${GPS_READER_OK_PATH}")"
disk_free_gb="$(printf '%s' "${health_json}" | json_path "${DISK_FREE_GB_PATH}")"
loop_ok="$(printf '%s' "${health_json}" | json_path "${LOOP_OK_PATH}")"
capture_fps="$(printf '%s' "${health_json}" | json_path "${CAPTURE_FPS_PATH}")"

# H2: components.gps_reader is only present when a GPS reader is configured.
# Its absence means gps is disabled on this rig — that is healthy, not invalid
# JSON (the old check restart-looped a gps-less rig every 2 cycles). The disk
# component is always emitted, so it stays the malformed-payload sentinel.
if [[ "${disk_free_gb}" == "__missing__" ]]; then
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
    # true OR __missing__ (gps disabled) both count as healthy.
    reset_counter "${GPS_FAIL_COUNT_FILE}"
  fi

  # H4: dead vision loop while the service claims active. Two zombie rigs
  # (API answering, vision loop dead) passed the old checks. loop.ok goes
  # false when the loop heartbeat stales; capture fps==0 catches a live loop
  # with a frozen grabber. Missing components are NOT counted (feature-detect:
  # early boot / older backends must not restart-loop).
  loop_dead=0
  if [[ "${loop_ok}" == "false" ]]; then
    loop_dead=1
  fi
  if [[ "${capture_fps}" != "__missing__" && "${capture_fps}" != "null" ]]; then
    if python3 -c 'import sys; raise SystemExit(0 if float(sys.argv[1]) == 0.0 else 1)' "${capture_fps}" 2>/dev/null; then
      loop_dead=1
    fi
  fi
  if (( loop_dead )); then
    increment_counter "${LOOP_FAIL_COUNT_FILE}"
    loop_failures="$(read_counter "${LOOP_FAIL_COUNT_FILE}")"
    log "rule=loop_dead_twice action=observe failures=${loop_failures} evidence=${LOOP_OK_PATH}=${loop_ok};${CAPTURE_FPS_PATH}=${capture_fps}"
    if (( loop_failures >= 2 )); then
      restart_wavecam "loop_dead_twice" "failures=${loop_failures};${LOOP_OK_PATH}=${loop_ok};${CAPTURE_FPS_PATH}=${capture_fps}"
    fi
  else
    reset_counter "${LOOP_FAIL_COUNT_FILE}"
  fi
else
  reset_counter "${GPS_FAIL_COUNT_FILE}"
  reset_counter "${LOOP_FAIL_COUNT_FILE}"
fi

if python3 -c 'import sys; raise SystemExit(0 if float(sys.argv[1]) < 5.0 else 1)' "${disk_free_gb}"; then
  stop_recording "disk_free_below_5gb" "${DISK_FREE_GB_PATH}=${disk_free_gb}"
fi
