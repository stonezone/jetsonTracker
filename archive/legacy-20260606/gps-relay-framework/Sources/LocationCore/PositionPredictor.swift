import Foundation

/// Kinematic position predictor for smooth tracking during GPS gaps
/// Uses simple dead reckoning based on last known velocity and course
public final class PositionPredictor: @unchecked Sendable {
    
    // MARK: - Configuration
    
    /// Maximum age of a fix before prediction becomes unreliable (seconds)
    public var maxPredictionAge: TimeInterval = 5.0
    
    /// Minimum speed to trust course data (m/s) - below this, course is noisy
    public var minSpeedForCourse: Double = 0.5
    
    /// Smoothing factor for course (0-1, higher = more smoothing)
    public var courseSmoothingFactor: Double = 0.3
    
    // Issue #15: Speed-based course confidence thresholds
    /// Speed above which course is fully trusted (m/s)
    public var fullCourseConfidenceSpeed: Double = 2.0
    
    // MARK: - State
    
    private var lastFix: LocationFix?
    private var smoothedCourse: Double?
    private var velocityHistory: [(speed: Double, course: Double, timestamp: Date)] = []
    private let maxHistorySize = 10
    private let lock = NSLock()
    
    // Issue #7: Acceleration modeling for dynamic subjects (surfing)
    private var accelerationHistory: [Double] = []  // m/s²
    private let maxAccelerationHistory = 5
    private let maxRealisticAcceleration: Double = 5.0  // m/s² - surfing bounds
    
    public init() {}
    
    // MARK: - Public API
    
    /// Update predictor with new GPS fix
    public func update(with fix: LocationFix) {
        lock.lock()
        defer { lock.unlock() }
        
        // Issue #7: Calculate implied acceleration from velocity change
        if let previousFix = lastFix {
            let dt = fix.timestamp.timeIntervalSince(previousFix.timestamp)
            if dt > 0.1 && dt < 5.0 {  // Reasonable time gap
                let dv = fix.speedMetersPerSecond - previousFix.speedMetersPerSecond
                let acceleration = dv / dt
                // Clamp to realistic bounds for surfing
                let clampedAccel = max(-maxRealisticAcceleration, min(maxRealisticAcceleration, acceleration))
                accelerationHistory.append(clampedAccel)
                if accelerationHistory.count > maxAccelerationHistory {
                    accelerationHistory.removeFirst()
                }
            }
        }
        
        // Update smoothed course if speed is sufficient
        // Issue #15: Apply speed-based course confidence weighting
        if fix.speedMetersPerSecond >= minSpeedForCourse && fix.courseDegrees > 0 {
            // Calculate course confidence based on speed
            // At minSpeedForCourse: low confidence (0.3), at fullCourseConfidenceSpeed: full confidence (1.0)
            let speedRange = fullCourseConfidenceSpeed - minSpeedForCourse
            let speedRatio = min(1.0, (fix.speedMetersPerSecond - minSpeedForCourse) / max(0.1, speedRange))
            let courseConfidence = 0.3 + (0.7 * speedRatio)  // Range: 0.3 to 1.0
            
            // Adjust smoothing factor based on confidence - higher confidence = less smoothing (faster adaptation)
            let adaptiveSmoothingFactor = courseSmoothingFactor * (1.0 - courseConfidence * 0.5)
            
            if let current = smoothedCourse {
                // Exponential moving average with wrap-around handling
                smoothedCourse = smoothAngle(from: current, to: fix.courseDegrees, factor: adaptiveSmoothingFactor)
            } else {
                smoothedCourse = fix.courseDegrees
            }
        }
        
        // Store velocity sample
        velocityHistory.append((fix.speedMetersPerSecond, fix.courseDegrees, fix.timestamp))
        if velocityHistory.count > maxHistorySize {
            velocityHistory.removeFirst()
        }
        
        lastFix = fix
    }
    
