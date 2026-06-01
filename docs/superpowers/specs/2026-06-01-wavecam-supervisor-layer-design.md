# WaveCam Supervisor Layer Design

Date: 2026-06-01

Status: draft/recon only. No live cutover, no service install, no Orin mutation.

## Decision

WaveCam needs a deterministic supervisor layer, not an always-on LLM pilot.

- `wavecam.service` should own the vision/servo process under systemd.
- A small deterministic supervisor process should monitor health and apply safe operator actions through the Control API and systemd.
- Codex on the Orin should be an on-demand diagnostic/operator assistant, or at most a disabled-by-default maintenance service. It should not run continuously in the real-time control path.

Feasibility:

| Option | Feasibility | Status | Recommendation |
|---|---:|---|---|
| `wavecam.service` for the servo runner | 9/10 | Validated process shape, not installed | Do first. |
| Deterministic supervisor process | 8/10 | Design only | Build after `wavecam.service`. |
| Always-on Codex systemd service | 3/10 | Not recommended | Avoid for motor authority. |
| On-demand Codex diagnostics on Orin | 8/10 | Codex CLI installed | Use for maintenance and analysis. |

## Current Runtime Facts

Validated read-only:

- The live servo runner is `python3 run.py config.orin.servo.yaml`.
- It runs from `/data/projects/wavecam-testbed`.
- It listens on `:8088`.
- There is no `wavecam.service` unit yet.
- `gps-server.service`, `dashboard.service`, and `cloudflared.service` are active systemd services.
- `orin/wavecam` is now the canonical repo package, but the current live runner is still the scratch/testbed checkout.

Implication: the first cutover is not a rewrite. It is wrapping the existing known-good runner in systemd, then later moving the runtime working directory from `/data/projects/wavecam-testbed` to the deployed repo path after a separate verification gate.

## Non-Negotiable Safety Invariants

1. The real-time capture/fusion/servo loop remains inside the WaveCam process.
2. Only `PtzOwner` decides whether a camera-moving command is allowed.
3. KILL remains sticky and wins over every owner.
4. The supervisor must never write VISCA directly.
5. Codex must never drive frame-by-frame motor control.
6. Restarting or killing Codex must not affect active tracking.
7. Restarting `wavecam.service` must send a camera stop on shutdown.
8. Failed startup must leave the camera stationary.

## Components

### `wavecam.service`

Purpose: keep the WaveCam runner alive and give the operator a standard way to start, stop, inspect, and restart it.

Initial target:

```ini
[Unit]
Description=WaveCam vision servo runner
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=zack
Group=zack
WorkingDirectory=/data/projects/wavecam-testbed
ExecStart=/usr/bin/python3 /data/projects/wavecam-testbed/run.py /data/projects/wavecam-testbed/config.orin.servo.yaml
Restart=on-failure
RestartSec=2
TimeoutStopSec=8
KillSignal=SIGINT
ExecStopPost=/usr/bin/python3 /data/projects/wavecam-testbed/tools/ptz_stop_once.py 192.168.100.88 1259

[Install]
WantedBy=multi-user.target
```

Notes:

- `WorkingDirectory` starts at `/data/projects/wavecam-testbed` because that is the live validated runner.
- A later cutover can change it to `/data/projects/gimbal/orin/wavecam` or another canonical deploy path.
- `KillSignal=SIGINT` is preferred over raw `SIGTERM` because `run.py` has cleanup in `finally`; this must be verified on the Orin before enabling.
- `ExecStopPost` should call a tiny deterministic stop helper. It must not import the full web app or start detection; it only sends pan/tilt stop and zoom stop.
- Use `Restart=on-failure`, not `Restart=always`, until KILL/restart behavior is explicitly tested. If the operator deliberately stops the service, it should stay stopped.

Validation before install:

1. Run the exact `ExecStart` manually.
2. Confirm `:8088/status` returns sane JSON.
3. Send `SIGINT` to a test process and verify the camera stops.
4. Run the stop helper independently and verify no camera movement except stop.
5. Only then install and enable the unit.

### `wavecam-supervisor.service`

Purpose: deterministic health watcher and safe command broker. It is not the vision loop and not an LLM.

Responsibilities:

- Poll `/status` on the WaveCam Control API.
- Poll systemd state for `wavecam.service`, `gps-server.service`, `dashboard.service`, `cloudflared.service`.
- Publish a compact health summary for the dashboard and phone app.
- Apply safe high-level actions:
  - start/restart `wavecam.service`
  - stop `wavecam.service`
  - KILL/RESUME through the Control API
  - apply validated config changes by writing config then restarting only when required
  - collect logs into a diagnostic bundle
- Refuse unsafe actions:
  - direct VISCA sends
  - camera movement while KILL is latched
  - config changes with unknown keys
  - restart loops beyond a configured threshold

