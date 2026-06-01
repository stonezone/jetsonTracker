# Orin Maintenance Runbook

## Current Validated State

Validated read-only on 2026-06-01.

| Item | Current value |
|---|---|
| OS | Ubuntu 22.04.5 LTS (`jammy`) |
| Jetson/L4T | R36.4.7, kernel `5.15.148-tegra` |
| Root filesystem | `/dev/nvme0n1p1`, ext4, label `NVME_ROOT`, mounted at `/` |
| Data filesystem | `/dev/nvme0n1p2`, ext4, label `NVME_DATA`, mounted at `/data` |
| NVMe model | WDC WDS500G2B0C-00PXH0, 465.8 GB |
| microSD | `mmcblk0`, 58.9 GB, still present |
| EFI boot partition | `mmcblk0p10`, vfat, mounted at `/boot/efi` |
| Python | 3.10.12 |
| OpenCV | 4.12.0 |
| NumPy | 1.26.4 |
| PyTorch | `2.5.0a0+872d972e41.nv24.08` |
| Ultralytics | 8.3.233 |
| TensorRT | 10.3.0.30 + CUDA 12.5 packages |
| FFmpeg | 4.4.2 |
| GStreamer | 1.20.3 |
| Codex CLI | 0.135.0 at `/home/zack/.local/bin/codex` |

## What This Means

- The Orin is **not currently booting its root filesystem from the microSD**. Root is already on NVMe.
- The microSD is **not removable yet**, because `/boot/efi` is still on `mmcblk0p10`.
- `/data` is already the right place for recordings and large project data.
- The remaining storage goal is narrower than originally assumed: move or duplicate the boot/EFI path
  so the microSD can become a removable export card.

## Feasibility Scores

| Task | Feasibility | Status | Notes |
|---|---:|---|---|
| Normal Ubuntu package updates | 7/10 | Unvalidated until run | Common, but Jetson camera/GPU stacks can regress. Snapshot first. |
| Full JetPack/L4T major upgrade | 3/10 | Not recommended unattended | High risk of boot, CUDA, TensorRT, camera, or kernel-module breakage. |
| Keep root/data on NVMe | 10/10 | Validated | Already true. |
| Make microSD removable | 5/10 | Needs hands-on recovery plan | Boot/EFI still depends on microSD. Must preserve a bootable fallback. |
| Use microSD as video export card | 8/10 after boot is independent | Blocked by EFI dependency | Once boot no longer needs microSD, mount it only for copied recordings. |
| Codex CLI on Orin | 8/10 | Installed | Official installer failed; direct official release asset worked with SHA-256 verification. |

## Safe Update Plan

Do this when the system is not actively tracking or recording.

1. Record current state:
   ```bash
   date
   uname -a
   cat /etc/nv_tegra_release
   lsblk -o NAME,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS,MODEL
   systemctl --no-pager --failed
   ```
2. Stop nonessential project services:
   ```bash
   sudo systemctl stop tracker recorder streamer 2>/dev/null || true
   ```
3. Back up the project and service state to `/data`:
   ```bash
   mkdir -p /data/backups
   tar -C /data/projects -czf /data/backups/gimbal-$(date +%Y%m%d-%H%M%S).tgz gimbal
   systemctl list-unit-files 'gps-server*' 'dashboard*' 'cloudflared*' > /data/backups/systemd-units-$(date +%Y%m%d-%H%M%S).txt
   ```
4. Refresh package metadata and review before upgrading:
   ```bash
   sudo apt update
   apt list --upgradable
   ```
5. Prefer a conservative upgrade first:
   ```bash
   sudo apt upgrade
   ```
6. Do **not** run these casually:
   ```bash
   sudo do-release-upgrade
   sudo apt full-upgrade
   ```
   Use them only with a recovery image, physical access, and time to repair Jetson boot/CUDA issues.
7. Reboot:
   ```bash
   sudo reboot
   ```
8. Verify after reboot:
   ```bash
   systemctl is-active gps-server cloudflared dashboard
   ip -brief addr show enP8p1s0
   ip route get 192.168.100.88
   curl -s http://localhost:8080/api/session
   cd /data/projects/gimbal
   python3 scripts/test_color_detector.py
   python3 scripts/test_vision_follow_logic.py
   python3 scripts/test_vision_assist.py
   ```

## Removable microSD Plan

Current target: keep OS root and recordings on NVMe, then make the microSD removable for footage export.

1. Confirm boot chain with physical access and a known-good recovery path.
2. Identify whether the firmware can boot the EFI partition from NVMe on this board/JetPack build.
3. Clone or recreate the EFI partition onto NVMe, not the removable microSD.
4. Update boot entries only after the NVMe EFI copy is verified.
5. Reboot with the microSD still inserted.
6. Confirm `/boot/efi` is mounted from NVMe, not `mmcblk0p10`.
7. Power down, remove microSD, boot again.
8. If the Orin boots without microSD, repartition/format the microSD as an export card.
9. Add a dashboard action later: copy completed recording segments from `/data/recordings` to the
   removable microSD, then unmount/eject.

Do not remove the microSD yet. Current state still mounts `/boot/efi` from it.

## Codex CLI Notes

Official install command failed on the Orin because the 0.135.0 installer looked for a package checksum
entry that was absent from the published package checksum manifest. The direct official release asset
worked:

- Asset: `codex-aarch64-unknown-linux-musl.tar.gz`
- Version: 0.135.0
- Verified SHA-256: `568bce1d593ef25ffdf5549369a8606085652294646a5c4961547a894ea2f76d`
- Installed binary: `/home/zack/.local/bin/codex`
- Verified: `codex-cli 0.135.0`

Use a login shell on the Orin if `codex` is not found:

```bash
bash -lc 'codex --version'
```
