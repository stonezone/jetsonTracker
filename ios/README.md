# WaveCam — iOS operator app

Native iPhone control surface for the WaveCam rig. Talks to the Orin Control API
(`/api/v1`); a built-in **mock mode** runs the full UI without the Orin.

## Build

The Xcode project is generated (not committed). Generate, then build:

```bash
cd ios/WaveCam
xcodegen generate
open WaveCam.xcodeproj   # or build via xcodebuild / XcodeBuildMCP
```

- iPhone-only, iOS 17+. Bundle id `com.stonezone.WaveCam`.
- Design: `docs/superpowers/specs/2026-06-01-wavecam-ios-app-spec.md`
- API contract: `docs/superpowers/specs/2026-06-01-wavecam-control-api-spec.md`
- Mockup reference: `docs/superpowers/specs/2026-06-01-wavecam-ios-app-mockup.html`
