# Orin Maintenance Runbook


## Current Validated State

Validated read-only on 2026-06-01; currency-reviewed 2026-06-23. Note: the boot-path
and microSD rows below predate the 2026-06-11 microSD failure — the rig now boots
**pure NVMe via systemd-boot** (see "Boot Architecture & Recovery (2026-06-11)"). The
live detector was swapped yolov8n → **YOLO11n TensorRT** on 2026-06-15
(`/data/projects/gimbal/models/yolo11n.engine`).

| Item | Current value |
|---|---|
| OS | Ubuntu 22.04.5 LTS (`jammy`) |
| Jetson/L4T | R36.4.7, kernel `5.15.148-tegra` |
| Root filesystem | `/dev/nvme0n1p1`, ext4, label `NVME_ROOT`, mounted at `/` |
| Data filesystem | `/dev/nvme0n1p2`, ext4, label `NVME_DATA`, mounted at `/data` |
| NVMe model | WDC WDS500G2B0C-00PXH0, 465.8 GB |
| microSD | original 64 GB card **DIED 2026-06-11** mid-migration; rig boots pure NVMe via systemd-boot. WAVEBOOT spare SD (Boot000A) is the tested fallback only — no persistent microSD dependency for normal operation |
| EFI boot partition | `mmcblk0p10`, vfat, mounted at `/boot/efi` |
| Python | 3.10.12 |
| OpenCV | 4.12.0 |
| NumPy | 1.26.4 |
| PyTorch | `2.5.0a0+872d972e41.nv24.08` |
| Ultralytics | 8.3.233 |
| TensorRT | 10.3.0.30 + CUDA 12.5 packages |
| FFmpeg | 4.4.2 |
| GStreamer | 1.20.3 |
| Agent CLI provider | Claude Code CLI (`claude -p`) on the operator's Claude subscription (default `claude_code`; alts deepseek/glm/kimi) — drives the on-demand agent subsystem |

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

Historical service state during 2026-06-01 recon:

- `gps-server.service`: was active on `:8765`; now archived legacy Watch/iPhone/Cloudflare GPS path
- `dashboard.service`: retired legacy `:8080` dashboard; should remain stopped/disabled
- `cloudflared.service`: was active during recon; now serves only `wavecam.freddieland.com` → `:8088`
- `wavecam.service`: active on `:8088`

Current WaveCam runtime expectation:

- `wavecam.service`: active on `:8088`
- `dashboard.service`: stopped/disabled legacy `:8080` dashboard
- `gps-server.service`: stopped/disabled legacy `:8765` Watch/iPhone/Cloudflare GPS relay
- `cloudflared.service`: optional; only needed for remote access via `wavecam.freddieland.com`
- `wavecam` reads GPS from the base Wio over USB serial (`DirectRadioGps`) using the custom `firmware/direct-lora/` firmware (2× SeeedStudio Wio Tracker L1 Lite — base on the Orin USB, remote on the subject; beacon ~2 Hz). This direct-LoRa path **superseded Meshtastic, dropped 2026-06-14**; any Meshtastic interval/preset/nodeDB guidance is obsolete.

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

## iPhone USB Tether Uplink

Read-only recon on 2026-06-01. No networking, module loading, service starts, or route changes were made.

Goal: use a USB-connected iPhone as a field internet uplink for the Orin, while keeping local camera control on
`enP8p1s0` (`192.168.100.10/24`) and the camera at `192.168.100.88`.

Validated bench result on 2026-06-01:

| Check | Result | Meaning |
|---|---|---|
| Working physical path | iPhone connected to a USB-A host port | The Orin USB-C port is device/gadget mode, not the right host path for iPhone tethering. |
| Wrong physical path | iPhone on Orin USB-C made `l4tbr0`/`usb0`/`usb1` change carrier only | That is the Jetson Linux-for-Tegra gadget bridge, not iPhone tethering. |
| Apple USB device | `lsusb` showed Apple after moving to USB-A host | The phone was actually enumerated by the Orin. |
| Tether interface | `enx664842e70c66` | Host-side iPhone Ethernet interface created by `ipheth`/NetworkManager. |
| DHCP address | `172.20.10.8/28` | iPhone Personal Hotspot default DHCP range. |
| Tether gateway | `172.20.10.1`, default route metric `101` | Tether became the preferred internet route over Wi-Fi metric `600`. |
| Internet validation | `ip route get 8.8.8.8` used tether; `ping 8.8.8.8` succeeded `2/2`; DNS OK | Phone uplink works for internet. |
| Camera LAN | `enP8p1s0` remained `192.168.100.10/24`; camera route stayed local | Camera network stayed isolated from the phone uplink. |

Rules from validation:

