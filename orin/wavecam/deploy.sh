#!/usr/bin/env bash
# Deploy orin/wavecam to the rig. The ONLY sanctioned deploy path.
# Usage: ./deploy.sh [--dry-run]
set -euo pipefail
cd "$(dirname "$0")"
# version.json is a deploy-time artifact — never leave it behind on failure
trap 'rm -f version.json' EXIT

HOST=orin
DEST=/data/projects/gimbal/wavecam
SHA=$(git rev-parse --short HEAD)
BRANCH=$(git rev-parse --abbrev-ref HEAD)
DIRTY=$(git status --porcelain -- . | head -1)
[ -n "$DIRTY" ] && { echo "REFUSED: orin/wavecam has uncommitted changes"; exit 1; }
python3 -m pytest -q tests || { echo "REFUSED: tests red — fix before deploying"; exit 1; }

printf '{"git_sha": "%s", "branch": "%s", "deployed_at": "%s"}\n' \
  "$SHA" "$BRANCH" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > version.json

RSYNC_FLAGS=(-av --delete
  --exclude '__pycache__' --exclude '*.pyc'
  --exclude 'camera_pose.json'          # persisted calibration — rig-owned
  --exclude 'config.local.yaml'         # hot-config overlay — rig-owned, survives deploys
  --exclude 'auth.json'                 # rig-owned credentials
  --exclude '*.log')
[ "${1:-}" = "--dry-run" ] && RSYNC_FLAGS+=(--dry-run)

ssh $HOST "cp -a $DEST ${DEST}.bak-\$(date +%Y%m%d-%H%M) 2>/dev/null || true"
rsync "${RSYNC_FLAGS[@]}" ./ "$HOST:$DEST/"
rm version.json
[ "${1:-}" = "--dry-run" ] && { echo "dry-run only"; exit 0; }

ssh $HOST 'sudo systemctl restart wavecam.service'
sleep 12
ssh $HOST 'systemctl is-active wavecam.service'
DEPLOYED=$(ssh $HOST "curl -s localhost:8088/api/v1/version" | python3 -c 'import json,sys; print(json.load(sys.stdin)["git_sha"])')
[ "$DEPLOYED" = "$SHA" ] && echo "DEPLOY OK: $SHA live" || { echo "DEPLOY MISMATCH: rig=$DEPLOYED local=$SHA"; exit 1; }

# H4 post-deploy health gate: two "zombie rigs" (API answering, vision loop
# dead) passed the SHA check alone. Require /health to show the loop heartbeat
# ok AND capture fps > 0 before calling the deploy good. The service just
# restarted, so allow a few retries (~30 s) for the loop to spin up.
HEALTH_OK=0
for _attempt in 1 2 3 4 5 6; do
  if ssh $HOST "curl -s --max-time 5 localhost:8088/api/v1/health" | python3 -c '
import json, sys
try:
    h = json.load(sys.stdin)
    comps = h.get("components", {})
    loop_ok = bool(comps.get("loop", {}).get("ok"))
    fps = float(comps.get("capture", {}).get("detail", {}).get("fps") or 0.0)
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if loop_ok and fps > 0.0 else 1)
'; then
    HEALTH_OK=1
    break
  fi
  echo "  health gate: loop/capture not ready (attempt ${_attempt}/6), retrying in 5s..."
  sleep 5
done
if [ "$HEALTH_OK" != 1 ]; then
  echo "DEPLOY FAILED: /api/v1/health never showed components.loop.ok=true with capture fps>0 (zombie rig?)."
  echo "  Inspect: ssh $HOST 'curl -s localhost:8088/api/v1/health | python3 -m json.tool'"
  exit 1
fi
echo "HEALTH OK: loop alive, capture fps > 0"

# ESP freshness check — non-fatal: kernel staleness is an operator action, not a deploy blocker.
# sync-esp.sh --check exits 0 (in-sync) or 2 (stale); 1 = preflight error (wrong device, etc.)
if ssh $HOST "sudo bash $DEST/tools/sync-esp.sh --check" 2>&1; then
  echo "ESP: in sync"
else
  _esp_rc=$?
  if [ $_esp_rc -eq 2 ]; then
    echo ""
    echo "WARNING: ESP kernel/initrd copies are STALE."
    echo "  The rig will boot the OLD kernel until you run:"
    echo "    ssh $HOST 'sudo bash $DEST/tools/sync-esp.sh'"
    echo ""
  else
    echo "WARNING: sync-esp.sh --check returned unexpected exit code $_esp_rc (preflight error — check SSH/path)"
  fi
fi
