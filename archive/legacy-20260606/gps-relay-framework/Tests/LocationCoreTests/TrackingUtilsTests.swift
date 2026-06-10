import XCTest
@testable import LocationCore

final class TrackingUtilsTests: XCTestCase {
    
    // MARK: - Distance Tests
    
    func testDistanceSamePoint() {
        let coord = LocationFix.Coordinate(latitude: 21.5, longitude: -158.0)
        XCTAssertEqual(coord.distance(to: coord), 0, accuracy: 0.1)
    }
    
    func testDistanceKnownPoints() {
        // Waimea Bay to Pipeline - approximately 3.5 km
        let waimea = LocationFix.Coordinate(latitude: 21.6417, longitude: -158.0631)
        let pipeline = LocationFix.Coordinate(latitude: 21.6650, longitude: -158.0533)
        
        let distance = waimea.distance(to: pipeline)
        XCTAssertEqual(distance, 2800, accuracy: 200) // ~2.8km with some tolerance
    }
    
    func testDistanceSymmetric() {
        let a = LocationFix.Coordinate(latitude: 21.5, longitude: -158.0)
        let b = LocationFix.Coordinate(latitude: 21.6, longitude: -158.1)
        
        XCTAssertEqual(a.distance(to: b), b.distance(to: a), accuracy: 0.1)
    }
    
    // MARK: - Bearing Tests
    
    func testBearingNorth() {
        let south = LocationFix.Coordinate(latitude: 21.0, longitude: -158.0)
        let north = LocationFix.Coordinate(latitude: 22.0, longitude: -158.0)
        
        let bearing = south.bearing(to: north)
        XCTAssertEqual(bearing, 0, accuracy: 1) // Due north
    }
    
    func testBearingEast() {
        let west = LocationFix.Coordinate(latitude: 21.5, longitude: -158.5)
        let east = LocationFix.Coordinate(latitude: 21.5, longitude: -157.5)
        
        let bearing = west.bearing(to: east)
        XCTAssertEqual(bearing, 90, accuracy: 1) // Due east
    }
    
    func testBearingSouth() {
        let north = LocationFix.Coordinate(latitude: 22.0, longitude: -158.0)
        let south = LocationFix.Coordinate(latitude: 21.0, longitude: -158.0)
        
        let bearing = north.bearing(to: south)
        XCTAssertEqual(bearing, 180, accuracy: 1) // Due south
    }
    
    func testBearingWest() {
        let east = LocationFix.Coordinate(latitude: 21.5, longitude: -157.5)
        let west = LocationFix.Coordinate(latitude: 21.5, longitude: -158.5)
        
        let bearing = east.bearing(to: west)
        XCTAssertEqual(bearing, 270, accuracy: 1) // Due west
    }
    
    // MARK: - Elevation Angle Tests
    
    func testElevationAngleSameAltitude() {
        let a = LocationFix.Coordinate(latitude: 21.5, longitude: -158.0)
        let b = LocationFix.Coordinate(latitude: 21.6, longitude: -158.0)
        
        let elevation = a.elevationAngle(to: b, altitudeDelta: 0)
        XCTAssertEqual(elevation, 0, accuracy: 0.1)
    }
    
    func testElevationAngleAbove() {
        let base = LocationFix.Coordinate(latitude: 21.5, longitude: -158.0)
        let target = LocationFix.Coordinate(latitude: 21.5001, longitude: -158.0) // ~11m away
        
        // Target is 10m higher, ~11m away -> ~42 degrees up
        let elevation = base.elevationAngle(to: target, altitudeDelta: 10)
        XCTAssertGreaterThan(elevation, 30)
        XCTAssertLessThan(elevation, 50)
    }
    
    func testElevationAngleBelow() {
        let base = LocationFix.Coordinate(latitude: 21.5, longitude: -158.0)
        let target = LocationFix.Coordinate(latitude: 21.5001, longitude: -158.0)
        
        let elevation = base.elevationAngle(to: target, altitudeDelta: -10)
        XCTAssertLessThan(elevation, 0) // Should be negative (below horizon)
    }
    
    // MARK: - GimbalCalculator Tests
    
    func testGimbalTargetNorthOfBase() {
        let baseCoord = LocationFix.Coordinate(latitude: 21.5, longitude: -158.0)
        let calculator = GimbalCalculator(baseCoordinate: baseCoord, baseAltitude: 0, baseHeading: nil)
        
        let remoteFix = makeTestFix(lat: 21.6, lon: -158.0, speed: 5, course: 180)
        let target = calculator.target(for: remoteFix)
        
        XCTAssertEqual(target.panDegrees, 0, accuracy: 5) // Should point north
        XCTAssertGreaterThan(target.distanceMeters, 10000) // About 11km north
    }
    
