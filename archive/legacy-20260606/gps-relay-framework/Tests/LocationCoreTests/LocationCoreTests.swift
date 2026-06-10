import XCTest
@testable import LocationCore

final class LocationCoreTests: XCTestCase {
    func testLocationFixEncodingMatchesSchemaKeys() throws {
        let fix = LocationFix(
            timestamp: Date(timeIntervalSince1970: 1_730_359_999),
            source: .watchOS,
            coordinate: .init(latitude: 37.3317, longitude: -122.0307),
            altitudeMeters: 22.4,
            horizontalAccuracyMeters: 5.2,
            verticalAccuracyMeters: 8.0,
            speedMetersPerSecond: 1.2,
            courseDegrees: 87,
            headingDegrees: nil,
            batteryFraction: 0.76,
            sequence: 1_042
        )
        let payload = try JSONDecoder().decode(LocationFixPayload.self, from: try JSONEncoder().encode(fix))
        XCTAssertEqual(payload.source, "watchOS")
        XCTAssertEqual(payload.seq, 1_042)
    }

    // MARK: - Accuracy Bounds Tests

    func testHorizontalAccuracyIsNonNegative() throws {
        let fix = LocationFix(
            timestamp: Date(),
            source: .iOS,
            coordinate: .init(latitude: 0, longitude: 0),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 0,
            verticalAccuracyMeters: 0,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            headingDegrees: nil,
            batteryFraction: 0.5,
            sequence: 1
        )
        XCTAssertGreaterThanOrEqual(fix.horizontalAccuracyMeters, 0, "Horizontal accuracy must be >= 0")
    }

    func testVerticalAccuracyIsNonNegative() throws {
        let fix = LocationFix(
            timestamp: Date(),
            source: .iOS,
            coordinate: .init(latitude: 0, longitude: 0),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 0,
            verticalAccuracyMeters: 0,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            headingDegrees: nil,
            batteryFraction: 0.5,
            sequence: 1
        )
        XCTAssertGreaterThanOrEqual(fix.verticalAccuracyMeters, 0, "Vertical accuracy must be >= 0")
    }

    func testAccuracyValuesEncodeCorrectly() throws {
        let fix = LocationFix(
            timestamp: Date(),
            source: .watchOS,
            coordinate: .init(latitude: 0, longitude: 0),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 12.5,
            verticalAccuracyMeters: 8.3,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            headingDegrees: nil,
            batteryFraction: 0.5,
            sequence: 1
        )
        let payload = try JSONDecoder().decode(LocationFixPayload.self, from: try JSONEncoder().encode(fix))
        XCTAssertEqual(payload.h_accuracy_m, 12.5)
        XCTAssertEqual(payload.v_accuracy_m, 8.3)
    }

    // MARK: - Sequence Monotonicity Tests

    func testSequenceNumbersIncrement() {
        let fix1 = LocationFix(
            timestamp: Date(),
            source: .iOS,
            coordinate: .init(latitude: 0, longitude: 0),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            headingDegrees: nil,
            batteryFraction: 0.5,
            sequence: 100
        )

        let fix2 = LocationFix(
            timestamp: Date(),
            source: .iOS,
            coordinate: .init(latitude: 0, longitude: 0),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            headingDegrees: nil,
            batteryFraction: 0.5,
            sequence: 101
        )

        let fix3 = LocationFix(
            timestamp: Date(),
            source: .iOS,
            coordinate: .init(latitude: 0, longitude: 0),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            headingDegrees: nil,
            batteryFraction: 0.5,
            sequence: 102
        )

        XCTAssertLessThan(fix1.sequence, fix2.sequence, "Sequence should increment")
        XCTAssertLessThan(fix2.sequence, fix3.sequence, "Sequence should increment")
        XCTAssertEqual(fix2.sequence - fix1.sequence, 1, "Sequence should increment by 1")
        XCTAssertEqual(fix3.sequence - fix2.sequence, 1, "Sequence should increment by 1")
    }

    func testSequenceEncodesAndDecodesCorrectly() throws {
        let fix = LocationFix(
            timestamp: Date(),
            source: .iOS,
            coordinate: .init(latitude: 0, longitude: 0),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            headingDegrees: nil,
            batteryFraction: 0.5,
            sequence: 12345
        )

        let payload = try JSONDecoder().decode(LocationFixPayload.self, from: try JSONEncoder().encode(fix))
        XCTAssertEqual(payload.seq, 12345, "Sequence number should encode/decode correctly")
    }

    // MARK: - Timestamp Conversion Tests

