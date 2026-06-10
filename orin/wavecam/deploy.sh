#!/usr/bin/env bash
# Deploy orin/wavecam to the rig. The ONLY sanctioned deploy path.
# Usage: ./deploy.sh [--dry-run]
set -euo pipefail
cd "$(dirname "$0")"

HOST=orin
DEST=/data/projects/gimbal/wavecam
SHA=$(git rev-parse --short HEAD)
BRANCH=$(git rev-parse --abbrev-ref HEAD)
DIRTY=$(git status --porcelain -- . | head -1)
[ -n "$DIRTY" ] && { echo "REFUSED: orin/wavecam has uncommitted changes"; exit 1; }

printf '{"git_sha": "%s", "branch": "%s", "deployed_at": "%s"}\n' \
  "$SHA" "$BRANCH" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > version.json

RSYNC_FLAGS=(-av --delete
  --exclude '__pycache__' --exclude '*.pyc'
  --exclude 'camera_pose.json'          # persisted calibration — rig-owned
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