    func testGimbalTargetWithBaseHeading() {
        let baseCoord = LocationFix.Coordinate(latitude: 21.5, longitude: -158.0)
        // Base is facing east (90°)
        let calculator = GimbalCalculator(baseCoordinate: baseCoord, baseAltitude: 0, baseHeading: 90)
        
        // Remote is due north
        let remoteFix = makeTestFix(lat: 21.6, lon: -158.0, speed: 5, course: 180)
        let target = calculator.target(for: remoteFix)
        
        // Pan should be -90° (or 270°) to look left from east-facing to north
        XCTAssertEqual(target.panDegrees, 270, accuracy: 5)
    }
    
    // MARK: - Helpers
    
    private func makeTestFix(lat: Double, lon: Double, speed: Double, course: Double) -> LocationFix {
        LocationFix(
            timestamp: Date(),
            source: .watchOS,
            coordinate: .init(latitude: lat, longitude: lon),
            altitudeMeters: 0,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: speed,
            courseDegrees: course,
            headingDegrees: nil,
            batteryFraction: 0.8,
            sequence: 1
        )
    }
}

final class PositionPredictorTests: XCTestCase {
    
    func testPredictWithoutFix() {
        let predictor = PositionPredictor()
        XCTAssertNil(predictor.predictPosition())
    }
    
    func testPredictImmediatelyAfterFix() {
        let predictor = PositionPredictor()
        let fix = makeTestFix(lat: 21.5, lon: -158.0, speed: 10, course: 90)
        
        predictor.update(with: fix)
        let prediction = predictor.predictPosition()
        
        XCTAssertNotNil(prediction)
        XCTAssertEqual(prediction?.confidence ?? 0, 1.0, accuracy: 0.1)
    }
    
    func testPredictMovingEast() {
        let predictor = PositionPredictor()
        predictor.maxPredictionAge = 10.0
        
        // Moving east at 10 m/s
        let fix = makeTestFix(lat: 21.5, lon: -158.0, speed: 10, course: 90)
        predictor.update(with: fix)
        
        // Predict 1 second in the future (manually)
        // At 10 m/s east for 1 second = 10m east
        // We can't easily test time-based prediction without mocking, but we can verify structure
        let prediction = predictor.predictPosition()
        XCTAssertNotNil(prediction)
        XCTAssertEqual(prediction?.predictedCourse ?? 0, 90, accuracy: 5)
    }
    
    func testCourseSmoothingUpdates() {
        let predictor = PositionPredictor()
        
        // First fix heading north (use 1 instead of 0 since course > 0 required for smoothing)
        let fix1 = makeTestFix(lat: 21.5, lon: -158.0, speed: 5, course: 1)
        predictor.update(with: fix1)
        
        // Second fix heading east - should smooth
        let fix2 = makeTestFix(lat: 21.5001, lon: -158.0, speed: 5, course: 90)
        predictor.update(with: fix2)
        
        let prediction = predictor.predictPosition()
        // Course should be smoothed between initial (1) and target (90)
        // With smoothing factor 0.3: result = 1 + (90-1) * 0.7 = 63.3
        XCTAssertNotNil(prediction)
        if let course = prediction?.predictedCourse {
            XCTAssertGreaterThan(course, 1)
            XCTAssertLessThan(course, 90)
        }
    }
    
    func testAverageVelocity() {
        let predictor = PositionPredictor()
        
        // Add several fixes at same speed/course
        for i in 0..<5 {
            let fix = makeTestFix(lat: 21.5 + Double(i) * 0.0001, lon: -158.0, speed: 8, course: 45)
            predictor.update(with: fix)
        }
        
        let velocity = predictor.averageVelocity()
        XCTAssertNotNil(velocity)
        XCTAssertEqual(velocity?.speed ?? 0, 8, accuracy: 0.1)
        XCTAssertEqual(velocity?.course ?? 0, 45, accuracy: 5)
    }
    
    func testReset() {
        let predictor = PositionPredictor()
        let fix = makeTestFix(lat: 21.5, lon: -158.0, speed: 10, course: 90)
        predictor.update(with: fix)
        
        predictor.reset()
        
        XCTAssertNil(predictor.predictPosition())
        XCTAssertNil(predictor.averageVelocity())
    }
    
    // MARK: - Helpers
    
    private func makeTestFix(lat: Double, lon: Double, speed: Double, course: Double) -> LocationFix {
        LocationFix(
            timestamp: Date(),
            source: .watchOS,
            coordinate: .init(latitude: lat, longitude: lon),
            altitudeMeters: 0,
            horizontalAccuracyMeters: 5,
            verticalAccuracyMeters: 5,
            speedMetersPerSecond: speed,
            courseDegrees: course,
            headingDegrees: nil,
            batteryFraction: 0.8,
            sequence: Int(Date().timeIntervalSinceReferenceDate)
        )
    }
}
