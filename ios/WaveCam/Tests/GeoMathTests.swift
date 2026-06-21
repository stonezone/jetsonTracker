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
    func testDueSouthBearingWrapsTo180() {
        let b = GeoMath.bearingDeg(fromLat: 21.6451, fromLon: -158.0501, toLat: 21.6441, toLon: -158.0501)
        XCTAssertEqual(b, 180, accuracy: 0.5)
    }
    func testHaversineKnownDistance() {
        let d = GeoMath.haversineMeters(fromLat: 21.6451, fromLon: -158.0501, toLat: 21.6461, toLon: -158.0501)
        XCTAssertEqual(d, 111.2, accuracy: 1.0)   // ~0.001 deg lat
    }
    func testElevationMatchesBackendConstant() {
        // base 2 m, subject 1 m, 100 m out -> ~ -0.57 deg (looks down)
        let e = GeoMath.elevationDeg(baseAltM: 2.0, distanceM: 100.0)
        XCTAssertEqual(e, atan2(1.0 - 2.0, 100.0) * 180.0 / .pi, accuracy: 1e-9)
        XCTAssertLessThan(e, 0)
    }
    func testElevationLooksDownMoreFromHigherBase() {
        XCTAssertLessThan(GeoMath.elevationDeg(baseAltM: 13.0, distanceM: 100.0),
                          GeoMath.elevationDeg(baseAltM: 2.0, distanceM: 100.0))
    }
}