    /// Predict position at given time based on last known fix
    /// Returns nil if no fix available or prediction would be too stale
    public func predictPosition(at time: Date = Date()) -> PredictedPosition? {
        lock.lock()
        defer { lock.unlock() }
        
        guard let fix = lastFix else { return nil }
        
        let elapsed = time.timeIntervalSince(fix.timestamp)
        
        // Don't predict too far into the future
        guard elapsed >= 0 && elapsed <= maxPredictionAge else { return nil }
        
        // If elapsed is very small, just return current position
        if elapsed < 0.1 {
            return PredictedPosition(
                coordinate: fix.coordinate,
                predictedAt: time,
                basedOnFixAt: fix.timestamp,
                confidence: 1.0,
                predictedSpeed: fix.speedMetersPerSecond,
                predictedCourse: smoothedCourse ?? fix.courseDegrees
            )
        }
        
        // Use smoothed course if available, otherwise fall back to fix course
        let course = smoothedCourse ?? fix.courseDegrees
        let speed = fix.speedMetersPerSecond
        
        // Issue #7: Use acceleration modeling for better prediction
        let avgAcceleration = accelerationHistory.isEmpty ? 0.0 : 
            accelerationHistory.reduce(0, +) / Double(accelerationHistory.count)
        
        // Calculate displacement with acceleration: d = v*t + 0.5*a*t²
        let distance = speed * elapsed + 0.5 * avgAcceleration * elapsed * elapsed
        
        // Predict new speed (for confidence calculation)
        let predictedSpeed = max(0, speed + avgAcceleration * elapsed)
        let (newLat, newLon) = projectPosition(
            from: fix.coordinate,
            distanceMeters: distance,
            bearingDegrees: course
        )
        
        // Confidence decays with time
        let confidence = max(0, 1.0 - (elapsed / maxPredictionAge))
        
        return PredictedPosition(
            coordinate: LocationFix.Coordinate(latitude: newLat, longitude: newLon),
            predictedAt: time,
            basedOnFixAt: fix.timestamp,
            confidence: confidence,
            predictedSpeed: predictedSpeed,  // Issue #7: Use predicted speed
            predictedCourse: course
        )
    }
    
    /// Get average velocity over recent history
    public func averageVelocity() -> (speed: Double, course: Double)? {
        lock.lock()
        defer { lock.unlock() }
        
        guard !velocityHistory.isEmpty else { return nil }
        
        let avgSpeed = velocityHistory.map { $0.speed }.reduce(0, +) / Double(velocityHistory.count)
        
        // Average course using circular mean
        let sinSum = velocityHistory.map { sin($0.course * .pi / 180) }.reduce(0, +)
        let cosSum = velocityHistory.map { cos($0.course * .pi / 180) }.reduce(0, +)
        let avgCourse = atan2(sinSum, cosSum) * 180 / .pi
        let normalizedCourse = avgCourse < 0 ? avgCourse + 360 : avgCourse
        
        return (avgSpeed, normalizedCourse)
    }
    
    /// Clear all state
    public func reset() {
        lock.lock()
        defer { lock.unlock() }
        lastFix = nil
        smoothedCourse = nil
        velocityHistory.removeAll()
        accelerationHistory.removeAll()  // Issue #7
    }
    
    // MARK: - Private Helpers
    
    /// Smooth angle transition handling wrap-around at 0/360
    private func smoothAngle(from current: Double, to target: Double, factor: Double) -> Double {
        var delta = target - current
        
        // Handle wrap-around
        if delta > 180 { delta -= 360 }
        if delta < -180 { delta += 360 }
        
        var result = current + delta * (1 - factor)
        
        // Normalize to 0-360
        if result < 0 { result += 360 }
        if result >= 360 { result -= 360 }
        
        return result
    }
    
    /// Project position given distance and bearing (simple spherical approximation)
    private func projectPosition(
        from coord: LocationFix.Coordinate,
        distanceMeters: Double,
        bearingDegrees: Double
    ) -> (latitude: Double, longitude: Double) {
        let earthRadius = 6_371_000.0 // meters
        
        let lat1 = coord.latitude * .pi / 180
        let lon1 = coord.longitude * .pi / 180
        let bearing = bearingDegrees * .pi / 180
        let angularDistance = distanceMeters / earthRadius
        
        let lat2 = asin(
            sin(lat1) * cos(angularDistance) +
            cos(lat1) * sin(angularDistance) * cos(bearing)
        )
        
        let lon2 = lon1 + atan2(
            sin(bearing) * sin(angularDistance) * cos(lat1),
            cos(angularDistance) - sin(lat1) * sin(lat2)
        )
        
        return (lat2 * 180 / .pi, lon2 * 180 / .pi)
    }
}

// MARK: - Predicted Position

public struct PredictedPosition: Codable, Equatable, Sendable {
    public let coordinate: LocationFix.Coordinate
    public let predictedAt: Date
    public let basedOnFixAt: Date
    public let confidence: Double  // 0-1, decays with prediction age
    public let predictedSpeed: Double
    public let predictedCourse: Double
    
    /// Age of the prediction in seconds (how far we've extrapolated)
    public var predictionAge: TimeInterval {
        predictedAt.timeIntervalSince(basedOnFixAt)
    }
    
    /// True if this is a real-time prediction (very recent fix)
    public var isRealTime: Bool {
        predictionAge < 0.5
    }
}
