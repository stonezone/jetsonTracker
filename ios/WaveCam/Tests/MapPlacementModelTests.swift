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
        // 200 m across a 400-pt-wide map => 0.5 m/pt; 8 pt slop => 4 m
        XCTAssertEqual(m.errorRadiusM(metersAcross: 200, screenWidthPoints: 400), 4.0, accuracy: 0.1)
        // Zoomed way out: large radius, not the optimistic 3 m the spec warned against
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
    func testCanConfirmHeadingLookAtNeedsValidDistance() {
        let m = MapPlacementModel()
        m.mode = .headingLookAt; m.tilesLoaded = true
        m.baseLat = 21.6451; m.baseLon = -158.0501
        m.lookAtLat = 21.64512; m.lookAtLon = -158.0501    // too close
        XCTAssertFalse(m.canConfirmHeading)
        m.lookAtLat = 21.6461                               // far enough
        XCTAssertTrue(m.canConfirmHeading)
    }
}
