import XCTest
@testable import WaveCam

final class SavedSpotsStoreTests: XCTestCase {
    private func freshDefaults() -> UserDefaults {
        let d = UserDefaults(suiteName: "wavecam-test-spots")!
        d.removePersistentDomain(forName: "wavecam-test-spots")
        return d
    }

    func testRoundTripPersistsSpot() {
        let d = freshDefaults()
        let s = SavedSpotsStore(defaults: d)
        s.add(SavedSpot(name: "Mokuleia", lat: 21.6808, lon: -158.0364, baseHeightM: 2, lastHeadingDeg: 190.8))
        let reloaded = SavedSpotsStore(defaults: d)        // new instance reads persisted JSON
        XCTAssertEqual(reloaded.spots.count, 1)
        XCTAssertEqual(reloaded.spots.first?.name, "Mokuleia")
        XCTAssertEqual(reloaded.spots.first?.baseHeightM ?? 0, 2, accuracy: 1e-6)
        XCTAssertEqual(reloaded.spots.first?.lastHeadingDeg ?? 0, 190.8, accuracy: 1e-6)
    }

    func testUpdateAndRemove() {
        let d = freshDefaults()
        let s = SavedSpotsStore(defaults: d)
        var spot = SavedSpot(name: "A", lat: 1, lon: 2, baseHeightM: 2, lastHeadingDeg: nil)
        s.add(spot)
        spot.name = "A2"; spot.lastHeadingDeg = 100
        s.update(spot)
        XCTAssertEqual(SavedSpotsStore(defaults: d).spots.first?.name, "A2")
        XCTAssertEqual(SavedSpotsStore(defaults: d).spots.first?.lastHeadingDeg ?? 0, 100, accuracy: 1e-6)
        s.remove(spot)
        XCTAssertTrue(SavedSpotsStore(defaults: d).spots.isEmpty)
    }
}