Recommended shape:

- Python process with a small typed config.
- No direct motor backend.
- Uses HTTP Control API for WaveCam state/actions.
- Uses `systemctl` only for service lifecycle.
- Writes one JSON health file, for example `/run/wavecam/supervisor.json`.
- Exposes one local-only API if needed, for example `127.0.0.1:8090`, or lets the main dashboard read the JSON file.

Restart policy:

- `Restart=always` is acceptable for the supervisor because it does not move the camera.
- If the supervisor dies, tracking continues.
- If WaveCam dies, supervisor can restart it only when the operator has enabled auto-restart.

### Codex on Orin

Codex should not run as a permanent always-on systemd service by default.

Recommended operating modes:

1. On-demand SSH/operator session.
   - Best default.
   - Runs diagnostics, explains state, edits docs/config with normal review.
   - Feasibility 8/10.

2. Disabled-by-default maintenance unit.
   - A systemd unit exists but is not enabled.
   - Operator starts it when remote agent work is needed.
   - Feasibility 6/10.

3. Scheduled diagnostic job.
   - Runs a fixed script, not open-ended Codex.
   - Emits health summaries or bundles logs.
   - Feasibility 8/10.

Rejected default:

- Always-on Codex daemon with authority to operate the camera.
- Feasibility 3/10.
- Reasons: token burn, credential exposure, nondeterministic latency, unclear privilege boundary, and no reason for an LLM to exist in a safety-critical control path.

## Control Boundaries

Allowed command flow:

```text
Phone / dashboard / Codex
  -> Control API
  -> WaveCam core
  -> PtzOwner
  -> VISCA backend
```

Allowed supervisor lifecycle flow:

```text
Supervisor
  -> systemd start/stop/restart wavecam.service
  -> Control API KILL/RESUME/status
  -> diagnostic log collection
```

Forbidden flow:

```text
Codex or supervisor
  -> direct VISCA movement
```

## Cutover Plan

This is the proposed sequence for when Zack is awake.

1. Confirm current live runner:
   ```bash
   ps -p "$(pgrep -f 'python3 run.py config.orin.servo.yaml')" -o pid,ppid,etime,cmd
   curl -s http://localhost:8088/status
   ```
2. Add a stop-only helper in the runtime tree.
3. Test the stop helper with the camera already stationary.
4. Draft `wavecam.service` but do not enable it.
5. Stop the current bare process with KILL active.
6. Start `wavecam.service` manually:
   ```bash
   sudo systemctl start wavecam.service
   ```
7. Verify:
   ```bash
   systemctl status wavecam.service
   curl -s http://localhost:8088/status
   journalctl -u wavecam.service -n 80 --no-pager
   ```
8. Test service stop:
   ```bash
   sudo systemctl stop wavecam.service
   ```
   Expected: camera stop command sent, service stays stopped.
9. Start again and test one controlled KILL/RESUME cycle.
10. Enable on boot only after restart/stop behavior is proven:
    ```bash
    sudo systemctl enable wavecam.service
    ```

Rollback:

- Disable/remove the unit.
- Start the previous bare process from `/data/projects/wavecam-testbed`.
- Current SD/NVMe boot state is unrelated and should not be touched during this cutover.

## Verification Gates

Minimum local checks before committing service files:

- `python3 -m tests.test_offline`
- `python3 -m tests.test_ptz_owner`
- `python3 -m tests.test_pipeline_kill`
- `python3 ~/.codex/skills/anti-vibe-engineering/scripts/python_quality_scan.py orin/wavecam`

Minimum live checks before enabling service:

- `/status` reachable on `:8088`.
- KILL latches and blocks motion.
- STOP sends pan/tilt stop and zoom stop.
- `systemctl stop wavecam.service` leaves camera stationary.
- `systemctl restart wavecam.service` does not produce an unexpected pan/tilt jump.

## Open Questions

1. Should the first `wavecam.service` wrap `/data/projects/wavecam-testbed` exactly, or should we first rsync the canonical repo `orin/wavecam` to `/data/projects/gimbal/orin/wavecam` and service that path?
2. Should auto-restart of `wavecam.service` be enabled immediately, or should restart require operator confirmation until the stop path is proven?
3. Should the supervisor expose its own local API, or should it only write `/run/wavecam/supervisor.json` for the main dashboard to read?

## Recommendation

Use a two-stage rollout.

Stage 1: wrap the currently running WaveCam process in `wavecam.service`, with no behavior change and no repo-path cutover.

Stage 2: add the deterministic supervisor process. Keep Codex on Orin as an on-demand diagnostic and config assistant, not an always-on service.

This is the smallest path that improves reliability without moving motor authority into a new unproven layer.
