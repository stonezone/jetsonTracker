import Foundation

// MARK: - GPS Kalman Filter

/// Extended Kalman Filter for GPS position tracking.
/// Provides smooth position estimates during GPS gaps and handles measurement noise.
///
/// State vector: [x, y, vx, vy] where:
/// - x, y: Position in local ENU (East-North-Up) coordinates (meters)
/// - vx, vy: Velocity in m/s
///
/// The filter uses GPS accuracy as measurement noise and models human/vehicle
/// motion with appropriate process noise for smooth tracking during turns.
public final class GPSKalmanFilter: @unchecked Sendable {

    // MARK: - Configuration

    public struct Configuration {
        /// Process noise for position (m²) - higher = more trust in measurements
        public var positionProcessNoise: Double = 0.1

        /// Process noise for velocity (m²/s²) - higher = allows faster acceleration
        public var velocityProcessNoise: Double = 1.0

        /// Minimum GPS accuracy to trust (meters)
        public var minMeasurementNoise: Double = 1.0

        /// Maximum GPS accuracy before heavy filtering (meters)
        public var maxMeasurementNoise: Double = 100.0

        /// Maximum time gap before resetting filter (seconds)
        public var maxPredictionGap: TimeInterval = 10.0

        public init() {}
    }

    // MARK: - State

    /// Filter state: [x, y, vx, vy]
    private var state: [Double] = [0, 0, 0, 0]

    /// State covariance matrix (4x4)
    private var covariance: [[Double]] = Array(repeating: Array(repeating: 0, count: 4), count: 4)

    /// Reference point for local coordinate conversion
    private var referenceLatitude: Double = 0
    private var referenceLongitude: Double = 0
    private var hasReference: Bool = false

    /// Last update timestamp
    private var lastUpdateTime: Date?

    /// Configuration
    public var configuration: Configuration

    /// Lock for thread safety
    private let lock = NSLock()

    /// Whether the filter has been initialized with at least one measurement
    public private(set) var isInitialized: Bool = false

    // MARK: - Initialization

    public init(configuration: Configuration = Configuration()) {
        self.configuration = configuration
        initializeCovariance()
    }

    private func initializeCovariance() {
        // Initial uncertainty: high for position, medium for velocity
        covariance[0][0] = 100  // x position variance (m²)
        covariance[1][1] = 100  // y position variance (m²)
        covariance[2][2] = 10   // vx velocity variance (m²/s²)
        covariance[3][3] = 10   // vy velocity variance (m²/s²)
    }

    // MARK: - Public API

    /// Update filter with new GPS measurement
    /// - Parameters:
    ///   - latitude: GPS latitude in degrees
    ///   - longitude: GPS longitude in degrees
    ///   - accuracy: Horizontal accuracy in meters
    ///   - speed: Speed in m/s (optional, improves velocity estimate)
    ///   - course: Course in degrees (optional, improves velocity estimate)
    ///   - timestamp: Measurement timestamp
    /// - Returns: Filtered position estimate
    public func update(
        latitude: Double,
        longitude: Double,
        accuracy: Double,
        speed: Double? = nil,
        course: Double? = nil,
        timestamp: Date
    ) -> FilteredPosition {
        lock.lock()
        defer { lock.unlock() }

        // Set reference point on first measurement
        if !hasReference {
            referenceLatitude = latitude
            referenceLongitude = longitude
            hasReference = true
        }

        // Convert to local ENU coordinates
        let (x, y) = latLonToENU(latitude: latitude, longitude: longitude)

        // Time delta
        let dt: Double
        if let lastTime = lastUpdateTime {
            dt = timestamp.timeIntervalSince(lastTime)

            // Reset if too much time has passed
            if dt > configuration.maxPredictionGap || dt < 0 {
                reset()
                return initializeWithMeasurement(x: x, y: y, accuracy: accuracy, speed: speed, course: course, timestamp: timestamp)
            }
        } else {
            return initializeWithMeasurement(x: x, y: y, accuracy: accuracy, speed: speed, course: course, timestamp: timestamp)
        }

        // Prediction step
        predict(dt: dt)

        // Update step with GPS measurement
        let measurementNoise = max(configuration.minMeasurementNoise, min(accuracy, configuration.maxMeasurementNoise))
        updateWithPosition(x: x, y: y, noise: measurementNoise, dt: dt)

        // Optionally update with velocity if speed/course available
        if let speed = speed, let course = course, speed > 0.5 {
            let vx = speed * sin(course * .pi / 180)
            let vy = speed * cos(course * .pi / 180)
            // Velocity measurement noise scales with speed uncertainty
            let velocityNoise = max(0.5, accuracy * 0.1)
            updateWithVelocity(vx: vx, vy: vy, noise: velocityNoise)
        }

        lastUpdateTime = timestamp
        isInitialized = true

        return currentEstimate(timestamp: timestamp)
    }

