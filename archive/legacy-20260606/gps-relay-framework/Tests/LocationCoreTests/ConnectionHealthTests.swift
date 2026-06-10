import XCTest
@testable import LocationCore

final class ConnectionHealthTests: XCTestCase {
    
    // MARK: - ConnectionQualityTracker Tests
    
    func testQualityTrackerEmptySnapshot() {
        let tracker = ConnectionQualityTracker()
        let snapshot = tracker.snapshot()
        
        XCTAssertEqual(snapshot.avgLatencyMs, 0)
        XCTAssertEqual(snapshot.packetLossRate, 0)
        XCTAssertEqual(snapshot.messagesPerSecond, 0)
        XCTAssertFalse(snapshot.isHealthy) // No recent messages = not healthy
    }
    
    func testQualityTrackerRecordsMessages() {
        let tracker = ConnectionQualityTracker()
        
        // Record some messages
        tracker.recordMessage(sequence: 1, latencyMs: 100)
        tracker.recordMessage(sequence: 2, latencyMs: 150)
        tracker.recordMessage(sequence: 3, latencyMs: 200)
        
        let snapshot = tracker.snapshot()
        
        XCTAssertEqual(snapshot.avgLatencyMs, 150, accuracy: 1)
        XCTAssertTrue(snapshot.lastUpdateAge < 1)
    }
    
    func testQualityTrackerHealthyConnection() {
        let tracker = ConnectionQualityTracker()
        
        // Simulate healthy connection: low latency, no packet loss
        for i in 1...10 {
            tracker.recordMessage(sequence: i, latencyMs: Double(50 + i * 5))
        }
        
        let snapshot = tracker.snapshot()
        
        XCTAssertTrue(snapshot.avgLatencyMs < 500)
        XCTAssertTrue(snapshot.isHealthy)
        XCTAssertGreaterThan(snapshot.connectionScore, 70)
    }
    
    func testQualityTrackerHighLatencyPenalty() {
        let tracker = ConnectionQualityTracker()
        
        // Record high latency messages
        for i in 1...10 {
            tracker.recordMessage(sequence: i, latencyMs: 800)
        }
        
        let snapshot = tracker.snapshot()
        
        XCTAssertFalse(snapshot.isHealthy)
        XCTAssertLessThan(snapshot.connectionScore, 70)
    }
    
    func testQualityTrackerReset() {
        let tracker = ConnectionQualityTracker()
        
        tracker.recordMessage(sequence: 1, latencyMs: 100)
        XCTAssertGreaterThan(tracker.snapshot().avgLatencyMs, 0)
        
        tracker.reset()
        
        XCTAssertEqual(tracker.snapshot().avgLatencyMs, 0)
    }
    
    // MARK: - HeartbeatManager Tests
    
    func testHeartbeatManagerInitialState() {
        let manager = HeartbeatManager()
        
        if case .healthy = manager.state {
            // Expected
        } else {
            XCTFail("Initial state should be healthy")
        }
        
        XCTAssertNil(manager.lastRTT)
        XCTAssertNil(manager.averageRTT)
    }
    
    func testHeartbeatPongResponse() {
        let manager = HeartbeatManager()
        
        let ping = HeartbeatMessage(type: .ping, sequence: 1)
        let pong = manager.createPong(for: ping)
        
        XCTAssertEqual(pong.type, .pong)
        XCTAssertEqual(pong.sequence, ping.sequence)
        XCTAssertEqual(pong.correlationId, ping.correlationId)
    }
    
    // MARK: - JitterBuffer Tests
    
    func testJitterBufferInOrder() {
        let buffer = JitterBuffer<Int>(bufferSize: 3, maxDelay: 0.1)
        
        // Add items in order
        let result1 = buffer.add(1, sequence: 1)
        let result2 = buffer.add(2, sequence: 2)
        let result3 = buffer.add(3, sequence: 3)
        
        // Should emit when buffer is full
        XCTAssertNil(result1)
        XCTAssertNil(result2)
        XCTAssertEqual(result3, 1)
    }
    
    func testJitterBufferFlush() {
        let buffer = JitterBuffer<String>(bufferSize: 5, maxDelay: 0.1)
        
        _ = buffer.add("a", sequence: 1)
        _ = buffer.add("b", sequence: 2)
        
        let flushed = buffer.flush()
        
        XCTAssertEqual(flushed, ["a", "b"])
        XCTAssertTrue(buffer.flush().isEmpty)
    }
    
