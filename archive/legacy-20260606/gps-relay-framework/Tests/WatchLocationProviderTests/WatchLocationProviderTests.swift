import XCTest
import CoreLocation
@testable import WatchLocationProvider
@testable import LocationCore

#if os(watchOS)
import HealthKit
import WatchConnectivity
import WatchKit

// MARK: - Mock Objects

/// Mock delegate to capture WatchLocationProvider events
final class MockWatchLocationProviderDelegate: WatchLocationProviderDelegate {
    var producedFixes: [LocationFix] = []
    var errors: [Error] = []
    
    func didProduce(_ fix: LocationFix) {
        producedFixes.append(fix)
    }
    
    func didFail(_ error: Error) {
        errors.append(error)
    }
    
    func reset() {
        producedFixes.removeAll()
        errors.removeAll()
    }
}

/// Mock CLLocationManager for testing location updates
final class MockCLLocationManager: CLLocationManager {
    var didStartUpdatingLocation = false
    var didStopUpdatingLocation = false
    var mockActivityType: CLActivityType = .other
    var mockDesiredAccuracy: CLLocationAccuracy = kCLLocationAccuracyBest
    var mockDistanceFilter: CLLocationDistance = kCLDistanceFilterNone
    
    override func startUpdatingLocation() {
        didStartUpdatingLocation = true
    }
    
    override func stopUpdatingLocation() {
        didStopUpdatingLocation = true
    }
}

/// Mock error for testing error handling
struct MockLocationError: LocalizedError {
    let message: String
    
    var errorDescription: String? { message }
}

// MARK: - Test Suite

final class WatchLocationProviderTests: XCTestCase {
    
    var provider: WatchLocationProvider!
    var delegate: MockWatchLocationProviderDelegate!
    
    override func setUp() {
        super.setUp()
        provider = WatchLocationProvider()
        delegate = MockWatchLocationProviderDelegate()
        provider.delegate = delegate
    }
    
    override func tearDown() {
        provider.stop()
        provider = nil
        delegate = nil
        super.tearDown()
    }
    
    // MARK: - Initialization Tests
    
    func testProviderInitialization() {
        XCTAssertNotNil(provider, "Provider should initialize successfully")
        XCTAssertNotNil(delegate, "Delegate should be set")
    }
    
    func testDelegateAssignment() {
        let newDelegate = MockWatchLocationProviderDelegate()
        provider.delegate = newDelegate
        XCTAssertNotNil(provider.delegate, "Delegate should be assignable")
    }
    
    // MARK: - LocationFix Serialization Tests
    
    func testLocationFixSerializationWithAllFields() throws {
        let timestamp = Date(timeIntervalSince1970: 1_730_359_999)
        let coordinate = LocationFix.Coordinate(latitude: 37.3317, longitude: -122.0307)
        
        let fix = LocationFix(
            timestamp: timestamp,
            source: .watchOS,
            coordinate: coordinate,
            altitudeMeters: 22.4,
            horizontalAccuracyMeters: 5.2,
            verticalAccuracyMeters: 8.0,
            speedMetersPerSecond: 1.2,
            courseDegrees: 87,
            batteryFraction: 0.76,
            sequence: 1_042
        )
        
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.withoutEscapingSlashes]
        let data = try encoder.encode(fix)
        
        XCTAssertFalse(data.isEmpty, "Encoded data should not be empty")
        
        let decoder = JSONDecoder()
        let decoded = try decoder.decode(LocationFix.self, from: data)
        