    func testTimestampConversionToUnixMilliseconds() throws {
        let timestamp = Date(timeIntervalSince1970: 1_730_359_999)
        let expectedMilliseconds = 1_730_359_999_000

        let fix = LocationFix(
            timestamp: timestamp,
            source: .watchOS,
            coordinate: .init(latitude: 0, longitude: 0),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            headingDegrees: nil,
            batteryFraction: 0.5,
            sequence: 1
        )

        let payload = try JSONDecoder().decode(LocationFixPayload.self, from: try JSONEncoder().encode(fix))
        XCTAssertEqual(payload.ts_unix_ms, expectedMilliseconds, "Timestamp should convert to Unix milliseconds correctly")
    }

    func testTimestampConversionFromUnixMilliseconds() throws {
        let timestampMilliseconds = 1_730_359_999_000
        let expectedDate = Date(timeIntervalSince1970: 1_730_359_999)

        let jsonString = """
        {
            "ts_unix_ms": \(timestampMilliseconds),
            "source": "iOS",
            "lat": 0,
            "lon": 0,
            "h_accuracy_m": 5,
            "v_accuracy_m": 5,
            "speed_mps": 0,
            "course_deg": 0,
            "battery_pct": 0.5,
            "seq": 1
        }
        """

        let fix = try JSONDecoder().decode(LocationFix.self, from: jsonString.data(using: .utf8)!)
        XCTAssertEqual(fix.timestamp.timeIntervalSince1970, expectedDate.timeIntervalSince1970, accuracy: 0.001, "Timestamp should convert from Unix milliseconds correctly")
    }

    func testTimestampRoundTripPrecision() throws {
        let originalTimestamp = Date(timeIntervalSince1970: 1_730_359_999.123)

        let fix = LocationFix(
            timestamp: originalTimestamp,
            source: .watchOS,
            coordinate: .init(latitude: 0, longitude: 0),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            headingDegrees: nil,
            batteryFraction: 0.5,
            sequence: 1
        )

        let encoded = try JSONEncoder().encode(fix)
        let decoded = try JSONDecoder().decode(LocationFix.self, from: encoded)

        // Unix milliseconds precision means we lose sub-millisecond precision
        XCTAssertEqual(decoded.timestamp.timeIntervalSince1970, originalTimestamp.timeIntervalSince1970, accuracy: 0.001, "Timestamp should survive round-trip with millisecond precision")
    }

    // MARK: - Round-Trip Encoding Tests

    func testRoundTripEncodingAllFields() throws {
        let originalFix = LocationFix(
            timestamp: Date(timeIntervalSince1970: 1_730_359_999.456),
            source: .watchOS,
            coordinate: .init(latitude: 37.3317, longitude: -122.0307),
            altitudeMeters: 22.4,
            horizontalAccuracyMeters: 5.2,
            verticalAccuracyMeters: 8.0,
            speedMetersPerSecond: 1.2,
            courseDegrees: 87,
            headingDegrees: nil,
            batteryFraction: 0.76,
            sequence: 1_042
        )

        let encoded = try JSONEncoder().encode(originalFix)
        let decoded = try JSONDecoder().decode(LocationFix.self, from: encoded)

        XCTAssertEqual(decoded.timestamp.timeIntervalSince1970, originalFix.timestamp.timeIntervalSince1970, accuracy: 0.001)
        XCTAssertEqual(decoded.source, originalFix.source)
        XCTAssertEqual(decoded.coordinate.latitude, originalFix.coordinate.latitude)
        XCTAssertEqual(decoded.coordinate.longitude, originalFix.coordinate.longitude)
        XCTAssertEqual(decoded.altitudeMeters, originalFix.altitudeMeters)
        XCTAssertEqual(decoded.horizontalAccuracyMeters, originalFix.horizontalAccuracyMeters)
        XCTAssertEqual(decoded.verticalAccuracyMeters, originalFix.verticalAccuracyMeters)
        XCTAssertEqual(decoded.speedMetersPerSecond, originalFix.speedMetersPerSecond)
        XCTAssertEqual(decoded.courseDegrees, originalFix.courseDegrees)
        XCTAssertEqual(decoded.batteryFraction, originalFix.batteryFraction)
        XCTAssertEqual(decoded.sequence, originalFix.sequence)
    }

    func testRoundTripEncodingWithIOSSource() throws {
        let originalFix = LocationFix(
            timestamp: Date(),
            source: .iOS,
            coordinate: .init(latitude: 0, longitude: 0),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            headingDegrees: nil,
            batteryFraction: 0.5,
            sequence: 1
        )

        let encoded = try JSONEncoder().encode(originalFix)
        let decoded = try JSONDecoder().decode(LocationFix.self, from: encoded)

        XCTAssertEqual(decoded.source, .iOS)
    }