- Use a USB-A host/data port or equivalent host adapter. Do not use the Orin USB-C device/gadget port for iPhone tethering.
- Do not run DHCP on `usb0`, `usb1`, or `l4tbr0` for the iPhone. Those are Jetson gadget interfaces.
- Leave the iPhone hotspot network at its default `172.20.10.0/28`.
- Do not set the phone/hotspot side to `192.168.100.x`; that collides with the camera LAN.
- NetworkManager auto-DHCP worked on the host-side `enx...` interface; no persistent manual connection was needed for the bench test.

Validated current state:

| Check | Result | Meaning |
|---|---|---|
| Kernel driver | `modinfo ipheth` succeeds; driver is present at `/lib/modules/5.15.148-tegra/kernel/drivers/net/usb/ipheth.ko` | The Jetson kernel has the Apple iPhone USB Ethernet driver available. |
| Driver loaded | `lsmod` does not show `ipheth` | Expected when no iPhone tether interface is currently enumerated. |
| Apple USB device | `lsusb` shows no Apple `05ac` device | No iPhone was attached/trusted/enumerated during the check. |
| Apple tools | `usbmuxd` package is installed; `/usr/sbin/usbmuxd` exists | Basic Apple USB multiplexor daemon is present. |
| Pairing tools | `idevice_id` and `idevicepair` are not installed | `libimobiledevice-utils` is missing; useful for trust/pair diagnostics. |
| `usbmuxd.service` | Static unit exists but is inactive | Expected with no active Apple USB session; not proven under attach. |
| `usbmuxd.socket` | Unit not found | This image does not expose socket activation for `usbmuxd`. |
| Current interfaces | `wlP1p1s0` Wi-Fi uplink, `enP8p1s0` camera LAN, `usb0`/`usb1` down/unmanaged Jetson gadget ports | No iPhone Ethernet interface is currently present. |
| Current default route | `default via 192.168.1.1 dev wlP1p1s0 metric 600` | Internet currently comes from Wi-Fi, not the phone. |

Current conclusion:

- iPhone USB tethering is **validated** on this Orin when the iPhone is connected to a USB-A host port.
- The missing piece was not the kernel driver. `ipheth` is present and the working interface appeared after real host-port enumeration.
- The earlier failure where no new Ethernet interface appeared was the Orin USB-C device/gadget path, not a host tether path.
- The existing `usb0` and `usb1` interfaces are Jetson USB gadget ports, not proof of iPhone tethering.

Bench validation steps, no persistent changes:

1. On the iPhone, enable **Personal Hotspot** and keep the screen unlocked.
2. Connect the iPhone to a USB-A host/data port on the Orin, using a known-good data cable or host adapter. Do not use the Orin USB-C device/gadget port.
3. Accept the **Trust This Computer** prompt on the iPhone if it appears.
4. On the Orin, run read-only checks:
   ```bash
   lsusb | grep -Ei 'apple|05ac' || true
   dmesg --color=never | grep -Ei 'apple|iphone|ipheth|usbmux|cdc' | tail -80
   lsmod | grep '^ipheth' || true
   ip -brief link
   ip -brief addr
   nmcli -f DEVICE,TYPE,STATE,CONNECTION device status
   ```
5. Expected success shape:
   - `lsusb` shows an Apple device.
   - `dmesg` shows the Apple device attaching and `ipheth` binding.
   - `ip -brief link` shows a new Ethernet-style interface beyond `usb0`/`usb1`, for example `enx664842e70c66`.
   - NetworkManager can acquire a DHCP address on that new interface.
   - `ip -brief addr` shows a `172.20.10.x/28` address on that interface.
6. Verify that camera LAN routing still stays local:
   ```bash
   ip route get 192.168.100.88
   ping -c 2 192.168.100.88
   ```
   Expected: route uses `enP8p1s0`, not the iPhone interface.
7. Verify internet route:
   ```bash
   ip route
   curl -I --max-time 5 https://cloudflare.com
   ```

If the iPhone appears in `lsusb` but no network interface appears:

1. Confirm `ipheth` is available:
   ```bash
   modinfo ipheth
   ```
2. Install diagnostics package in a maintenance window if needed:
   ```bash
   sudo apt install libimobiledevice-utils
   ```
3. Reconnect the iPhone and inspect trust/pairing:
   ```bash
   idevice_id -l
   idevicepair validate
   ```
4. Start `usbmuxd` only for a bench test if it does not start automatically:
   ```bash
   sudo systemctl start usbmuxd
   ```

If the iPhone interface appears but stays unmanaged:

1. Create a temporary DHCP connection for that interface with a higher route metric than the camera LAN.
2. Do not add a gateway to `enP8p1s0`; the camera LAN remains no-gateway.
3. Keep the iPhone route as internet-only; never route `192.168.100.0/24` through the phone.
4. Only make NetworkManager changes persistent after a bench test proves:
   - iPhone internet works.
   - Camera LAN still reaches `192.168.100.88`.
   - WaveCam still works after reconnect and reboot.
   - Any deliberately enabled remote-access service still works after reconnect and reboot.