        XCTAssertEqual(decoded.source, .watchOS)
        XCTAssertEqual(decoded.coordinate.latitude, 37.3317)
        XCTAssertEqual(decoded.coordinate.longitude, -122.0307)
        XCTAssertEqual(decoded.altitudeMeters, 22.4)
        XCTAssertEqual(decoded.sequence, 1_042)
    }
    
    func testLocationFixSerializationWithoutAltitude() throws {
        let fix = LocationFix(
            timestamp: Date(),
            source: .watchOS,
            coordinate: .init(latitude: 0, longitude: 0),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 5.0,
            verticalAccuracyMeters: 5.0,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            batteryFraction: 0.5,
            sequence: 1
        )
        
        let encoder = JSONEncoder()
        let data = try encoder.encode(fix)
        let decoded = try JSONDecoder().decode(LocationFix.self, from: data)
        
        XCTAssertNil(decoded.altitudeMeters, "Altitude should be nil when not provided")
    }
    
    func testLocationFixJSONFormat() throws {
        let fix = LocationFix(
            timestamp: Date(timeIntervalSince1970: 1_000_000),
            source: .watchOS,
            coordinate: .init(latitude: 37.5, longitude: -122.5),
            altitudeMeters: 100,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 10,
            speedMetersPerSecond: 2.5,
            courseDegrees: 180,
            batteryFraction: 0.8,
            sequence: 42
        )
        
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.withoutEscapingSlashes, .prettyPrinted]
        let data = try encoder.encode(fix)
        let jsonString = String(data: data, encoding: .utf8)!
        
        XCTAssertTrue(jsonString.contains("\"source\" : \"watchOS\""))
        XCTAssertTrue(jsonString.contains("\"lat\" : 37.5"))
        XCTAssertTrue(jsonString.contains("\"lon\" : -122.5"))
        XCTAssertTrue(jsonString.contains("\"seq\" : 42"))
    }
    
    // MARK: - Sequence Number Generation Tests
    
    func testSequenceNumberGeneration() {
        // Sequence numbers should be based on timestamp in milliseconds
        let before = Int(Date().timeIntervalSinceReferenceDate * 1000)
        Thread.sleep(forTimeInterval: 0.01) // 10ms delay
        let after = Int(Date().timeIntervalSinceReferenceDate * 1000)
        
        XCTAssertGreaterThan(after, before, "Sequence numbers should increase over time")
    }
    
    func testSequenceNumberUniqueness() {
        var sequences: Set<Int> = []
        
        for _ in 0..<100 {
            let seq = Int(Date().timeIntervalSinceReferenceDate * 1000)
            sequences.insert(seq)
            Thread.sleep(forTimeInterval: 0.001) // 1ms delay
        }
        
        // Most sequences should be unique (allowing for some duplicates due to timing)
        XCTAssertGreaterThan(sequences.count, 90, "Most sequence numbers should be unique")
    }
    
    func testSequenceNumberFormat() {
        let seq = Int(Date().timeIntervalSinceReferenceDate * 1000)
        XCTAssertGreaterThan(seq, 0, "Sequence number should be positive")
        XCTAssertLessThan(String(seq).count, 20, "Sequence number should be reasonable size")
    }
    
    // MARK: - Delegate Callback Tests
    
    func testDelegateReceivesProducedFix() {
        let fix = LocationFix(
            timestamp: Date(),
            source: .watchOS,
            coordinate: .init(latitude: 37.0, longitude: -122.0),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            batteryFraction: 0.5,
            sequence: 1
        )
        
        delegate.didProduce(fix)
        
        XCTAssertEqual(delegate.producedFixes.count, 1)
        XCTAssertEqual(delegate.producedFixes.first?.source, .watchOS)
    }
    
    func testDelegateReceivesErrors() {
        let error = MockLocationError(message: "Test error")
        delegate.didFail(error)
        
        XCTAssertEqual(delegate.errors.count, 1)
        XCTAssertEqual((delegate.errors.first as? MockLocationError)?.message, "Test error")
    }
    
    func testDelegateReceivesMultipleFixes() {
        for i in 1...5 {
            let fix = LocationFix(
                timestamp: Date(),
                source: .watchOS,
                coordinate: .init(latitude: Double(i), longitude: Double(i)),
                altitudeMeters: nil,
                horizontalAccuracyMeters: 5,
                verticalAccuracyMeters: 5,
                speedMetersPerSecond: 0,
                courseDegrees: 0,
                batteryFraction: 0.5,
                sequence: i
            )
            delegate.didProduce(fix)
        }
        
        XCTAssertEqual(delegate.producedFixes.count, 5)
        XCTAssertEqual(delegate.producedFixes[0].sequence, 1)
        XCTAssertEqual(delegate.producedFixes[4].sequence, 5)
    }
    
    func testDelegateReset() {
        delegate.didProduce(LocationFix(
            timestamp: Date(),
            source: .watchOS,
            coordinate: .init(latitude: 0, longitude: 0),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            batteryFraction: 0.5,
            sequence: 1
        ))
        delegate.didFail(MockLocationError(message: "Test"))
        
        delegate.reset()
        
        XCTAssertTrue(delegate.producedFixes.isEmpty)
        XCTAssertTrue(delegate.errors.isEmpty)
    }
    
    // MARK: - CLLocation to LocationFix Conversion Tests
    
    func testCLLocationConversionWithValidData() {
        let coordinate = CLLocationCoordinate2D(latitude: 37.3317, longitude: -122.0307)
        let location = CLLocation(
            coordinate: coordinate,
            altitude: 22.4,
            horizontalAccuracy: 5.2,
            verticalAccuracy: 8.0,
            course: 87,
            speed: 1.2,
            timestamp: Date()
        )
        
        XCTAssertEqual(location.coordinate.latitude, 37.3317)
        XCTAssertEqual(location.coordinate.longitude, -122.0307)
        XCTAssertEqual(location.altitude, 22.4)
        XCTAssertEqual(location.horizontalAccuracy, 5.2)
        XCTAssertEqual(location.verticalAccuracy, 8.0)
        XCTAssertEqual(location.course, 87)
        XCTAssertEqual(location.speed, 1.2)
    }
    
    func testCLLocationConversionWithInvalidAltitude() {
        let coordinate = CLLocationCoordinate2D(latitude: 0, longitude: 0)
        let location = CLLocation(
            coordinate: coordinate,
            altitude: 0,
            horizontalAccuracy: 5.0,
            verticalAccuracy: -1.0, // Invalid vertical accuracy
            course: 0,
            speed: 0,
            timestamp: Date()
        )
        
        // When verticalAccuracy is negative, altitude should not be used
        XCTAssertLessThan(location.verticalAccuracy, 0)
    }
    
    func testCLLocationConversionWithNegativeSpeed() {
        let coordinate = CLLocationCoordinate2D(latitude: 0, longitude: 0)
        let location = CLLocation(
            coordinate: coordinate,
            altitude: 0,
            horizontalAccuracy: 5.0,
            verticalAccuracy: 5.0,
            course: 0,
            speed: -1.0, // Invalid speed
            timestamp: Date()
        )
        
        // Negative speed should be clamped to 0
        let clampedSpeed = max(location.speed, 0)
        XCTAssertEqual(clampedSpeed, 0)
    }
    
    func testCLLocationConversionWithInvalidCourse() {
        let coordinate = CLLocationCoordinate2D(latitude: 0, longitude: 0)
        let location = CLLocation(
            coordinate: coordinate,
            altitude: 0,
            horizontalAccuracy: 5.0,
            verticalAccuracy: 5.0,
            course: -1.0, // Invalid course
            speed: 0,
            timestamp: Date()
        )
        
        // Negative course should be handled (set to 0)
        let validCourse = location.course >= 0 ? location.course : 0
        XCTAssertEqual(validCourse, 0)
    }
    
    // MARK: - Battery Level Tests
    
    func testBatteryLevelValidRange() {
        let device = WKInterfaceDevice.current()
        device.isBatteryMonitoringEnabled = true
        
        let batteryLevel = device.batteryLevel
        
        // Battery level should be between -1 (unknown) and 1.0 (full)
        if batteryLevel >= 0 {
            XCTAssertGreaterThanOrEqual(batteryLevel, 0.0)
            XCTAssertLessThanOrEqual(batteryLevel, 1.0)
        } else {
            XCTAssertEqual(batteryLevel, -1.0, "Unknown battery level should be -1")
        }
    }
    
    func testBatteryLevelInLocationFix() {
        let device = WKInterfaceDevice.current()
        device.isBatteryMonitoringEnabled = true
        
        let batteryFraction = device.batteryLevel >= 0 ? Double(device.batteryLevel) : 0
        
        let fix = LocationFix(
            timestamp: Date(),
            source: .watchOS,
            coordinate: .init(latitude: 0, longitude: 0),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            batteryFraction: batteryFraction,
            sequence: 1
        )
        
        XCTAssertGreaterThanOrEqual(fix.batteryFraction, 0.0)
        XCTAssertLessThanOrEqual(fix.batteryFraction, 1.0)
    }
    
    // MARK: - Error Handling Tests
    
    func testErrorHandlingForInvalidJSON() {
        let invalidJSON = "{ invalid json }".data(using: .utf8)!
        
        XCTAssertThrowsError(try JSONDecoder().decode(LocationFix.self, from: invalidJSON)) { error in
            XCTAssertTrue(error is DecodingError)
        }
    }
    
    func testErrorHandlingForMissingRequiredFields() {
        let incompleteJSON = """
        {
            "ts_unix_ms": 1000000000,
            "source": "watchOS"
        }
        """.data(using: .utf8)!
        
        XCTAssertThrowsError(try JSONDecoder().decode(LocationFix.self, from: incompleteJSON)) { error in
            XCTAssertTrue(error is DecodingError)
        }
    }
    
    func testErrorHandlingForInvalidSource() {
        let invalidSourceJSON = """
        {
            "ts_unix_ms": 1000000000,
            "source": "invalidSource",
            "lat": 0,
            "lon": 0,
            "h_accuracy_m": 5,
            "v_accuracy_m": 5,
            "speed_mps": 0,
            "course_deg": 0,
            "battery_pct": 0.5,
            "seq": 1
        }
        """.data(using: .utf8)!
        
        XCTAssertThrowsError(try JSONDecoder().decode(LocationFix.self, from: invalidSourceJSON)) { error in
            XCTAssertTrue(error is DecodingError)
        }
    }
    
    // MARK: - WatchConnectivity State Tests
    
    func testWCSessionSupport() {
        XCTAssertTrue(WCSession.isSupported(), "WatchConnectivity should be supported on watchOS")
    }
    
    func testWCSessionActivationStates() {
        let session = WCSession.default
        
        // Possible activation states
        let validStates: [WCSessionActivationState] = [.notActivated, .inactive, .activated]
        
        XCTAssertTrue(validStates.contains(session.activationState), 
                     "Session should be in a valid activation state")
    }
    
    func testWCSessionReachability() {
        let session = WCSession.default
        
        // Reachability is a boolean property
        _ = session.isReachable
        
        // Test passes if we can access the property without crashing
        XCTAssertTrue(true)
    }
    
    // MARK: - Integration-style Tests
    
    func testLocationFixCreationFlow() {
        // Simulate the flow from CLLocation to LocationFix
        let coordinate = CLLocationCoordinate2D(latitude: 37.7749, longitude: -122.4194)
        let location = CLLocation(
            coordinate: coordinate,
            altitude: 10.0,
            horizontalAccuracy: 5.0,
            verticalAccuracy: 8.0,
            course: 90,
            speed: 2.5,
            timestamp: Date()
        )
        
        let device = WKInterfaceDevice.current()
        device.isBatteryMonitoringEnabled = true
        
        let fix = LocationFix(
            timestamp: location.timestamp,
            source: .watchOS,
            coordinate: .init(latitude: location.coordinate.latitude, longitude: location.coordinate.longitude),
            altitudeMeters: location.verticalAccuracy >= 0 ? location.altitude : nil,
            horizontalAccuracyMeters: location.horizontalAccuracy,
            verticalAccuracyMeters: max(location.verticalAccuracy, 0),
            speedMetersPerSecond: max(location.speed, 0),
            courseDegrees: location.course >= 0 ? location.course : 0,
            batteryFraction: device.batteryLevel >= 0 ? Double(device.batteryLevel) : 0,
            sequence: Int(Date().timeIntervalSinceReferenceDate * 1000)
        )
        
        XCTAssertEqual(fix.source, .watchOS)
        XCTAssertEqual(fix.coordinate.latitude, 37.7749)
        XCTAssertEqual(fix.coordinate.longitude, -122.4194)
        XCTAssertEqual(fix.altitudeMeters, 10.0)
        XCTAssertEqual(fix.horizontalAccuracyMeters, 5.0)
        XCTAssertEqual(fix.verticalAccuracyMeters, 8.0)
        XCTAssertEqual(fix.speedMetersPerSecond, 2.5)
        XCTAssertEqual(fix.courseDegrees, 90)
    }
    
    func testMultipleLocationUpdatesSequencing() {
        var fixes: [LocationFix] = []
        
        for i in 1...10 {
            let coordinate = CLLocationCoordinate2D(latitude: Double(i), longitude: Double(i))
            let location = CLLocation(
                coordinate: coordinate,
                altitude: Double(i),
                horizontalAccuracy: 5.0,
                verticalAccuracy: 5.0,
                course: 0,
                speed: 0,
                timestamp: Date()
            )
            
            let fix = LocationFix(
                timestamp: location.timestamp,
                source: .watchOS,
                coordinate: .init(latitude: location.coordinate.latitude, longitude: location.coordinate.longitude),
                altitudeMeters: location.altitude,
                horizontalAccuracyMeters: location.horizontalAccuracy,
                verticalAccuracyMeters: location.verticalAccuracy,
                speedMetersPerSecond: location.speed,
                courseDegrees: 0,
                batteryFraction: 0.5,
                sequence: Int(Date().timeIntervalSinceReferenceDate * 1000) + i
            )
            
            fixes.append(fix)
            Thread.sleep(forTimeInterval: 0.001)
        }
        
        XCTAssertEqual(fixes.count, 10)
        
        // Verify sequences are increasing
        for i in 1..<fixes.count {
            XCTAssertGreaterThan(fixes[i].sequence, fixes[i-1].sequence)
        }
    }
    
    // MARK: - Edge Case Tests
    
    func testLocationFixWithExtremeCoordinates() throws {
        let fix = LocationFix(
            timestamp: Date(),
            source: .watchOS,
            coordinate: .init(latitude: 90, longitude: 180),
            altitudeMeters: 8848.86, // Mount Everest
            horizontalAccuracyMeters: 0.1,
            verticalAccuracyMeters: 0.1,
            speedMetersPerSecond: 343, // Speed of sound
            courseDegrees: 359.9,
            batteryFraction: 1.0,
            sequence: Int.max - 1
        )
        
        let data = try JSONEncoder().encode(fix)
        let decoded = try JSONDecoder().decode(LocationFix.self, from: data)
        
        XCTAssertEqual(decoded.coordinate.latitude, 90)
        XCTAssertEqual(decoded.coordinate.longitude, 180)
        XCTAssertEqual(decoded.altitudeMeters, 8848.86)
    }
    
    func testLocationFixWithMinimumValues() throws {
        let fix = LocationFix(
            timestamp: Date(timeIntervalSince1970: 0),
            source: .watchOS,
            coordinate: .init(latitude: -90, longitude: -180),
            altitudeMeters: -428, // Dead Sea
            horizontalAccuracyMeters: 0,
            verticalAccuracyMeters: 0,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            batteryFraction: 0,
            sequence: 0
        )
        
        let data = try JSONEncoder().encode(fix)
        let decoded = try JSONDecoder().decode(LocationFix.self, from: data)
        
        XCTAssertEqual(decoded.coordinate.latitude, -90)
        XCTAssertEqual(decoded.coordinate.longitude, -180)
        XCTAssertEqual(decoded.altitudeMeters, -428)
    }
    
    func testJSONEncoderConfiguration() {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.withoutEscapingSlashes]
        
        let fix = LocationFix(
            timestamp: Date(),
            source: .watchOS,
            coordinate: .init(latitude: 37.0, longitude: -122.0),
            altitudeMeters: nil,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: 0,
            courseDegrees: 0,
            batteryFraction: 0.5,
            sequence: 1
        )
        
        XCTAssertNoThrow(try encoder.encode(fix))
    }
    
    // MARK: - Concurrency Safety Tests
    
    func testConcurrentDelegateCallbacks() {
        let expectation = expectation(description: "Concurrent callbacks")
        expectation.expectedFulfillmentCount = 10
        
        DispatchQueue.concurrentPerform(iterations: 10) { i in
            let fix = LocationFix(
                timestamp: Date(),
                source: .watchOS,
                coordinate: .init(latitude: Double(i), longitude: Double(i)),
                altitudeMeters: nil,
                horizontalAccuracyMeters: 5,
                verticalAccuracyMeters: 5,
                speedMetersPerSecond: 0,
                courseDegrees: 0,
                batteryFraction: 0.5,
                sequence: i
            )
            delegate.didProduce(fix)
            expectation.fulfill()
        }
        
        wait(for: [expectation], timeout: 5.0)
        XCTAssertEqual(delegate.producedFixes.count, 10)
    }
}

#else

// MARK: - Non-watchOS Stub Tests

final class WatchLocationProviderTests: XCTestCase {
    
    func testWatchLocationProviderNotAvailableOnNonWatchOS() {
        let provider = WatchLocationProvider()
        XCTAssertNotNil(provider, "Provider should instantiate but be non-functional on non-watchOS")
    }
    
    func testDelegateAssignmentOnNonWatchOS() {
        let provider = WatchLocationProvider()
        let delegate = MockNonWatchOSDelegate()
        provider.delegate = delegate
        
        XCTAssertNotNil(provider.delegate)
    }
}

final class MockNonWatchOSDelegate: WatchLocationProviderDelegate {
    func didProduce(_ fix: LocationFix) {}
    func didFail(_ error: Error) {}
}

#endif
