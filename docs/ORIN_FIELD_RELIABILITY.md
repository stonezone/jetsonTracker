# Orin Field Reliability Runbook

The #1 field risk for WaveCam is **the Orin not staying reachable on the network**, not the
software. This runbook covers diagnosing and recovering a "camera brain is offline" situation.

## Symptom: app shows OFFLINE / guide won't load / no live data

The iOS app reaches the Orin over two routes (`WaveCamClient.getWithFallback`):
- **USB tether** — `http://172.20.10.8:8088` (tried first)
- **Wi-Fi** — `http://192.168.1.155:8088` (fallback)

If both fail, the app is offline. Almost always this is the Orin's **network presence**, not the code.

## Step 1 — isolate: is it the Orin or your network?

Run from a machine on the same Wi-Fi:

```bash
ping -c2 192.168.1.1     # your gateway/router
ping -c2 192.168.1.155   # the Orin (Wi-Fi)
```

- **Gateway 0% loss + Orin 100% loss → it's the Orin** (down, rebooted, or drifted IP). Go to Step 2.
- **Both fail → it's your machine's Wi-Fi**, not the Orin.

> Observed 2026-06-05: one client saw intermittent `.155` reachability while another continuous ping
> later stayed clean. When reachability reports disagree, test from the app route, the same Wi-Fi
> client, and the Orin console before calling the host down.

## Step 2 — find where the Orin went (IP drift)

Root cause seen so far: **DHCP re-leased the Orin to a different IP on reboot** (it drifted to
`192.168.1.50`, so `.155` went dark and the app couldn't find it).

```bash
ping -c1 192.168.1.50           # the IP it drifted to before
ping -c1 orin                   # hostname (Codex set the Orin hostname to "orin")
arp -a | grep -i b8:27\|nvidia  # scan the LAN for the Orin's MAC if needed
```

**Permanent Wi-Fi fix (applied 2026-06-05):** the Orin's Wi-Fi profile is manually pinned to
`192.168.1.155/24`, and the router also has a **DHCP reservation** for `192.168.1.155` tied to
the Orin's Wi-Fi MAC. Re-confirm both after router or NetworkManager changes.

## Step 3 — tether route

The iPhone USB tether path (`172.20.10.8/28`, Orin USB-A host port) is the field-primary uplink.
Current truth: `172.20.10.8` is the expected iPhone Personal Hotspot DHCP lease, not a static
address hardcoded on the Orin. Verify it when the phone is physically tethered:

```bash
ssh orin 'ip -4 addr; nmcli -t -f NAME,DEVICE,IP4.ADDRESS connection show --active'
curl -s -o /dev/null -w "%{http_code}\n" --max-time 6 http://172.20.10.8:8088/api/v1/status   # expect 200
```

If tethered and `.155` is down, the app should still reach the Orin over the tether route. If the
phone assigns a different tether address, update the app route/defaults or fix the tether DHCP source;
do not assume the Orin is enforcing `172.20.10.8` locally.

## Step 4 — service vs host

If the host pings but `:8088` is dead → the `wavecam.service` is down (Codex/Zack lane: restart it
via `ssh orin` + `systemctl restart wavecam.service`. Claude has a standing grant to deploy via `deploy.sh` + restart the service; the KILL-reachable + supervise-only rails always hold).
If the host doesn't ping at all → it's a network/power/reboot issue, not the service.

## Step 5 — verify recovery

```bash
ping -c2 192.168.1.155
curl -s -o /dev/null -w "%{http_code}\n" --max-time 6 http://192.168.1.155:8088/api/v1/status   # expect 200
curl -s -o /dev/null -w "%{http_code}\n" --max-time 6 http://192.168.1.155:8088/guide            # expect 200
```

## Open: reboot/flap root cause (unconfirmed)

The Orin had ~3 hard reboots (Jun 3) and a drop (Jun 5). The static IP / reservation fixes
*reachability after* a reboot but **not why it reboots/drops**. Checklist to confirm before field use:

- [ ] Power: brown-outs / undervolt warnings (`dmesg | grep -i voltage`, check the PSU/cable).
- [ ] Thermal: throttling/shutdown under inference load (`tegrastats`).
- [ ] Wi-Fi driver: adapter dropping/re-associating (`journalctl -u wpa_supplicant`, `dmesg | grep -i wlan`).
- [ ] OOM / service crash-loop (`journalctl -u wavecam.service`, `dmesg | grep -i oom`).
- [ ] Auto-updates / unattended-upgrades triggering reboots (disable on the rig).

Until the root cause is confirmed, treat unattended Orin uptime as **not field-proven**.