Feasibility:

| Option | Feasibility | Status | Notes |
|---|---:|---|---|
| USB-A host-port iPhone tether as Orin field uplink | 8/10 | Validated | Internet worked through `172.20.10.1`; camera LAN stayed on `enP8p1s0`. |
| Orin USB-C device-port iPhone tether | 2/10 | Wrong path | This only toggles the Jetson gadget bridge (`l4tbr0`/`usb0`/`usb1`); no Apple host enumeration. |
| Wi-Fi hotspot from iPhone to Orin | 8/10 | Common fallback | Simpler routing; consumes Wi-Fi radio and may be less physically reliable than wired power/data. |
| Dedicated LTE router/modem for Orin uplink | 8/10 | Recommended fallback | More field-grade than iPhone tethering; simpler to make persistent. |
| Use iPhone USB only for charging/control, not uplink | 9/10 | Low risk | Keeps network topology simple if direct-LoRa or Wi-Fi provides the data path. |

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
| Claude CLI agent-subsystem integration | 8/10 | Installed | On-demand advisor shells out to `claude -p` on the operator's Claude subscription (`POST /api/v1/agent/{chat,summon}`). Use a login shell on the Orin if `claude` is not found. |

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
   systemctl list-unit-files 'wavecam*' 'cloudflared*' > /data/backups/systemd-units-$(date +%Y%m%d-%H%M%S).txt
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
   systemctl is-active wavecam
   systemctl is-active dashboard 2>/dev/null || true
   ip -brief addr show enP8p1s0
   ip route get 192.168.100.88
   curl -s http://localhost:8088/api/v1/status
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
10. Keep the old SD untouched until the restored system passes camera, GPS, WaveCam web/API, and recording tests.

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
19. Add a WaveCam action later: copy completed recording segments from `/data/recordings` to the
   removable microSD, then unmount/eject.

Do not remove the microSD yet. Current state still mounts `/boot/efi` from it, and `BootCurrent`
shows the SD device.

**STATUS (2026-06-11): The microSD died mid-migration.** The rig now boots pure NVMe via
systemd-boot. See the section below.

## Boot Architecture & Recovery (2026-06-11)

The 64 GB microSD failed 2026-06-11 during the in-place ESP migration (FAT writes appeared to
succeed but were garbage; the card eventually stopped mounting read-only on any host). The rig was
recovered to a working state that no longer needs the SD card for normal operation.

### Current Boot Chain

```
UEFI Boot000B "WaveCam NVMe boot" (first in order)
  └─ systemd-boot  on  nvme0n1p3  (FAT16 ESP, ~256 MiB, mounted at /boot/efi)
       └─ /boot/efi/boot/Image   (kernel COPY on the ESP)
       └─ /boot/efi/boot/initrd  (initrd COPY on the ESP)
            └─ root  nvme0n1p1  (ext4, label NVME_ROOT, /)
               └─ /boot/Image   (kernel on rootfs — source of truth for apt)
               └─ /boot/initrd  (initrd on rootfs)
```

UEFI Boot000A "WaveCam card boot" is the WAVEBOOT spare SD card (tested fallback, second in
boot order). The L4T launcher (`BOOTAA64.l4t`) is preserved on the ESP for reference but is not
in the active boot path — its A/B machinery died with the SD card.

Verified current state:

| Item | Value |
|---|---|
| Boot entry | Boot000B "WaveCam NVMe boot" (first) |
| ESP device | `/dev/nvme0n1p3`, FAT16, mounted at `/boot/efi` |
| Kernel on ESP | `/boot/efi/boot/Image` |
| Initrd on ESP | `/boot/efi/boot/initrd` |
| Root | `/dev/nvme0n1p1`, ext4, label `NVME_ROOT` |
| Fallback | Boot000A "WaveCam card boot" (WAVEBOOT spare SD, insert and power on) |

### KERNEL UPDATE RULE

`apt` updates the kernel and initrd on the rootfs (`/boot/Image`, `/boot/initrd`) but does NOT
touch the ESP copies. After any kernel or initrd change the ESP copies must be updated or the rig
will silently boot the old kernel on next restart.

**After every `apt upgrade` or `apt install nvidia-l4t-kernel*`:**

```bash
sudo bash /data/projects/gimbal/wavecam/tools/sync-esp.sh
```

The script compares sha256 hashes, copies rootfs→ESP if they differ, re-verifies, and exits 2
if it had to fix anything (exit 0 = already in sync, exit 1 = verify failure). Check it before
restarting:

```bash
sudo bash /data/projects/gimbal/wavecam/tools/sync-esp.sh --check
# exit 0 = in sync; exit 2 = stale (run without --check to fix)
```

