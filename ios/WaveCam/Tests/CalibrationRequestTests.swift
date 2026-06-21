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
        XCTAssertNil(b["pan_enc"])                       // V1: backend captures the live encoder
        XCTAssertEqual(b["method"] as? String, "map_lookat")
    }
    func testMapHeadingPreviewIsNotAccepted() {
        let b = WaveCamClient.mapHeadingBody(targetLat: 21.6461, targetLon: -158.0501, operatorAccepted: false, source: "ios_native")
        XCTAssertEqual(b["operator_accepted"] as? Bool, false)
    }
}
