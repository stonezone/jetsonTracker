# Map-based base placement + heading — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the WaveCam operator place the camera base location and set heading on an Apple satellite map, bypassing ±15 m GPS base noise, by writing to the existing calibration endpoints.

**Architecture:** A new SwiftUI screen (`MapPlacementView`) wrapping `MKMapView` (UIViewRepresentable, `.hybridFlyover`→`.hybrid`) with a fixed center crosshair for base placement and a look-at pin for heading. It computes lat/lon + bearing/distance app-side and POSTs to the already-supported manual-location and target-coords heading endpoints via new `WaveCamClient` methods. It runs inside an active CALIBRATE session. Pure logic (geo math, request bodies, placement guards) is TDD'd in a new `WaveCamTests` target; the SwiftUI/MapKit UI is verified on-device (project norm: "the build is the source of truth").

**Tech Stack:** Swift 5.9 / SwiftUI / MapKit (iOS 17), xcodegen, existing `WaveCamClient` (URLSession). Backend unchanged (FastAPI `/api/v1/calibration/*`).

## Global Constraints

- iOS deployment target **17.0** (verbatim from `project.yml`). MapKit/`MKMapView` UIViewRepresentable only — **no Google SDK**.
- All calibration writes require an **active CALIBRATE session** (PTZ owner = `calibrate`); re-check at each POST.
- **KILL must stay reachable** at all times; never block the main UI.
- Every GET/POST goes through the existing client transport (`getWithFallback` / `post`) — never a raw non-failover request.
- Backend endpoints are **fixed**: `POST /api/v1/calibration/location` (manual: `lat`,`lon`,`use_live_base:false`,`manual_error_radius_m`) and `POST /api/v1/calibration/heading-lock` (`target_lat`,`target_lon`,`operator_accepted`,`max_uncertainty_deg` — **omit `pan_enc`** so the backend captures the live encoder at request time = review finding V1). No backend changes in this plan.
- New Source files require `xcodegen generate` before building (per repo `CLAUDE.md`).

---

## File Structure

- `ios/WaveCam/project.yml` — **modify**: add a `WaveCamTests` unit-test target.
- `ios/WaveCam/Sources/GeoMath.swift` — **create**: pure `geoBearingDeg(from:to:)` + `haversineMeters(_:_:)`. One responsibility: spherical geometry, parity with backend `gps_geo`.
- `ios/WaveCam/Sources/MapPlacementModel.swift` — **create**: `@Observable` view-model — base coord, look-at coord, mode, tile-loaded flag, derived `lookAtBearingDeg`/`lookAtDistanceM`/`isLookAtValid`, `errorRadiusM(forMetersAcross:)`. Pure, testable; no UIKit/MapKit imports.
- `ios/WaveCam/Sources/MapPlacementView.swift` — **create**: SwiftUI screen + `MKMapView` UIViewRepresentable (`MapKitContainer`), crosshair overlay, mode controls, confirm actions, tile-load + distance guards. UI only.
- `ios/WaveCam/Sources/WaveCamClient.swift` — **modify**: add `calibrateLocationManual(...)` and `calibrateMapHeading(preview:targetLat:targetLon:source:)`; factor request bodies into pure builders for testing.
- `ios/WaveCam/Sources/CalibrateView.swift` — **modify**: add "Place on map" (LocationCard) and "Set heading on map" (HeadingCard) buttons that present `MapPlacementView` as a sheet and apply results.
- `ios/WaveCam/Tests/` — **create**: `GeoMathTests.swift`, `MapPlacementModelTests.swift`, `CalibrationRequestTests.swift`.

---

## Task 1: Add a unit-test target

**Files:**
- Modify: `ios/WaveCam/project.yml`
- Create: `ios/WaveCam/Tests/SmokeTests.swift`

**Interfaces:**
- Produces: a runnable `WaveCamTests` target so later tasks can TDD pure logic via `xcodebuild test`.

- [ ] **Step 1: Add the test target to `project.yml`** (append under `targets:`)

```yaml
  WaveCamTests:
    type: bundle.unit-test
    platform: iOS
    sources:
      - Tests
    dependencies:
      - target: WaveCam
    settings:
      GENERATE_INFOPLIST_FILE: true
      TARGETED_DEVICE_FAMILY: "1"
```

