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

## Boot Dependency Recon

Read-only recon on 2026-06-01 shows the OS root is already on NVMe, but the boot path is not SD-free.

| Check | Result | Meaning |
|---|---|---|
| `/proc/cmdline` root | `root=PARTUUID=6580ff99-600c-443b-b7f3-24bf0e695bf6` | Kernel mounted the NVMe root partition. |
| `/dev/nvme0n1p1` | 60 GiB ext4, label `NVME_ROOT`, mounted `/` | Active OS root. |
| `/dev/nvme0n1p2` | 405.8 GiB ext4, label `NVME_DATA`, mounted `/data` | Recording/project data partition. |
| NVMe free space | about 1 MiB total | No room to add an EFI partition without shrinking/repartitioning. |
| `/dev/mmcblk0p10` | 64 MiB vfat, mounted `/boot/efi` | Current EFI system partition is still on the microSD. |
| UEFI BootCurrent | `0001 UEFI SD Device` | Current boot path came from the SD device. |
| UEFI BootOrder | `0008` NVMe first, then `0001` SD | Firmware tries NVMe first, but currently falls back to SD because NVMe has no EFI partition. |
| SD `mmcblk0p2`-`p15` | A/B kernel, DTB, recovery, ESP, UDA, reserved partitions | This is still a Jetson boot/recovery layout, not just a storage card. |

Current service state during recon:

- `gps-server.service`: active on `:8765`
- `dashboard.service`: active on `:8080`
- `cloudflared.service`: active
- WaveCam servo runner: active as `python3 run.py config.orin.servo.yaml` from `/data/projects/wavecam-testbed`, listening on `:8088`
- No `wavecam.service` unit exists yet; the servo runner is a process, not a managed systemd service.

Current package state:

- `nvidia-l4t-core`: `36.4.7-20250918154033`
- `nvidia-l4t-kernel`: `5.15.148-tegra-36.4.7-20250918154033`
- `nvidia-jetpack` meta-package: not installed; current `r36.4` apt source offers `6.2.1+b38`
- NVIDIA says JetPack 6.2.2 is the current JetPack 6 production release and uses Jetson Linux 36.5. This Orin is still on the `r36.4` repo lane.

## What This Means

- The Orin is **not currently booting its root filesystem from the microSD**. Root is already on NVMe.
- The microSD is **not removable yet**, because `/boot/efi` is still on `mmcblk0p10`.
- `/data` is already the right place for recordings and large project data.
- The remaining storage goal is narrower than originally assumed: make the boot path SD-independent
  so the microSD can become a removable export card.
- Do not treat the current SD as disposable. It still provides the known-good boot fallback.

## Feasibility Scores

| Task | Feasibility | Status | Notes |
|---|---:|---|---|
| Normal Ubuntu package updates | 7/10 | Unvalidated until run | Common, but Jetson camera/GPU stacks can regress. Snapshot first. |
| Full JetPack/L4T major upgrade | 3/10 | Not recommended unattended | High risk of boot, CUDA, TensorRT, camera, or kernel-module breakage. |
| Keep root/data on NVMe | 10/10 | Validated | Already true. |
| Make microSD removable | 5/10 | Needs hands-on recovery plan | Boot/EFI still depends on microSD. Must preserve a bootable fallback. |
| In-place NVMe ESP conversion | 4/10 | Bench-only | Requires shrinking `/data` or repartitioning NVMe. Reversible if SD is preserved, but boot behavior must be proven. |
| Fresh SDK Manager/initrd NVMe flash | 7/10 | Bench-only | Cleaner Jetson-supported route, but requires backup/restore and recovery-mode host workflow. |
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
   Current upgradable set includes Docker, OpenCV development packages, WebKit, systemd, OpenSSH,
   Python 3.10, and many Ubuntu security packages. Do not assume this is risk-free just because it
   is an `apt upgrade`; camera/GPU/video stacks must be re-tested after reboot.
6. Do **not** run these casually:
   ```bash
   sudo do-release-upgrade
   sudo apt full-upgrade
   ```
   Use them only with a recovery image, physical access, and time to repair Jetson boot/CUDA issues.
