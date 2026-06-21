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
}