`deploy.sh` runs `--check` automatically and prints a warning when the ESP is stale, but does
not block the deploy. The kernel gap is an operator action, not a deploy blocker.

### WAVEBOOT Fallback Card Procedure

The WAVEBOOT spare SD card is pre-loaded and tested. Use it if the NVMe ESP becomes unbootable.

1. Insert the WAVEBOOT card into the Orin SD slot.
2. Power on (or reboot). UEFI tries Boot000B first; if that fails, it falls through to
   Boot000A which reads the WAVEBOOT card.
3. If the UEFI order was changed and the card entry is not second, enter UEFI setup at
   power-on (hold or spam **Esc**) and move "WaveCam card boot" above "WaveCam NVMe boot"
   temporarily.
4. Once booted from the card, the NVMe root and data are still available — the card only
   provides the bootloader. Repair the NVMe ESP from the running system.
5. Do not write to the WAVEBOOT card while using it as a boot device. Keep it as a clean spare.

### UEFI Shell Recovery Basics

Enter the UEFI Shell from the UEFI setup menu (Esc at power-on → Boot Manager → EFI Shell), or
from the UEFI boot-order menu.

**Volume map:**

```
Shell> map -r -b
```

Identifies volumes. Lines containing `HD(1,` are NVMe; `HD(0,` is the SD slot. To confirm:

```
Shell> vol FS0:
```

Look for the label (WAVEBOOT on the spare card; the NVMe ESP has no label by default).

**Binary verify (never use `cp` for binary verification — it silently corrupts):**

```
Shell> comp FS0:\EFI\BOOT\Image FSn:\EFI\BOOT\Image
```

Then sha256 separately from Linux once the system is booted.

**Leading-slash echo quirk:** the UEFI Shell `echo` command treats a leading `\` in an argument
as a path escape. Quote or escape if you need to print a path that begins with `\`.

**Booting a specific entry from the shell:**

```
Shell> bcfg boot dump
Shell> bootorder
```

Then select by entry number or use `exit` to return to the UEFI menu and pick an entry
manually.

### L4T Traps (Hard-Won)

| Trap | Detail |
|---|---|
| Kernel ignores `initrd=` on cmdline | This L4T kernel (5.15.148-tegra) uses LoadFile2 for the initrd; the cmdline `initrd=` loader is compiled out. Direct kernel boots from the UEFI shell stall at "Waiting for root device /dev/nvme0n1p1" because NVMe is modular (in initrd). |
| EFI shell `cp` silently corrupts | FAT writes to the failed SD "succeeded" but were garbage. Always `comp` + sha256 after copying. |
| OS chain A flips Unbootable | After failed boot attempts L4T's A/B records "OS chain A status: Unbootable" in NVRAM. The rig will then skip Boot000B. Reset: UEFI setup → Device Manager → NVIDIA Configuration → L4T Configuration → reset chain A status to Bootable. |
| Grace-form save quirk | In L4T Configuration, pressing **F10** alone sometimes fails to save. Press **D** first (to mark dirty), then **F10**. |
| Odd-sector / forced-FAT32 ESP is firmware-hostile | The failed in-place mkfs.vfat -F32 partition caused "unable to locate partition" in UEFI. Use FAT16 for small ESPs (<256 MiB) on this firmware, or dd-clone a known-good ESP. |

## Reference Links

- NVIDIA JetPack 6.2.2 page: https://developer.nvidia.com/embedded/jetpack-sdk-622
- NVIDIA Jetson Linux flashing support, NVMe boot section: https://docs.nvidia.com/jetson/archives/r36.3/DeveloperGuide/SD/FlashingSupport.html#flashing-to-an-nvme-drive
- NVIDIA Jetson Linux UEFI boot order notes: https://docs.nvidia.com/jetson/archives/r36.4/DeveloperGuide/SD/Bootloader/UEFI.html#overriding-the-default-boot-order-during-flashing

## Agent Subsystem Notes

The agent subsystem is an **on-demand** feature of `wavecam.service` (not a separate daemon). It
shells out to the **Claude Code CLI** (`claude -p`) running on the operator's Claude subscription
(default provider `claude_code`; alternates deepseek/glm/kimi), reached via `POST /api/v1/agent/{chat,summon}`.

It runs in two modes:

- **Advisor (default):** inspect/advise only. It can read status and answer questions but cannot move
  the camera.
- **Acting-agent (operator-ARM-gated, supervise-only):** an operator-only ARM toggle (default OFF,
  TTL 600 s, KILL-disarmed) grants it a shell to act via the control API. It only moves the camera
  when ARMed; it never moves the camera unattended. **KILL is human-only + supreme** — it is never an
  agent capability and it disarms the agent + stops motion.

Use a login shell on the Orin if `claude` is not found:

```bash
bash -lc 'claude --version'
```
