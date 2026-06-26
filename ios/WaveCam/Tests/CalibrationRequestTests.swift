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
    func testMapLocationBodyCarriesAltitude() {
        let b = WaveCamClient.mapLocationBody(lat: 21.6451, lon: -158.0501, errorRadiusM: 4,
                                              source: "ios_native", altM: 2.0)
        XCTAssertEqual(b["alt_m"] as? Double, 2.0)
    }
    func testOffsetBodyShape() {
        let b = WaveCamClient.offsetCalibrateBody(targetLat: 21.6461, targetLon: -158.0501,
                                                  step3BearingDeg: 180, source: "ios_native")
        XCTAssertEqual(b["method"] == nil, true)         // offset route has no "method" field
        XCTAssertEqual(b["operator_accepted"] as? Bool, true)
        XCTAssertEqual(b["target_lat"] as? Double, 21.6461)
        XCTAssertEqual(b["target_lon"] as? Double, -158.0501)
        XCTAssertEqual(b["step3_bearing_deg"] as? Double, 180)
    }
    func testOffsetBodyOmitsStep3WhenNil() {
        let b = WaveCamClient.offsetCalibrateBody(targetLat: 21.6461, targetLon: -158.0501,
                                                  step3BearingDeg: nil, source: "ios_native")
        XCTAssertNil(b["step3_bearing_deg"])
    }
    func testOffsetBodyOmitsCoordsForLiveFix() {
        let b = WaveCamClient.offsetCalibrateBody(targetLat: nil, targetLon: nil,
                                                  step3BearingDeg: 180, source: "ios_native")
        XCTAssertNil(b["target_lat"])           // backend uses its own live fix
        XCTAssertNil(b["target_lon"])
        XCTAssertEqual(b["operator_accepted"] as? Bool, true)
    }
}