    /// Predict position at a future time without updating the filter
    /// - Parameter timestamp: Time to predict for
    /// - Returns: Predicted position, or nil if filter not initialized
    public func predict(at timestamp: Date) -> FilteredPosition? {
        lock.lock()
        defer { lock.unlock() }

        guard isInitialized, let lastTime = lastUpdateTime else { return nil }

        let dt = timestamp.timeIntervalSince(lastTime)
        guard dt >= 0 && dt <= configuration.maxPredictionGap else { return nil }

        // Predict state without modifying filter
        let predictedX = state[0] + state[2] * dt
        let predictedY = state[1] + state[3] * dt

        // Convert back to lat/lon
        let (lat, lon) = enuToLatLon(x: predictedX, y: predictedY)

        // Confidence decreases with prediction time
        let confidence = max(0, 1.0 - dt / configuration.maxPredictionGap)

        // Position uncertainty grows with time
        let positionUncertainty = sqrt(covariance[0][0] + covariance[1][1]) + sqrt(covariance[2][2] + covariance[3][3]) * dt

        return FilteredPosition(
            latitude: lat,
            longitude: lon,
            velocityX: state[2],
            velocityY: state[3],
            speed: sqrt(state[2] * state[2] + state[3] * state[3]),
            course: atan2(state[2], state[3]) * 180 / .pi,
            confidence: confidence,
            positionUncertainty: positionUncertainty,
            timestamp: timestamp,
            isPrediction: dt > 0.1
        )
    }

    /// Reset filter state
    public func reset() {
        lock.lock()
        defer { lock.unlock() }

        state = [0, 0, 0, 0]
        initializeCovariance()
        hasReference = false
        lastUpdateTime = nil
        isInitialized = false
    }

    /// Get current filtered estimate
    public func currentEstimate(timestamp: Date = Date()) -> FilteredPosition {
        let (lat, lon) = enuToLatLon(x: state[0], y: state[1])
        let speed = sqrt(state[2] * state[2] + state[3] * state[3])
        var course = atan2(state[2], state[3]) * 180 / .pi
        if course < 0 { course += 360 }

        let positionUncertainty = sqrt(covariance[0][0] + covariance[1][1])

        return FilteredPosition(
            latitude: lat,
            longitude: lon,
            velocityX: state[2],
            velocityY: state[3],
            speed: speed,
            course: course,
            confidence: 1.0,
            positionUncertainty: positionUncertainty,
            timestamp: timestamp,
            isPrediction: false
        )
    }

    // MARK: - Private Methods - Kalman Filter

    private func initializeWithMeasurement(
        x: Double,
        y: Double,
        accuracy: Double,
        speed: Double?,
        course: Double?,
        timestamp: Date
    ) -> FilteredPosition {
        state[0] = x
        state[1] = y

        if let speed = speed, let course = course, speed > 0.5 {
            state[2] = speed * sin(course * .pi / 180)
            state[3] = speed * cos(course * .pi / 180)
        } else {
            state[2] = 0
            state[3] = 0
        }

        // Initialize covariance based on measurement accuracy
        covariance[0][0] = accuracy * accuracy
        covariance[1][1] = accuracy * accuracy
        covariance[2][2] = 10  // Velocity uncertainty
        covariance[3][3] = 10

        lastUpdateTime = timestamp
        isInitialized = true

        return currentEstimate(timestamp: timestamp)
    }

    /// Prediction step: project state and covariance forward in time
    private func predict(dt: Double) {
        // State transition: x' = x + vx*dt, y' = y + vy*dt
        state[0] += state[2] * dt
        state[1] += state[3] * dt

        // State transition matrix F (applied to covariance)
        // F = [1 0 dt 0]
        //     [0 1 0 dt]
        //     [0 0 1  0]
        //     [0 0 0  1]

        // P' = F * P * F^T + Q
        // Simplified update for diagonal-dominant covariance:
        let dt2 = dt * dt

        // Position variance grows with velocity variance
        covariance[0][0] += covariance[2][2] * dt2 + configuration.positionProcessNoise
        covariance[1][1] += covariance[3][3] * dt2 + configuration.positionProcessNoise

        // Add cross-terms for position-velocity correlation
        covariance[0][2] += covariance[2][2] * dt
        covariance[2][0] = covariance[0][2]
        covariance[1][3] += covariance[3][3] * dt
        covariance[3][1] = covariance[1][3]

        // Velocity variance grows with process noise
        covariance[2][2] += configuration.velocityProcessNoise
        covariance[3][3] += configuration.velocityProcessNoise
    }