7. Do not switch the NVIDIA repo lane from `r36.4` to `r36.5` casually. That is a JetPack/L4T upgrade,
   not normal maintenance.
8. If moving to JetPack 6.2.2 / Jetson Linux 36.5, use a bench maintenance window with full backup,
   physical display or serial access, recovery host, and time to rebuild TensorRT engines.
9. Reboot:
   ```bash
   sudo reboot
   ```
10. Verify after reboot:
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

### Recommended Path: Clean External NVMe Boot Flash

This is the safer Jetson-supported path if a full backup/restore window is acceptable.

1. Back up `/data/projects`, `/data/recordings`, `/etc/systemd/system`, `/etc/NetworkManager`, and
   `/home/zack`.
2. Export current recon:
   ```bash
   lsblk -o NAME,SIZE,FSTYPE,LABEL,PARTUUID,UUID,MOUNTPOINTS
   sudo efibootmgr -v
   sudo nvbootctrl dump-slots-info
   ```
3. Prepare a recovery host with NVIDIA SDK Manager or Linux_for_Tegra for the target JetPack lane.
4. Put the Orin in recovery mode.
5. Flash NVMe as the boot/root device using NVIDIA's initrd external storage workflow.
6. Boot with the microSD still inserted.
7. Confirm `/` and `/boot/efi` are both on NVMe.
8. Shut down, remove the microSD, and boot again.
9. If SD-free boot succeeds, restore project data and services.
10. Keep the old SD untouched until the restored system passes camera, GPS, dashboard, and recording tests.

### Alternative Path: In-Place NVMe EFI Conversion

This keeps the existing NVMe root and data partitions. It is more delicate because the NVMe has almost
no free space, so adding an EFI partition requires shrinking `/data`.

Bench-only checklist:

1. Confirm physical access, serial or HDMI access, and a recovery host.
2. Keep the current microSD unchanged as rollback media.
3. Stop all services that use `/data`.
4. Back up `/data` to external storage.
5. Unmount `/data`.
6. Run filesystem check on `/dev/nvme0n1p2`.
7. Shrink `/dev/nvme0n1p2` by at least 512 MiB.
8. Resize the GPT partition boundary for `/dev/nvme0n1p2`.
9. Create a new FAT32 EFI partition at the end of the NVMe, for example `/dev/nvme0n1p3`.
10. Copy the current SD EFI contents from `/boot/efi` into the new NVMe EFI partition.
11. Create an explicit UEFI boot entry for the NVMe EFI loader, or verify that the existing `UEFI WDC`
    device entry finds `EFI/BOOT/BOOTAA64.efi`.
12. Update `/etc/fstab` to mount the NVMe EFI partition at `/boot/efi`.
13. Reboot with the microSD still inserted.
14. Confirm:
    ```bash
    findmnt /boot/efi
    sudo efibootmgr -v
    cat /proc/cmdline
    ```
15. Shut down and remove the microSD only after `/boot/efi` is confirmed on NVMe.
16. Boot without the microSD.
17. If boot fails, reinsert the untouched SD and boot from the SD entry in UEFI.
18. If SD-free boot succeeds, repartition/format the microSD as an export card.
19. Add a dashboard action later: copy completed recording segments from `/data/recordings` to the
   removable microSD, then unmount/eject.

Do not remove the microSD yet. Current state still mounts `/boot/efi` from it, and `BootCurrent`
shows the SD device.

## Reference Links

- NVIDIA JetPack 6.2.2 page: https://developer.nvidia.com/embedded/jetpack-sdk-622
- NVIDIA Jetson Linux flashing support, NVMe boot section: https://docs.nvidia.com/jetson/archives/r36.3/DeveloperGuide/SD/FlashingSupport.html#flashing-to-an-nvme-drive
- NVIDIA Jetson Linux UEFI boot order notes: https://docs.nvidia.com/jetson/archives/r36.4/DeveloperGuide/SD/Bootloader/UEFI.html#overriding-the-default-boot-order-during-flashing

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