    // MARK: - GPSJumpDetector Tests
    
    func testGPSJumpDetectorFirstFix() {
        let detector = GPSJumpDetector()
        let fix = makeTestFix(lat: 21.5, lon: -158.0, speed: 5)
        
        let result = detector.validate(fix)
        
        XCTAssertTrue(result.isValid)
        XCTAssertNil(result.reason)
    }
    
    func testGPSJumpDetectorNormalMovement() {
        let detector = GPSJumpDetector()
        
        // First fix
        let fix1 = makeTestFix(lat: 21.5, lon: -158.0, speed: 5)
        _ = detector.validate(fix1)
        
        // Second fix 100m away after 1 second (100 m/s = 360 km/h is too fast!)
        // Let's use realistic movement: 10m in 1 second = 10 m/s = 36 km/h
        let fix2 = makeTestFix(lat: 21.5 + 0.00009, lon: -158.0, speed: 10)  // ~10m north
        let result = detector.validate(fix2)
        
        XCTAssertTrue(result.isValid, result.reason ?? "")
    }
    
    func testGPSJumpDetectorImpossibleSpeed() {
        let detector = GPSJumpDetector()
        detector.maxSpeed = 55  // ~200 km/h
        
        // First fix
        let fix1 = makeTestFix(lat: 21.5, lon: -158.0, speed: 5)
        _ = detector.validate(fix1)
        
        // Wait a tiny bit for time delta
        Thread.sleep(forTimeInterval: 0.2)
        
        // Second fix 1km away after 0.2 second (5000 m/s = impossible)
        let fix2 = makeTestFix(lat: 21.5 + 0.009, lon: -158.0, speed: 10)  // ~1km north
        let result = detector.validate(fix2)
        
        XCTAssertFalse(result.isValid)
        XCTAssertNotNil(result.reason)
        XCTAssertTrue(result.reason?.contains("Impossible speed") ?? false)
    }
    
    func testGPSJumpDetectorReset() {
        let detector = GPSJumpDetector()
        
        let fix1 = makeTestFix(lat: 21.5, lon: -158.0, speed: 5)
        _ = detector.validate(fix1)
        
        detector.reset()
        
        // After reset, first fix should be accepted regardless
        let fix2 = makeTestFix(lat: 50.0, lon: -100.0, speed: 0)  // Far away
        let result = detector.validate(fix2)
        
        XCTAssertTrue(result.isValid)
    }
    
    // MARK: - LatencyMeasurement Tests
    
    func testLatencyMeasurementWatchToPhone() {
        let gpsTime = Date().addingTimeInterval(-0.2)
        let phoneTime = Date()
        
        let measurement = LatencyMeasurement(
            gpsTimestamp: gpsTime,
            phoneReceivedAt: phoneTime,
            serverSentAt: phoneTime,
            sequence: 1
        )
        
        XCTAssertEqual(measurement.watchToPhoneLatencyMs, 200, accuracy: 10)
        XCTAssertNil(measurement.totalLatencyMs)  // No server ack
    }
    
    func testLatencyMeasurementEndToEnd() {
        let gpsTime = Date().addingTimeInterval(-0.5)
        let phoneTime = Date().addingTimeInterval(-0.3)
        let serverTime = Date()
        
        let measurement = LatencyMeasurement(
            gpsTimestamp: gpsTime,
            phoneReceivedAt: phoneTime,
            serverSentAt: phoneTime,
            serverAckAt: serverTime,
            sequence: 1
        )
        
        XCTAssertEqual(measurement.totalLatencyMs ?? 0, 500, accuracy: 20)
        XCTAssertEqual(measurement.watchToPhoneLatencyMs, 200, accuracy: 10)
        XCTAssertEqual(measurement.phoneToServerLatencyMs ?? 0, 300, accuracy: 10)
    }
    
    // MARK: - Helpers
    
    private func makeTestFix(
        lat: Double,
        lon: Double,
        speed: Double = 0,
        course: Double = 0
    ) -> LocationFix {
        LocationFix(
            timestamp: Date(),
            source: .watchOS,
            coordinate: .init(latitude: lat, longitude: lon),
            altitudeMeters: 10,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 10,
            speedMetersPerSecond: speed,
            courseDegrees: course,
            headingDegrees: nil,
            batteryFraction: 0.8,
            sequence: Int.random(in: 0...1000)
        )
    }
}
