import XCTest
@testable import WaveCam

final class OffsetCalibrateModelTests: XCTestCase {
    private func goodFix() -> OffsetCalibrateModel {
        let m = OffsetCalibrateModel()
        m.baseLat = 21.6; m.baseLon = -158.0
        m.trackerLat = 21.6007; m.trackerLon = -158.0
        m.sats = 9; m.hdop = 1.2; m.fixAgeSec = 1; m.loraAgeSec = 1
        return m
    }

    func testCanCaptureNeedsQualityFix() {
        let m = goodFix()
        XCTAssertTrue(m.canCapture)
        m.hdop = 8.0
        XCTAssertFalse(m.canCapture)          // HDOP too high
        m.hdop = 1.2; m.sats = 3
        XCTAssertFalse(m.canCapture)          // too few sats
    }

    func testGateMessageDistinguishesFailureModes() {
        let m = goodFix()
        XCTAssertNil(m.gateMessage)
        m.loraAgeSec = nil
        XCTAssertEqual(m.gateMessage, "No packets from the tracker — check the LoRa link.")
        m.loraAgeSec = 30
        XCTAssertEqual(m.gateMessage, "Tracker fix is stale — wait for a fresh packet.")
        m.loraAgeSec = 1; m.trackerLat = nil
        XCTAssertEqual(m.gateMessage, "Waiting for a GPS fix from the tracker…")
    }

    func testOffsetBands() {
        let m = OffsetCalibrateModel()
        XCTAssertEqual(m.offsetBand(2), .small)
        XCTAssertEqual(m.offsetBand(10), .moderate)
        XCTAssertEqual(m.offsetBand(-25), .large)
    }

    func testDistanceAndBearing() {
        let m = goodFix()
        XCTAssertEqual(m.bearingDeg ?? -1, 0, accuracy: 0.5)     // tracker due north
        XCTAssertEqual(m.distanceM ?? 0, 77.8, accuracy: 2.0)    // ~0.0007 deg lat
    }
}
