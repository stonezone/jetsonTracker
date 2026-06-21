import XCTest
@testable import WaveCam

final class OffsetCalibrateModelTests: XCTestCase {
    private func goodFix() -> OffsetCalibrateModel {
        let m = OffsetCalibrateModel()
        m.baseLat = 21.6; m.baseLon = -158.0
        m.targetSats = 9; m.targetAgeSec = 1; m.stale = false
        m.distanceM = 80; m.bearingDeg = 0
        return m
    }

    func testCanCaptureNeedsQualityAndDistance() {
        let m = goodFix()
        XCTAssertTrue(m.canCapture)
        m.targetSats = 3
        XCTAssertFalse(m.canCapture)          // too few sats
        m.targetSats = 9; m.distanceM = 10
        XCTAssertFalse(m.canCapture)          // too close
    }

    func testGateMessageDistinguishesFailureModes() {
        let m = goodFix()
        XCTAssertNil(m.gateMessage)
        m.stale = true
        XCTAssertEqual(m.gateMessage, "Tracker fix is stale — wait for a fresh packet.")
        let blank = OffsetCalibrateModel()
        XCTAssertEqual(blank.gateMessage, "No fix from the tracker yet — give it open sky.")
    }

    func testOffsetBands() {
        let m = OffsetCalibrateModel()
        XCTAssertEqual(m.offsetBand(2), .small)
        XCTAssertEqual(m.offsetBand(10), .moderate)
        XCTAssertEqual(m.offsetBand(-25), .large)
    }

    func testTrackerCoordFromBearingDistance() {
        let m = goodFix()       // 80 m due north of the base
        guard let t = m.trackerCoord else { return XCTFail("no tracker coord") }
        XCTAssertEqual(GeoMath.bearingDeg(fromLat: 21.6, fromLon: -158.0, toLat: t.lat, toLon: t.lon), 0, accuracy: 0.5)
        XCTAssertEqual(GeoMath.haversineMeters(fromLat: 21.6, fromLon: -158.0, toLat: t.lat, toLon: t.lon), 80, accuracy: 1.0)
    }
}
