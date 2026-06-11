#!/usr/bin/env bash
# sync-esp.sh — keep the systemd-boot ESP copies in sync with the rootfs kernel/initrd.
#
# After any `apt upgrade` that touches nvidia-l4t-kernel the rootfs copies at
# /boot/Image and /boot/initrd are updated, but the ESP copies at
# /boot/efi/boot/Image and /boot/efi/boot/initrd are NOT.  The rig then silently
# boots the old kernel until those copies are refreshed.  This script detects and
# fixes that gap.
#
# Must be run as root ON the rig.
#
# Usage:
#   sudo bash sync-esp.sh            # detect + fix; exit 0=in-sync 2=fixed 1=error
#   sudo bash sync-esp.sh --check    # report-only; exit 0=in-sync 2=stale 1=error
#
# Exit codes:
#   0  — ESP is already in sync (or was already up to date)
#   2  — ESP was (or is) stale  (--check: stale detected; no --check: fixed successfully)
#   1  — fatal error (wrong user, ESP not mounted from nvme0n1p3, sha256 verify failed)
set -euo pipefail

# ── constants ─────────────────────────────────────────────────────────────────
ROOTFS_KERNEL=/boot/Image
ROOTFS_INITRD=/boot/initrd
ESP_MOUNT=/boot/efi
ESP_KERNEL=${ESP_MOUNT}/boot/Image
ESP_INITRD=${ESP_MOUNT}/boot/initrd
ESP_DEV=nvme0n1p3

# ── flags ─────────────────────────────────────────────────────────────────────
CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

# ── helpers ───────────────────────────────────────────────────────────────────
log()  { printf '[sync-esp] %s\n' "$*" >&2; }
fail() { log "ERROR: $*"; exit 1; }

sha256() {
    sha256sum "$1" | awk '{print $1}'
}

# ── pre-flight ────────────────────────────────────────────────────────────────
[[ $(id -u) -eq 0 ]] || fail "must be run as root (sudo bash $0)"

# Verify the ESP is actually mounted from nvme0n1p3 before touching anything.
# findmnt exits non-zero when the mount point is not found.
MOUNTED_FROM=$(findmnt -n -o SOURCE "${ESP_MOUNT}" 2>/dev/null || true)
if [[ "${MOUNTED_FROM}" != "/dev/${ESP_DEV}" ]]; then
    fail "${ESP_MOUNT} is not mounted from /dev/${ESP_DEV} (found: '${MOUNTED_FROM:-nothing}')" \
         "— refusing to touch ESP copies"
fi

[[ -f "${ROOTFS_KERNEL}" ]] || fail "rootfs kernel not found: ${ROOTFS_KERNEL}"
[[ -f "${ROOTFS_INITRD}" ]] || fail "rootfs initrd not found: ${ROOTFS_INITRD}"

# ── hash comparison ───────────────────────────────────────────────────────────
SRC_K=$(sha256 "${ROOTFS_KERNEL}")
SRC_I=$(sha256 "${ROOTFS_INITRD}")

stale_kernel=0
stale_initrd=0

if [[ -f "${ESP_KERNEL}" ]]; then
    DST_K=$(sha256 "${ESP_KERNEL}")
    [[ "${SRC_K}" == "${DST_K}" ]] || stale_kernel=1
else
    log "ESP kernel copy missing: ${ESP_KERNEL}"
    stale_kernel=1
fi

if [[ -f "${ESP_INITRD}" ]]; then
    DST_I=$(sha256 "${ESP_INITRD}")
    [[ "${SRC_I}" == "${DST_I}" ]] || stale_initrd=1
else
    log "ESP initrd copy missing: ${ESP_INITRD}"
    stale_initrd=1
fi

if [[ ${stale_kernel} -eq 0 && ${stale_initrd} -eq 0 ]]; then
    log "ESP in sync — no action needed"
    exit 0
fi

# ── stale: report or fix ──────────────────────────────────────────────────────
if [[ ${stale_kernel} -eq 1 ]]; then
    log "STALE kernel  rootfs=${SRC_K}  esp=${DST_K:-MISSING}"
fi
if [[ ${stale_initrd} -eq 1 ]]; then
    log "STALE initrd  rootfs=${SRC_I}  esp=${DST_I:-MISSING}"
fi

if [[ ${CHECK_ONLY} -eq 1 ]]; then
    log "--check mode: ESP is stale (run without --check to fix)"
    exit 2
fi

# ── copy + verify ─────────────────────────────────────────────────────────────
if [[ ${stale_kernel} -eq 1 ]]; then
    log "Copying kernel  ${ROOTFS_KERNEL} → ${ESP_KERNEL}"
    cp "${ROOTFS_KERNEL}" "${ESP_KERNEL}"
    NEW_K=$(sha256 "${ESP_KERNEL}")
    [[ "${NEW_K}" == "${SRC_K}" ]] || fail "kernel verify failed after copy (esp=${NEW_K} want=${SRC_K})"
    log "OK kernel  ${NEW_K}"
fi

if [[ ${stale_initrd} -eq 1 ]]; then
    log "Copying initrd  ${ROOTFS_INITRD} → ${ESP_INITRD}"
    cp "${ROOTFS_INITRD}" "${ESP_INITRD}"
    NEW_I=$(sha256 "${ESP_INITRD}")
    [[ "${NEW_I}" == "${SRC_I}" ]] || fail "initrd verify failed after copy (esp=${NEW_I} want=${SRC_I})"
    log "OK initrd  ${NEW_I}"
fi

sync
log "ESP updated and verified — reboot will use the new kernel"
exit 2