    /// Update step: incorporate position measurement
    private func updateWithPosition(x: Double, y: Double, noise: Double, dt: Double) {
        let R = noise * noise  // Measurement noise variance

        // Kalman gain for position (simplified for independent x,y)
        let Kx = covariance[0][0] / (covariance[0][0] + R)
        let Ky = covariance[1][1] / (covariance[1][1] + R)

        // Innovation (measurement residual)
        let innovationX = x - state[0]
        let innovationY = y - state[1]

        // State update
        state[0] += Kx * innovationX
        state[1] += Ky * innovationY

        // Also update velocity estimate based on position innovation.
        // Convert meters to m/s using dt; damp to avoid overreacting to GPS jumps.
        if dt > 0.05 {
            let damping = 0.5
            let impliedVx = (innovationX * Kx / dt) * damping
            let impliedVy = (innovationY * Ky / dt) * damping
            state[2] += impliedVx
            state[3] += impliedVy
        }

        // Covariance update (simplified Joseph form for stability)
        covariance[0][0] *= (1 - Kx)
        covariance[1][1] *= (1 - Ky)
        covariance[0][2] *= (1 - Kx)
        covariance[2][0] = covariance[0][2]
        covariance[1][3] *= (1 - Ky)
        covariance[3][1] = covariance[1][3]
    }

    /// Update step: incorporate velocity measurement
    private func updateWithVelocity(vx: Double, vy: Double, noise: Double) {
        let R = noise * noise

        let Kvx = covariance[2][2] / (covariance[2][2] + R)
        let Kvy = covariance[3][3] / (covariance[3][3] + R)

        state[2] += Kvx * (vx - state[2])
        state[3] += Kvy * (vy - state[3])

        covariance[2][2] *= (1 - Kvx)
        covariance[3][3] *= (1 - Kvy)
    }

    // MARK: - Coordinate Conversion

    /// Convert lat/lon to local ENU (East-North-Up) coordinates in meters
    private func latLonToENU(latitude: Double, longitude: Double) -> (x: Double, y: Double) {
        let earthRadius = 6_371_000.0  // meters

        let latRad = latitude * .pi / 180
        let lonRad = longitude * .pi / 180
        let refLatRad = referenceLatitude * .pi / 180
        let refLonRad = referenceLongitude * .pi / 180

        // East (x) - longitude difference scaled by cos(lat)
        let x = earthRadius * (lonRad - refLonRad) * cos(refLatRad)

        // North (y) - latitude difference
        let y = earthRadius * (latRad - refLatRad)

        return (x, y)
    }

    /// Convert local ENU coordinates back to lat/lon
    private func enuToLatLon(x: Double, y: Double) -> (latitude: Double, longitude: Double) {
        let earthRadius = 6_371_000.0

        let refLatRad = referenceLatitude * .pi / 180

        let latitude = referenceLatitude + (y / earthRadius) * 180 / .pi
        let longitude = referenceLongitude + (x / (earthRadius * cos(refLatRad))) * 180 / .pi

        return (latitude, longitude)
    }
}

// MARK: - Filtered Position

/// Result from Kalman filter - smoothed position estimate
public struct FilteredPosition: Codable, Equatable, Sendable {
    /// Filtered latitude in degrees
    public let latitude: Double

    /// Filtered longitude in degrees
    public let longitude: Double

    /// Velocity in East direction (m/s)
    public let velocityX: Double

    /// Velocity in North direction (m/s)
    public let velocityY: Double

    /// Speed magnitude (m/s)
    public let speed: Double

    /// Course/heading in degrees (0-360, 0=North)
    public let course: Double

    /// Confidence in estimate (0-1, based on filter convergence and prediction age)
    public let confidence: Double

    /// Estimated position uncertainty in meters
    public let positionUncertainty: Double

    /// Timestamp of this estimate
    public let timestamp: Date

    /// True if this is a predicted (extrapolated) position
    public let isPrediction: Bool

    /// Convert to LocationFix coordinate
    public var coordinate: LocationFix.Coordinate {
        LocationFix.Coordinate(latitude: latitude, longitude: longitude)
    }
}

// MARK: - Position Predictor Integration

extension PositionPredictor {

    /// Create a Kalman filter-backed predictor
    /// This provides smoother predictions than the default kinematic model
    public static func withKalmanFilter(configuration: GPSKalmanFilter.Configuration = GPSKalmanFilter.Configuration()) -> GPSKalmanFilter {
        GPSKalmanFilter(configuration: configuration)
    }
}
