#!/usr/bin/env bash
# Vendor the Seeed Wio Tracker L1 board variant (pin map, clocks) from the
# Meshtastic firmware tree. Fetched at build-setup time rather than committed:
# keeps GPL provenance out of this repo while pinning exactly what we build
# against. Re-run is idempotent.
set -euo pipefail
PIN="master"   # promote to a commit SHA at Phase-1 flash proof
BASE="https://raw.githubusercontent.com/meshtastic/firmware/${PIN}/variants/nrf52840/seeed_wio_tracker_L1"
DEST="$(dirname "$0")/variants/seeed_wio_tracker_L1"
mkdir -p "$DEST"
for f in variant.h variant.cpp; do
  curl -fsSL "$BASE/$f" -o "$DEST/$f"
  echo "fetched $f ($(wc -c < "$DEST/$f") bytes)"
done
echo "variant vendored at $DEST (pin: $PIN)"