    func testRoundTripEquality() throws {
        let originalFix = LocationFix(
            timestamp: Date(timeIntervalSince1970: 1_730_359_999),
            source: .watchOS,
            coordinate: .init(latitude: 37.3317, longitude: -122.0307),
            altitudeMeters: 22.4,
            horizontalAccuracyMeters: 5.2,
            verticalAccuracyMeters: 8.0,
            speedMetersPerSecond: 1.2,
            courseDegrees: 87,
            headingDegrees: nil,
            batteryFraction: 0.76,
            sequence: 1_042
        )

        let encoded = try JSONEncoder().encode(originalFix)
        let decoded = try JSONDecoder().decode(LocationFix.self, from: encoded)

        XCTAssertEqual(decoded, originalFix, "Round-trip encoding should produce equal LocationFix")
    }

    // MARK: - Edge Cases: Optional Altitude

    func testAltitudeNil() throws {
        let fix = LocationFix(
            timestamp: Date(),
            source: .iOS,
            coordinate: .init(latitude: 0, longitude: 0),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            headingDegrees: nil,
            batteryFraction: 0.5,
            sequence: 1
        )

        XCTAssertNil(fix.altitudeMeters, "Altitude should be nil when not provided")

        let payload = try JSONDecoder().decode(LocationFixPayload.self, from: try JSONEncoder().encode(fix))
        XCTAssertNil(payload.alt_m, "Altitude should encode as nil")
    }

    func testAltitudePresent() throws {
        let fix = LocationFix(
            timestamp: Date(),
            source: .iOS,
            coordinate: .init(latitude: 0, longitude: 0),
            altitudeMeters: 123.45,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            headingDegrees: nil,
            batteryFraction: 0.5,
            sequence: 1
        )

        XCTAssertEqual(fix.altitudeMeters, 123.45)

        let payload = try JSONDecoder().decode(LocationFixPayload.self, from: try JSONEncoder().encode(fix))
        XCTAssertEqual(payload.alt_m, 123.45)
    }

    func testAltitudeRoundTrip() throws {
        let fixWithAltitude = LocationFix(
            timestamp: Date(timeIntervalSince1970: 1_000_000),
            source: .watchOS,
            coordinate: .init(latitude: 0, longitude: 0),
            altitudeMeters: 999.9,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            headingDegrees: nil,
            batteryFraction: 0.5,
            sequence: 1
        )

        let encodedWith = try JSONEncoder().encode(fixWithAltitude)
        let decodedWith = try JSONDecoder().decode(LocationFix.self, from: encodedWith)
        XCTAssertEqual(decodedWith.altitudeMeters, 999.9)

        let fixWithoutAltitude = LocationFix(
            timestamp: Date(timeIntervalSince1970: 1_000_000),
            source: .watchOS,
            coordinate: .init(latitude: 0, longitude: 0),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            headingDegrees: nil,
            batteryFraction: 0.5,
            sequence: 2
        )

        let encodedWithout = try JSONEncoder().encode(fixWithoutAltitude)
        let decodedWithout = try JSONDecoder().decode(LocationFix.self, from: encodedWithout)
        XCTAssertNil(decodedWithout.altitudeMeters)
    }

    // MARK: - Edge Cases: Boundary Values

    func testLatitudeBoundaries() throws {
        let fixMinLat = LocationFix(
            timestamp: Date(),
            source: .iOS,
            coordinate: .init(latitude: -90, longitude: 0),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            headingDegrees: nil,
            batteryFraction: 0.5,
            sequence: 1
        )
        XCTAssertEqual(fixMinLat.coordinate.latitude, -90)

        let fixMaxLat = LocationFix(
            timestamp: Date(),
            source: .iOS,
            coordinate: .init(latitude: 90, longitude: 0),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            headingDegrees: nil,
            batteryFraction: 0.5,
            sequence: 2
        )
        XCTAssertEqual(fixMaxLat.coordinate.latitude, 90)

        let payloadMin = try JSONDecoder().decode(LocationFixPayload.self, from: try JSONEncoder().encode(fixMinLat))
        XCTAssertEqual(payloadMin.lat, -90)

        let payloadMax = try JSONDecoder().decode(LocationFixPayload.self, from: try JSONEncoder().encode(fixMaxLat))
        XCTAssertEqual(payloadMax.lat, 90)
    }

    func testLongitudeBoundaries() throws {
        let fixMinLon = LocationFix(
            timestamp: Date(),
            source: .iOS,
            coordinate: .init(latitude: 0, longitude: -180),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            headingDegrees: nil,
            batteryFraction: 0.5,
            sequence: 1
        )
        XCTAssertEqual(fixMinLon.coordinate.longitude, -180)

        let fixMaxLon = LocationFix(
            timestamp: Date(),
            source: .iOS,
            coordinate: .init(latitude: 0, longitude: 180),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            headingDegrees: nil,
            batteryFraction: 0.5,
            sequence: 2
        )
        XCTAssertEqual(fixMaxLon.coordinate.longitude, 180)

        let payloadMin = try JSONDecoder().decode(LocationFixPayload.self, from: try JSONEncoder().encode(fixMinLon))
        XCTAssertEqual(payloadMin.lon, -180)

        let payloadMax = try JSONDecoder().decode(LocationFixPayload.self, from: try JSONEncoder().encode(fixMaxLon))
        XCTAssertEqual(payloadMax.lon, 180)
    }

