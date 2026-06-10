#!/usr/bin/env bash
# Build + install WaveCam on Zack's iPhone with a git-derived build number, so the
# Connect tab's version line uniquely identifies every install (was hardcoded "27").
#   ./build-device.sh            # build + install
#   ./build-device.sh build      # build only
set -euo pipefail
cd "$(dirname "$0")"

DEVICE_UDID="44AC4E62-45B1-58A0-8571-857F1EC2E014"   # iPhone 15 Pro Max
BUILD_NUM=$(git rev-list --count HEAD)
DERIVED=/tmp/wavecam-device-build

echo "Build number: $BUILD_NUM ($(git rev-parse --short HEAD))"
xcodegen generate
xcodebuild -project WaveCam.xcodeproj -scheme WaveCam -configuration Debug \
  -destination "id=$DEVICE_UDID" -derivedDataPath "$DERIVED" \
  -allowProvisioningUpdates CURRENT_PROJECT_VERSION="$BUILD_NUM" build \
  | grep -E "BUILD|error:" | tail -3

if [[ "${1:-install}" != "build" ]]; then
  xcrun devicectl device install app --device "$DEVICE_UDID" \
    "$DERIVED/Build/Products/Debug-iphoneos/WaveCam.app"
  echo "Installed build $BUILD_NUM — verify on the Connect tab."
fi
