import Foundation

// MARK: - Tracking Extensions for Gimbal Control

public extension LocationFix.Coordinate {
    
    /// Calculate distance to another coordinate in meters
    func distance(to other: LocationFix.Coordinate) -> Double {
        let earthRadius = 6_371_000.0 // meters
        
        let lat1 = latitude * .pi / 180
        let lat2 = other.latitude * .pi / 180
        let deltaLat = (other.latitude - latitude) * .pi / 180
        let deltaLon = (other.longitude - longitude) * .pi / 180
        
        // Haversine formula
        let a = sin(deltaLat / 2) * sin(deltaLat / 2) +
                cos(lat1) * cos(lat2) * sin(deltaLon / 2) * sin(deltaLon / 2)
        let c = 2 * atan2(sqrt(a), sqrt(1 - a))
        
        return earthRadius * c
    }
    
    /// Calculate bearing from this coordinate to another in degrees (0-360)
    func bearing(to other: LocationFix.Coordinate) -> Double {
        let lat1 = latitude * .pi / 180
        let lat2 = other.latitude * .pi / 180
        let deltaLon = (other.longitude - longitude) * .pi / 180
        
        let y = sin(deltaLon) * cos(lat2)
        let x = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(deltaLon)
        
        var bearing = atan2(y, x) * 180 / .pi
        if bearing < 0 { bearing += 360 }
        
        return bearing
    }
    
    /// Calculate elevation angle to target at given altitude difference (degrees above/below horizon)
    func elevationAngle(to other: LocationFix.Coordinate, altitudeDelta: Double) -> Double {
        let dist = distance(to: other)
        guard dist > 0 else { return 0 }
        return atan2(altitudeDelta, dist) * 180 / .pi
    }
}

// MARK: - Gimbal Targeting

/// Represents gimbal pan/tilt angles needed to track a target
public struct GimbalTarget: Codable, Equatable, Sendable {
    /// Pan angle in degrees (0 = North, 90 = East, 180 = South, 270 = West)
    public let panDegrees: Double
    
    /// Tilt angle in degrees (0 = horizon, positive = up, negative = down)
    public let tiltDegrees: Double
    
    /// Distance to target in meters
    public let distanceMeters: Double
    
    /// Confidence in the target (0-1)
    public let confidence: Double
    
    /// Timestamp when this target was calculated
    public let timestamp: Date
    
    public init(panDegrees: Double, tiltDegrees: Double, distanceMeters: Double, confidence: Double, timestamp: Date = Date()) {
        self.panDegrees = panDegrees
        self.tiltDegrees = tiltDegrees
        self.distanceMeters = distanceMeters
        self.confidence = confidence
        self.timestamp = timestamp
    }
}

/// Calculates gimbal angles from base station to remote target
public struct GimbalCalculator: Sendable {
    
    /// Base station (phone/tripod) location
    public let baseCoordinate: LocationFix.Coordinate
    public let baseAltitude: Double
    public let baseHeading: Double?  // Compass heading of base, nil if unknown
    
    public init(baseCoordinate: LocationFix.Coordinate, baseAltitude: Double, baseHeading: Double? = nil) {
        self.baseCoordinate = baseCoordinate
        self.baseAltitude = baseAltitude
        self.baseHeading = baseHeading
    }
    
    /// Calculate gimbal target for a remote fix
    public func target(for remoteFix: LocationFix, confidence: Double = 1.0) -> GimbalTarget {
        let remoteCoord = remoteFix.coordinate
        let remoteAlt = remoteFix.altitudeMeters ?? baseAltitude
        
        // Calculate absolute bearing from base to remote
        var panAngle = baseCoordinate.bearing(to: remoteCoord)
        
        // If we know the base heading (compass direction gimbal is initially facing),
        // convert to relative pan angle
        if let heading = baseHeading {
            panAngle = panAngle - heading
            if panAngle < 0 { panAngle += 360 }
            if panAngle >= 360 { panAngle -= 360 }
        }
        
        // Calculate tilt
        let altitudeDelta = remoteAlt - baseAltitude
        let tiltAngle = baseCoordinate.elevationAngle(to: remoteCoord, altitudeDelta: altitudeDelta)
        
        // Calculate distance
        let distance = baseCoordinate.distance(to: remoteCoord)
        
        return GimbalTarget(
            panDegrees: panAngle,
            tiltDegrees: tiltAngle,
            distanceMeters: distance,
            confidence: confidence,
            timestamp: remoteFix.timestamp
        )
    }
    
    /// Calculate gimbal target from a predicted position
    public func target(for prediction: PredictedPosition) -> GimbalTarget {
        let remoteCoord = prediction.coordinate
        
        var panAngle = baseCoordinate.bearing(to: remoteCoord)
        if let heading = baseHeading {
            panAngle = panAngle - heading
            if panAngle < 0 { panAngle += 360 }
            if panAngle >= 360 { panAngle -= 360 }
        }
        
        // No altitude in prediction, assume same as base
        let tiltAngle = 0.0
        let distance = baseCoordinate.distance(to: remoteCoord)
        
        return GimbalTarget(
            panDegrees: panAngle,
            tiltDegrees: tiltAngle,
            distanceMeters: distance,
            confidence: prediction.confidence,
            timestamp: prediction.predictedAt
        )
    }
}

// MARK: - Angular Velocity

/// Tracks angular velocity of gimbal target for smooth tracking
public final class AngularVelocityTracker: @unchecked Sendable {
    
    private var history: [(pan: Double, tilt: Double, timestamp: Date)] = []
    private let maxHistory = 5
    private let lock = NSLock()
    
    public init() {}
    
    /// Add a gimbal target sample
    public func add(_ target: GimbalTarget) {
        lock.lock()
        defer { lock.unlock() }
        
        history.append((target.panDegrees, target.tiltDegrees, target.timestamp))
        if history.count > maxHistory {
            history.removeFirst()
        }
    }
    
    /// Get current angular velocity in degrees per second
    public func angularVelocity() -> (panVelocity: Double, tiltVelocity: Double)? {
        lock.lock()
        defer { lock.unlock() }
        
        guard history.count >= 2 else { return nil }
        
        let first = history.first!
        let last = history.last!
        let dt = last.timestamp.timeIntervalSince(first.timestamp)
        
        guard dt > 0.1 else { return nil }
        
        // Handle pan wrap-around
        var panDelta = last.pan - first.pan
        if panDelta > 180 { panDelta -= 360 }
        if panDelta < -180 { panDelta += 360 }
        
        let tiltDelta = last.tilt - first.tilt
        
        return (panDelta / dt, tiltDelta / dt)
    }
    
    /// Reset tracker
    public func reset() {
        lock.lock()
        defer { lock.unlock() }
        history.removeAll()
    }
}
