#!/usr/bin/env bash
# Vendor the Seeed Wio Tracker L1 board variant (pin map, clocks, TCXO) from
# the Meshtastic firmware tree. Fetched at build-setup time rather than
# committed: keeps GPL provenance out of this repo while pinning EXACTLY what
# we build against. Re-run is idempotent.
#
# PINNED to a commit SHA (not a branch) so the same repo commit always builds
# against the same pin map — the variant defines load-bearing facts (the 1.8V
# DIO3 TCXO voltage, and that PIN_LED2 is physically the buzzer) that a silent
# upstream change must not be able to alter under us.
set -euo pipefail
PIN="88137c60e6d228150bb1541d7603dca8bcbbb43d"  # meshtastic/firmware master 2026-06-12
BASE="https://raw.githubusercontent.com/meshtastic/firmware/${PIN}/variants/nrf52840/seeed_wio_tracker_L1"
DEST="$(dirname "$0")/variants/seeed_wio_tracker_L1"
LOCK="$(dirname "$0")/variants.lock"
mkdir -p "$DEST"
: > "$LOCK"
echo "# meshtastic/firmware variant pin — fetched by fetch_variant.sh" >> "$LOCK"
echo "commit=$PIN" >> "$LOCK"
for f in variant.h variant.cpp; do
  curl -fsSL "$BASE/$f" -o "$DEST/$f"
  sha=$(shasum -a 256 "$DEST/$f" | cut -d' ' -f1)
  echo "$f sha256=$sha" >> "$LOCK"
  echo "fetched $f ($(wc -c < "$DEST/$f") bytes, sha256 ${sha:0:12})"
done
echo "variant vendored at $DEST (pinned ${PIN:0:12}); lock written to $LOCK"