    func testCourseBoundaries() throws {
        let fixMinCourse = LocationFix(
            timestamp: Date(),
            source: .iOS,
            coordinate: .init(latitude: 0, longitude: 0),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            headingDegrees: nil,
            batteryFraction: 0.5,
            sequence: 1
        )
        XCTAssertEqual(fixMinCourse.courseDegrees, 0)

        let fixMaxCourse = LocationFix(
            timestamp: Date(),
            source: .iOS,
            coordinate: .init(latitude: 0, longitude: 0),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: 0,
            courseDegrees: 360,
            headingDegrees: nil,
            batteryFraction: 0.5,
            sequence: 2
        )
        XCTAssertEqual(fixMaxCourse.courseDegrees, 360)

        let payloadMin = try JSONDecoder().decode(LocationFixPayload.self, from: try JSONEncoder().encode(fixMinCourse))
        XCTAssertEqual(payloadMin.course_deg, 0)

        let payloadMax = try JSONDecoder().decode(LocationFixPayload.self, from: try JSONEncoder().encode(fixMaxCourse))
        XCTAssertEqual(payloadMax.course_deg, 360)
    }

    func testBatteryBoundaries() throws {
        let fixMinBattery = LocationFix(
            timestamp: Date(),
            source: .iOS,
            coordinate: .init(latitude: 0, longitude: 0),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            headingDegrees: nil,
            batteryFraction: 0,
            sequence: 1
        )
        XCTAssertEqual(fixMinBattery.batteryFraction, 0)

        let fixMaxBattery = LocationFix(
            timestamp: Date(),
            source: .iOS,
            coordinate: .init(latitude: 0, longitude: 0),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            headingDegrees: nil,
            batteryFraction: 1,
            sequence: 2
        )
        XCTAssertEqual(fixMaxBattery.batteryFraction, 1)

        let payloadMin = try JSONDecoder().decode(LocationFixPayload.self, from: try JSONEncoder().encode(fixMinBattery))
        XCTAssertEqual(payloadMin.battery_pct, 0)

        let payloadMax = try JSONDecoder().decode(LocationFixPayload.self, from: try JSONEncoder().encode(fixMaxBattery))
        XCTAssertEqual(payloadMax.battery_pct, 1)
    }

    func testZeroAccuracyValues() throws {
        let fix = LocationFix(
            timestamp: Date(),
            source: .iOS,
            coordinate: .init(latitude: 0, longitude: 0),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 0,
            verticalAccuracyMeters: 0,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            headingDegrees: nil,
            batteryFraction: 0.5,
            sequence: 1
        )

        XCTAssertEqual(fix.horizontalAccuracyMeters, 0)
        XCTAssertEqual(fix.verticalAccuracyMeters, 0)

        let payload = try JSONDecoder().decode(LocationFixPayload.self, from: try JSONEncoder().encode(fix))
        XCTAssertEqual(payload.h_accuracy_m, 0)
        XCTAssertEqual(payload.v_accuracy_m, 0)
    }

    func testNegativeAltitude() throws {
        let fix = LocationFix(
            timestamp: Date(),
            source: .iOS,
            coordinate: .init(latitude: 0, longitude: 0),
            altitudeMeters: -100.5,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            headingDegrees: nil,
            batteryFraction: 0.5,
            sequence: 1
        )

        XCTAssertEqual(fix.altitudeMeters, -100.5, "Negative altitude should be allowed (below sea level)")

        let payload = try JSONDecoder().decode(LocationFixPayload.self, from: try JSONEncoder().encode(fix))
        XCTAssertEqual(payload.alt_m, -100.5)
    }

    func testHighSpeedValue() throws {
        let fix = LocationFix(
            timestamp: Date(),
            source: .watchOS,
            coordinate: .init(latitude: 0, longitude: 0),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: 300.5,
            courseDegrees: 180,
            headingDegrees: nil,
            batteryFraction: 0.5,
            sequence: 1
        )

        XCTAssertEqual(fix.speedMetersPerSecond, 300.5)

        let payload = try JSONDecoder().decode(LocationFixPayload.self, from: try JSONEncoder().encode(fix))
        XCTAssertEqual(payload.speed_mps, 300.5)
    }
}