- [ ] **Step 2: Create a trivial smoke test**

`ios/WaveCam/Tests/SmokeTests.swift`:
```swift
import XCTest
@testable import WaveCam

final class SmokeTests: XCTestCase {
    func testHarnessRuns() { XCTAssertEqual(2 + 2, 4) }
}
```

- [ ] **Step 3: Regenerate the project**

Run: `cd ios/WaveCam && xcodegen generate`
Expected: "Created project at .../WaveCam.xcodeproj" with no error; the scheme list now includes `WaveCamTests`.

- [ ] **Step 4: Run the test on a simulator**

Run: `cd ios/WaveCam && xcodebuild test -scheme WaveCam -destination 'platform=iOS Simulator,name=iPhone 16' -only-testing:WaveCamTests/SmokeTests 2>&1 | tail -5`
Expected: `** TEST SUCCEEDED **`. (If `WaveCam` isn't a test-enabled scheme, use `-scheme WaveCamTests`; if no `iPhone 16` sim, substitute one from `xcrun simctl list devices available`.)

- [ ] **Step 5: Commit**

```bash
git add ios/WaveCam/project.yml ios/WaveCam/Tests/SmokeTests.swift ios/WaveCam/WaveCam.xcodeproj
git commit -m "test(ios): add WaveCamTests unit-test target"
```

---

## Task 2: Geo math (bearing + distance), parity with backend

**Files:**
- Create: `ios/WaveCam/Sources/GeoMath.swift`
- Test: `ios/WaveCam/Tests/GeoMathTests.swift`

**Interfaces:**
- Produces: `enum GeoMath { static func bearingDeg(fromLat:fromLon:toLat:toLon:) -> Double; static func haversineMeters(fromLat:fromLon:toLat:toLon:) -> Double }` — returns forward azimuth in [0,360) and great-circle meters. Used by `MapPlacementModel` and the heading-parity test (review finding V9).

- [ ] **Step 1: Write the failing tests**

`ios/WaveCam/Tests/GeoMathTests.swift`:
```swift
import XCTest
@testable import WaveCam

final class GeoMathTests: XCTestCase {
    func testDueNorthBearingIsZero() {
        let b = GeoMath.bearingDeg(fromLat: 21.6451, fromLon: -158.0501, toLat: 21.6461, toLon: -158.0501)
        XCTAssertEqual(b, 0, accuracy: 0.5)
    }
    func testDueEastBearingIs90() {
        let b = GeoMath.bearingDeg(fromLat: 21.6451, fromLon: -158.0501, toLat: 21.6451, toLon: -158.0490)
        XCTAssertEqual(b, 90, accuracy: 0.5)
    }
    func testBearingWrapsTo360Range() {
        let b = GeoMath.bearingDeg(fromLat: 21.6451, fromLon: -158.0501, toLat: 21.6441, toLon: -158.0501)
        XCTAssertEqual(b, 180, accuracy: 0.5)
    }
    func testHaversineKnownDistance() {
        // ~0.001 deg lat ≈ 111.2 m
        let d = GeoMath.haversineMeters(fromLat: 21.6451, fromLon: -158.0501, toLat: 21.6461, toLon: -158.0501)
        XCTAssertEqual(d, 111.2, accuracy: 1.0)
    }
}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ios/WaveCam && xcodebuild test -scheme WaveCam -destination 'platform=iOS Simulator,name=iPhone 16' -only-testing:WaveCamTests/GeoMathTests 2>&1 | tail -5`
Expected: FAIL — `cannot find 'GeoMath' in scope`.

- [ ] **Step 3: Implement**

`ios/WaveCam/Sources/GeoMath.swift`:
```swift
import Foundation

/// Spherical geometry, matching the backend's gps_geo bearing/haversine so a
/// map-computed bearing equals what the rig computes for the same coordinates.
enum GeoMath {
    static func bearingDeg(fromLat lat1: Double, fromLon lon1: Double,
                           toLat lat2: Double, toLon lon2: Double) -> Double {
        let p1 = lat1 * .pi / 180, p2 = lat2 * .pi / 180
        let dl = (lon2 - lon1) * .pi / 180
        let y = sin(dl) * cos(p2)
        let x = cos(p1) * sin(p2) - sin(p1) * cos(p2) * cos(dl)
        let deg = atan2(y, x) * 180 / .pi
        return (deg.truncatingRemainder(dividingBy: 360) + 360).truncatingRemainder(dividingBy: 360)
    }

    static func haversineMeters(fromLat lat1: Double, fromLon lon1: Double,
                                toLat lat2: Double, toLon lon2: Double) -> Double {
        let r = 6_371_000.0
        let p1 = lat1 * .pi / 180, p2 = lat2 * .pi / 180
        let dp = (lat2 - lat1) * .pi / 180, dl = (lon2 - lon1) * .pi / 180
        let a = sin(dp/2)*sin(dp/2) + cos(p1)*cos(p2)*sin(dl/2)*sin(dl/2)
        return r * 2 * atan2(sqrt(a), sqrt(1-a))
    }
}
```

- [ ] **Step 4: Run to verify pass**

Run: same as Step 2.
Expected: `** TEST SUCCEEDED **`.

- [ ] **Step 5: Commit**

```bash
git add ios/WaveCam/Sources/GeoMath.swift ios/WaveCam/Tests/GeoMathTests.swift ios/WaveCam/WaveCam.xcodeproj
git commit -m "feat(ios): GeoMath bearing+haversine (backend parity)"
```

---

## Task 3: MapPlacement view-model (state + guards)

**Files:**
- Create: `ios/WaveCam/Sources/MapPlacementModel.swift`
- Test: `ios/WaveCam/Tests/MapPlacementModelTests.swift`

**Interfaces:**
- Consumes: `GeoMath` (Task 2).
- Produces: `@Observable final class MapPlacementModel` with `var baseLat/baseLon: Double?`, `var lookAtLat/lookAtLon: Double?`, `var tilesLoaded: Bool`, `enum Mode { case base, headingLookAt, headingArrow }`, `var mode`, `var arrowBearingDeg: Double`; computed `lookAtDistanceM: Double?`, `lookAtBearingDeg: Double?`, `isLookAtValid: Bool` (distance ≥ `Self.minLookAtMeters` = 50), `func errorRadiusM(metersAcross: Double, screenWidthPoints: Double) -> Double`, `var canConfirmLocation: Bool`, `var canConfirmHeading: Bool`. Encodes review findings V2 (min look-at distance) and V4 (honest radius).

- [ ] **Step 1: Write the failing tests**

`ios/WaveCam/Tests/MapPlacementModelTests.swift`:
```swift
import XCTest
@testable import WaveCam

final class MapPlacementModelTests: XCTestCase {
    func testLookAtInvalidWhenTooClose() {
        let m = MapPlacementModel()
        m.baseLat = 21.6451; m.baseLon = -158.0501
        m.lookAtLat = 21.64512; m.lookAtLon = -158.0501   // ~2 m away
        XCTAssertFalse(m.isLookAtValid)                    // < 50 m min
    }
    func testLookAtValidWhenFarEnough() {
        let m = MapPlacementModel()
        m.baseLat = 21.6451; m.baseLon = -158.0501
        m.lookAtLat = 21.6461; m.lookAtLon = -158.0501     // ~111 m away
        XCTAssertTrue(m.isLookAtValid)
        XCTAssertEqual(m.lookAtBearingDeg ?? -1, 0, accuracy: 0.5)
    }
    func testErrorRadiusScalesWithZoomAndHasFloor() {
        let m = MapPlacementModel()
        // 200 m across a 400-pt-wide map ⇒ 0.5 m/pt; 8 pt slop ⇒ 4 m
        XCTAssertEqual(m.errorRadiusM(metersAcross: 200, screenWidthPoints: 400), 4.0, accuracy: 0.1)
        // Zoomed way out: large radius, not the 3 m the spec warned against
        XCTAssertGreaterThan(m.errorRadiusM(metersAcross: 5000, screenWidthPoints: 400), 30)
        // Floor never below 2.5 m
        XCTAssertEqual(m.errorRadiusM(metersAcross: 10, screenWidthPoints: 400), 2.5, accuracy: 0.01)
    }
    func testCanConfirmLocationRequiresTilesAndBase() {
        let m = MapPlacementModel()
        m.baseLat = 21.6451; m.baseLon = -158.0501
        XCTAssertFalse(m.canConfirmLocation)   // tiles not loaded (V5)
        m.tilesLoaded = true
        XCTAssertTrue(m.canConfirmLocation)
    }
}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ios/WaveCam && xcodebuild test -scheme WaveCam -destination 'platform=iOS Simulator,name=iPhone 16' -only-testing:WaveCamTests/MapPlacementModelTests 2>&1 | tail -5`
Expected: FAIL — `cannot find 'MapPlacementModel' in scope`.

- [ ] **Step 3: Implement**

`ios/WaveCam/Sources/MapPlacementModel.swift`:
```swift
import Foundation
import Observation

@Observable
final class MapPlacementModel {
    enum Mode { case base, headingLookAt, headingArrow }

    static let minLookAtMeters = 50.0   // V2: short look-at => bad heading
    static let radiusFloorM = 2.5
    static let slopPoints = 8.0         // assumed placement slop in screen points

    var mode: Mode = .base
    var baseLat: Double?
    var baseLon: Double?
    var lookAtLat: Double?
    var lookAtLon: Double?
    var arrowBearingDeg: Double = 0
    var tilesLoaded = false

    var lookAtDistanceM: Double? {
        guard let bla = baseLat, let blo = baseLon, let lla = lookAtLat, let llo = lookAtLon else { return nil }
        return GeoMath.haversineMeters(fromLat: bla, fromLon: blo, toLat: lla, toLon: llo)
    }
    var lookAtBearingDeg: Double? {
        guard let bla = baseLat, let blo = baseLon, let lla = lookAtLat, let llo = lookAtLon else { return nil }
        return GeoMath.bearingDeg(fromLat: bla, fromLon: blo, toLat: lla, toLon: llo)
    }
    var isLookAtValid: Bool { (lookAtDistanceM ?? 0) >= Self.minLookAtMeters }

    /// V4: error radius scales with map zoom; never the optimistic fixed 3 m.
    func errorRadiusM(metersAcross: Double, screenWidthPoints: Double) -> Double {
        let metersPerPoint = metersAcross / max(screenWidthPoints, 1)
        return max(Self.radiusFloorM, metersPerPoint * Self.slopPoints)
    }

    var canConfirmLocation: Bool { baseLat != nil && baseLon != nil && tilesLoaded }
    var canConfirmHeading: Bool {
        guard tilesLoaded else { return false }
        switch mode {
        case .headingLookAt: return isLookAtValid
        case .headingArrow: return true
        case .base: return false
        }
    }
}
```

- [ ] **Step 4: Run to verify pass** — same command as Step 2. Expected: `** TEST SUCCEEDED **`.

- [ ] **Step 5: Commit**

```bash
git add ios/WaveCam/Sources/MapPlacementModel.swift ios/WaveCam/Tests/MapPlacementModelTests.swift ios/WaveCam/WaveCam.xcodeproj
git commit -m "feat(ios): MapPlacementModel state + distance/radius/tile guards"
```

---

## Task 4: Client methods (manual location + map heading)

**Files:**
- Modify: `ios/WaveCam/Sources/WaveCamClient.swift`
- Test: `ios/WaveCam/Tests/CalibrationRequestTests.swift`

**Interfaces:**
- Consumes: existing `WCCalibrationSessionState`, `WaveCamCalibrationError`, the private `post(...)` transport, `Self.decoder`, and the existing pattern used by `calibrateLocation`/`calibrateHeadingLockAccept`.
- Produces, as `static` pure builders (testable) + async wrappers:
  - `static func mapLocationBody(lat:Double, lon:Double, errorRadiusM:Double, source:String) -> [String: Any]`
  - `static func mapHeadingBody(targetLat:Double, targetLon:Double, operatorAccepted:Bool, source:String) -> [String: Any]`
  - `func calibrateLocationManual(lat:Double, lon:Double, errorRadiusM:Double, source:String = "ios_native") async -> Result<WCCalibrationSessionState, WaveCamCalibrationError>`
  - `func calibrateMapHeading(preview:Bool, targetLat:Double, targetLon:Double, source:String = "ios_native") async -> Result<WCCalibrationSessionState, WaveCamCalibrationError>`
- Note (V1): the heading body **omits `pan_enc`** → backend captures the live encoder at request time. Body uses `method:"map_lookat"`, `max_uncertainty_deg` left to the backend default unless tuning.

- [ ] **Step 1: Write the failing tests** (pure builders only — no network)

`ios/WaveCam/Tests/CalibrationRequestTests.swift`:
```swift
import XCTest
@testable import WaveCam

final class CalibrationRequestTests: XCTestCase {
    func testMapLocationBodyUsesManualCoordsNotLiveBase() {
        let b = WaveCamClient.mapLocationBody(lat: 21.6451, lon: -158.0501, errorRadiusM: 4, source: "ios_native")
        XCTAssertEqual(b["lat"] as? Double, 21.6451)
        XCTAssertEqual(b["lon"] as? Double, -158.0501)
        XCTAssertEqual(b["use_live_base"] as? Bool, false)
        XCTAssertEqual(b["manual_error_radius_m"] as? Double, 4)
        XCTAssertEqual(b["method"] as? String, "map_manual")
    }
    func testMapHeadingBodyOmitsPanEnc() {
        let b = WaveCamClient.mapHeadingBody(targetLat: 21.6461, targetLon: -158.0501, operatorAccepted: true, source: "ios_native")
        XCTAssertEqual(b["target_lat"] as? Double, 21.6461)
        XCTAssertEqual(b["target_lon"] as? Double, -158.0501)
        XCTAssertEqual(b["operator_accepted"] as? Bool, true)
        XCTAssertNil(b["pan_enc"])                        // V1: backend captures it
        XCTAssertEqual(b["method"] as? String, "map_lookat")
    }
}
```

- [ ] **Step 2: Run to verify failure** — `-only-testing:WaveCamTests/CalibrationRequestTests`. Expected: FAIL — `type 'WaveCamClient' has no member 'mapLocationBody'`.

- [ ] **Step 3: Implement** — add to `WaveCamClient` (place beside the existing `calibrateLocation` / `calibrateHeadingLockAccept` methods; read the file to match their `post(...)`/`Self.decoder` error-handling pattern, then mirror it):

```swift
// MARK: - Map-based placement (manual coords; bypasses GPS noise)

static func mapLocationBody(lat: Double, lon: Double, errorRadiusM: Double, source: String) -> [String: Any] {
    ["method": "map_manual", "use_live_base": false,
     "lat": lat, "lon": lon, "manual_error_radius_m": errorRadiusM, "source": source]
}

static func mapHeadingBody(targetLat: Double, targetLon: Double, operatorAccepted: Bool, source: String) -> [String: Any] {
    // pan_enc intentionally omitted — backend captures the live encoder at request time (V1).
    ["method": "map_lookat", "operator_accepted": operatorAccepted,
     "target_lat": targetLat, "target_lon": targetLon, "source": source]
}

func calibrateLocationManual(lat: Double, lon: Double, errorRadiusM: Double,
                             source: String = "ios_native") async
    -> Result<WCCalibrationSessionState, WaveCamCalibrationError> {
    guard mode == .live else { return .failure(.unavailable) }
    return await sendCalibrationSession("calibration/location",
                                        body: Self.mapLocationBody(lat: lat, lon: lon, errorRadiusM: errorRadiusM, source: source))
}

func calibrateMapHeading(preview: Bool, targetLat: Double, targetLon: Double,
                         source: String = "ios_native") async
    -> Result<WCCalibrationSessionState, WaveCamCalibrationError> {
    guard mode == .live else { return .failure(.unavailable) }
    return await sendCalibrationSession("calibration/heading-lock",
                                        body: Self.mapHeadingBody(targetLat: targetLat, targetLon: targetLon, operatorAccepted: !preview, source: source))
}
```

> Note: reuse whatever private helper `calibrateLocation`/`calibrateHeadingLockAccept` already use to POST + decode `WCCalibrationSessionState` (the Explore notes call it like `sendCalibrationSession(_:body:)`). If the actual helper name differs, match it — do not invent a new transport.

- [ ] **Step 4: Run to verify pass** — Step 2 command. Expected: `** TEST SUCCEEDED **`.

- [ ] **Step 5: Build the app target** to confirm the async wrappers compile against the real helper:

Run: `cd ios/WaveCam && xcodebuild build -scheme WaveCam -destination 'platform=iOS Simulator,name=iPhone 16' 2>&1 | tail -3`
Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 6: Commit**

```bash
git add ios/WaveCam/Sources/WaveCamClient.swift ios/WaveCam/Tests/CalibrationRequestTests.swift
git commit -m "feat(ios): client methods for map-based manual location + look-at heading"
```

---

## Task 5: MapPlacementView (MapKit UI)

**Files:**
- Create: `ios/WaveCam/Sources/MapPlacementView.swift`

**Interfaces:**
- Consumes: `MapPlacementModel` (Task 3), `WaveCamClient` (Task 4). Presented as a sheet from `CalibrateView`.
- Produces: `struct MapPlacementView: View` initialized with the `WaveCamClient`, an initial center `(lat,lon)`, a `purpose: MapPlacementModel.Mode` (`.base` or heading), and `onDone: () -> Void`. Internally hosts `MapKitContainer: UIViewRepresentable` (wraps `MKMapView`) to get the tile-load delegate callback and a fixed center crosshair.

Implementation notes (UI — verified on device, not unit-tested):
- Use `MKMapView` via `UIViewRepresentable`, **not** SwiftUI `Map` — needed for `mapViewDidFinishLoadingMap`/`mapViewDidFailLoadingMap` (→ `model.tilesLoaded`, finding V5) and `mapType = .hybrid`.
- **Base mode:** fixed center crosshair (SF Symbol `plus` overlaid at center); on `regionDidChange`, write `mapView.centerCoordinate` → `model.baseLat/Lon` and `model.errorRadiusM(metersAcross: region→meters, screenWidthPoints: bounds.width)`. "Use this location" → `client.calibrateLocationManual(...)`, disabled unless `model.canConfirmLocation`.
- **Look-at heading mode:** keep the base pin fixed; a draggable look-at annotation (or "drop at center") → `model.lookAtLat/Lon`. Show live `lookAtDistanceM` + a banner "move the look-at point ≥50 m out" when `!isLookAtValid` (V2). Optional polyline from base along the camera's current bearing (V3) — deferred to Task 7 (needs `pan_enc`). "Set heading" runs `calibrateMapHeading(preview:true,...)` then, on operator accept, `preview:false`; disabled unless `model.canConfirmHeading`.
- **Arrow mode:** lock `mapView` to North-up (`isRotateEnabled = false`, camera heading 0 — V6); a rotatable arrow overlay sets `model.arrowBearingDeg`; "Set heading" posts via the existing `calibrateHeadingLockAccept(bearingDeg:)` path.
- Re-check session at confirm (V7): before each POST, the caller verifies `client.status?.calibration?.active == true` (or the session field) and surfaces an error if not.
- Aim guidance (V3): show inline text "Aim the camera at the landmark in the Live view, keep it still, then drop the pin" so the operator uses the **video optical center** to aim.

- [ ] **Step 1: Create the view** — `ios/WaveCam/Sources/MapPlacementView.swift` (full skeleton; fill the action closures against the Task-4 client methods):

```swift
import SwiftUI
import MapKit

struct MapPlacementView: View {
    @State private var model = MapPlacementModel()
    let client: WaveCamClient
    let initialLat: Double
    let initialLon: Double
    let purpose: MapPlacementModel.Mode
    let onDone: () -> Void
    @State private var busy = false
    @State private var message: String?

    var body: some View {
        VStack(spacing: 12) {
            MapKitContainer(model: model, initialLat: initialLat, initialLon: initialLon)
                .overlay(Image(systemName: "plus").font(.title2).opacity(model.mode == .base ? 1 : 0))
            if !model.tilesLoaded {
                Text("Map imagery not loaded — connect to load satellite tiles")
                    .font(.footnote).foregroundStyle(.orange)
            }
            controls
            if let message { Text(message).font(.footnote).foregroundStyle(.secondary) }
        }
        .onAppear { model.mode = purpose }
        .padding()
    }

    @ViewBuilder private var controls: some View {
        switch model.mode {
        case .base:
            Button("Use this location") { Task { await confirmLocation() } }
                .disabled(!model.canConfirmLocation || busy)
        case .headingLookAt:
            if let d = model.lookAtDistanceM { Text(String(format: "look-at %.0f m", d)).font(.footnote) }
            if !model.isLookAtValid { Text("Move the look-at point ≥50 m out").foregroundStyle(.orange).font(.footnote) }
            Text("Aim the camera at the landmark in Live, keep it still, then confirm.").font(.caption2).foregroundStyle(.secondary)
            Button("Set heading from look-at") { Task { await confirmHeading() } }
                .disabled(!model.canConfirmHeading || busy)
        case .headingArrow:
            Text("North-up. Rotate the arrow to the camera's forward direction.").font(.caption2)
            Button("Set heading from arrow") { Task { await confirmArrowHeading() } }
                .disabled(busy)
        }
    }

    private func confirmLocation() async {
        busy = true; defer { busy = false }
        let r = await client.calibrateLocationManual(lat: model.baseLat!, lon: model.baseLon!,
                                                      errorRadiusM: model.lastErrorRadiusM)
        apply(r)
    }
    private func confirmHeading() async {
        busy = true; defer { busy = false }
        let r = await client.calibrateMapHeading(preview: false, targetLat: model.lookAtLat!, targetLon: model.lookAtLon!)
        apply(r)
    }
    private func confirmArrowHeading() async {
        busy = true; defer { busy = false }
        await client.calibrateHeadingLockAccept(bearingDeg: model.arrowBearingDeg, distanceM: nil)
        onDone()
    }
    private func apply(_ r: Result<WCCalibrationSessionState, WaveCamCalibrationError>) {
        switch r {
        case .success: onDone()
        case .failure(let e): message = "Failed: \(e)"
        }
    }
}
```

> `model.lastErrorRadiusM` is a stored `Double` the `MapKitContainer` updates on region change (add `var lastErrorRadiusM = MapPlacementModel.radiusFloorM` to the model). Wire the `MKMapViewDelegate` (`mapViewDidFinishLoadingMap` → `model.tilesLoaded = true`; `regionDidChangeAnimated` → update center coords + `lastErrorRadiusM`; for look-at mode, a long-press or center-drop sets `lookAtLat/Lon`).

- [ ] **Step 2: Regenerate + build**

Run: `cd ios/WaveCam && xcodegen generate && xcodebuild build -scheme WaveCam -destination 'platform=iOS Simulator,name=iPhone 16' 2>&1 | tail -3`
Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 3: Commit**

```bash
git add ios/WaveCam/Sources/MapPlacementView.swift ios/WaveCam/Sources/MapPlacementModel.swift ios/WaveCam/WaveCam.xcodeproj
git commit -m "feat(ios): MapPlacementView (MKMapView hybrid, crosshair, guards)"
```

---

## Task 6: Wire into CalibrateView

**Files:**
- Modify: `ios/WaveCam/Sources/CalibrateView.swift`

**Interfaces:**
- Consumes: `MapPlacementView` (Task 5), the existing `WizardStep`/`LocationCard`/`HeadingCard`, the `WaveCamClient` instance the view already holds, and the current base GPS fix (for the map's initial center) from the client's status (`sensors.base` lat/lon; fall back to a sane default if absent).

- [ ] **Step 1: Add map-entry state + sheet** — in `CalibrateView` add `@State private var showingMap = false` and `@State private var mapPurpose: MapPlacementModel.Mode = .base`, and a `.sheet(isPresented: $showingMap) { MapPlacementView(client: client, initialLat: <base lat or 0>, initialLon: <base lon or 0>, purpose: mapPurpose) { showingMap = false; Task { await refreshCalibration() } } }`. (Use the view's existing calibration-refresh call in the `onDone` closure.)

- [ ] **Step 2: Add the buttons** — in `LocationCard`, beside the existing "Lock location" button, add `Button("Place on map") { mapPurpose = .base; showingMap = true }`. In `HeadingCard`, beside the existing preview/accept flow, add `Button("Set heading on map") { mapPurpose = .headingLookAt; showingMap = true }`. (Read the current card bodies to place these in the existing button row/stack.)

- [ ] **Step 3: Regenerate + build**

Run: `cd ios/WaveCam && xcodegen generate && xcodebuild build -scheme WaveCam -destination 'platform=iOS Simulator,name=iPhone 16' 2>&1 | tail -3`
Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 4: Commit**

```bash
git add ios/WaveCam/Sources/CalibrateView.swift
git commit -m "feat(ios): Calibrate wizard — Place-on-map entry points"
```

---

## Task 7: On-device + live-rig end-to-end verification

**Files:** none (verification + a regression note).

- [ ] **Step 1: Install on device**

Run: `cd ios/WaveCam && ./build-device.sh` (signing/UDID per the `.claude` `ios-app-build` memory). Expected: app installs; build number stamped.

- [ ] **Step 2: Location placement** — On the rig (CALIBRATE started): Calibrate tab → Location → "Place on map" → pan/zoom so a known landmark is centered → "Use this location". Verify `GET /api/v1/calibration` shows `location.method == "map_manual"`, `lat`/`lon` ≈ the map center, and `error_radius_m` reflecting zoom (not a fixed 3 m). Confirm the button was disabled until tiles loaded.

- [ ] **Step 3: Look-at heading** — Aim the camera (Live view) at a **distant** landmark (≥50 m), keep it still → Heading → "Set heading on map" → drop the look-at pin on that landmark → confirm the ≥50 m guard passes → set. Verify `/calibration` shows a heading captured and `reference_heading` is sane; confirm the heading endpoint received **no `pan_enc`** (backend used the live encoder).

- [ ] **Step 4: Guard checks** — With network off, confirm the location "Use this location" button stays disabled and the banner shows (V5). With a <50 m look-at, confirm the heading button stays disabled with the warning (V2). Drop CALIBRATE mid-flow (exit on another device/web) and confirm a POST fails clean, not silently (V7).

- [ ] **Step 5: Accuracy comparison (the point of the feature)** — Lock the base via map vs via GPS at the same spot; compare GPS-pointing miss against a known target. Map placement should not be worse, and should remove the GPS base bias. Record numbers in the field memory.

- [ ] **Step 6: Commit the verification note**

```bash
git add docs/superpowers/plans/2026-06-20-map-base-placement.md
git commit -m "docs(plan): map placement verified on device + rig"
```

---

## Self-Review

- **Spec coverage:** location-by-map → Tasks 4–6; both heading models → Task 5 (look-at + arrow); Apple MapKit hybrid → Task 5; review guards V1 (omit pan_enc, Task 4), V2 (min look-at, Task 3/5), V4 (scaled radius, Task 3), V5 (tile gate, Task 3/5), V6 (North-up arrow, Task 5), V7 (session re-check, Task 5/6); V3 aim-via-video (Task 5 guidance) + the optional camera-bearing polyline deferred (needs `pan_enc` exposure — flagged below); V8 (location-before-heading) is enforced by the wizard order (Location step precedes Heading step) + Step 3 verifies it; V9 bearing parity → Task 2 tests. Testing → Tasks 2–4 (unit) + Task 7 (e2e).
- **Deferred / flagged:** the V3 "draw the camera's current bearing as a polyline on the map" enhancement needs `pan_enc` (or a computed camera bearing) added to the iOS status `PTZ` model — not currently exposed. Left as a follow-up; the aim-via-Live-view text guidance covers the core need without it.
- **Placeholder scan:** no TBDs; the one soft spot is the exact private POST-helper name in `WaveCamClient` (`sendCalibrationSession`) — Task 4 instructs matching the real helper rather than inventing one.
- **Type consistency:** `MapPlacementModel.Mode`, `errorRadiusM`, `calibrateLocationManual`, `calibrateMapHeading`, `mapLocationBody`/`mapHeadingBody`, `GeoMath.bearingDeg`/`haversineMeters` are referenced consistently across Tasks 2–6.
