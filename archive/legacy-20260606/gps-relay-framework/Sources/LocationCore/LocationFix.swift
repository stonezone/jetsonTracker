import Foundation

public struct LocationFix: Codable, Equatable, Sendable {
    public enum Source: String, Codable, Sendable {
        case watchOS
        case iOS
    }

    public struct Coordinate: Codable, Equatable, Sendable {
        public let latitude: Double
        public let longitude: Double

        public init(latitude: Double, longitude: Double) {
            self.latitude = latitude
            self.longitude = longitude
        }
    }

    public let timestamp: Date
    public let source: Source
    public let coordinate: Coordinate
    public let altitudeMeters: Double?
    public let horizontalAccuracyMeters: Double
    public let verticalAccuracyMeters: Double
    public let speedMetersPerSecond: Double
    public let courseDegrees: Double
    public let headingDegrees: Double?  // Magnetic heading (compass direction device is pointing)
    public let batteryFraction: Double
    public let sequence: Int

    public init(
        timestamp: Date,
        source: Source,
        coordinate: Coordinate,
        altitudeMeters: Double?,
        horizontalAccuracyMeters: Double,
        verticalAccuracyMeters: Double,
        speedMetersPerSecond: Double,
        courseDegrees: Double,
        headingDegrees: Double?,
        batteryFraction: Double,
        sequence: Int
    ) {
        self.timestamp = timestamp
        self.source = source
        self.coordinate = coordinate
        self.altitudeMeters = altitudeMeters
        self.horizontalAccuracyMeters = horizontalAccuracyMeters
        self.verticalAccuracyMeters = verticalAccuracyMeters
        self.speedMetersPerSecond = speedMetersPerSecond
        self.courseDegrees = courseDegrees
        self.headingDegrees = headingDegrees
        self.batteryFraction = batteryFraction
        self.sequence = sequence
    }

    private enum CodingKeys: String, CodingKey {
        case timestamp = "ts_unix_ms"
        case source
        case latitude = "lat"
        case longitude = "lon"
        case altitudeMeters = "alt_m"
        case horizontalAccuracyMeters = "h_accuracy_m"
        case verticalAccuracyMeters = "v_accuracy_m"
        case speedMetersPerSecond = "speed_mps"
        case courseDegrees = "course_deg"
        case headingDegrees = "heading_deg"
        case batteryFraction = "battery_pct"
        case sequence = "seq"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let timestampMilliseconds = try container.decode(Int64.self, forKey: .timestamp)
        self.timestamp = Date(timeIntervalSince1970: Double(timestampMilliseconds) / 1_000)
        self.source = try container.decode(Source.self, forKey: .source)
        let latitude = try container.decode(Double.self, forKey: .latitude)
        let longitude = try container.decode(Double.self, forKey: .longitude)
        self.coordinate = Coordinate(latitude: latitude, longitude: longitude)
        self.altitudeMeters = try container.decodeIfPresent(Double.self, forKey: .altitudeMeters)
        self.horizontalAccuracyMeters = try container.decode(Double.self, forKey: .horizontalAccuracyMeters)
        self.verticalAccuracyMeters = try container.decode(Double.self, forKey: .verticalAccuracyMeters)
        self.speedMetersPerSecond = try container.decode(Double.self, forKey: .speedMetersPerSecond)
        self.courseDegrees = try container.decode(Double.self, forKey: .courseDegrees)
        self.headingDegrees = try container.decodeIfPresent(Double.self, forKey: .headingDegrees)
        self.batteryFraction = try container.decode(Double.self, forKey: .batteryFraction)
        self.sequence = try container.decode(Int.self, forKey: .sequence)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        let milliseconds = Int64(timestamp.timeIntervalSince1970 * 1_000)
        try container.encode(milliseconds, forKey: .timestamp)
        try container.encode(source, forKey: .source)
        try container.encode(coordinate.latitude, forKey: .latitude)
        try container.encode(coordinate.longitude, forKey: .longitude)
        try container.encodeIfPresent(altitudeMeters, forKey: .altitudeMeters)
        try container.encode(horizontalAccuracyMeters, forKey: .horizontalAccuracyMeters)
        try container.encode(verticalAccuracyMeters, forKey: .verticalAccuracyMeters)
        try container.encode(speedMetersPerSecond, forKey: .speedMetersPerSecond)
        try container.encode(courseDegrees, forKey: .courseDegrees)
        try container.encodeIfPresent(headingDegrees, forKey: .headingDegrees)
        try container.encode(batteryFraction, forKey: .batteryFraction)
        try container.encode(sequence, forKey: .sequence)
    }
}

public struct LocationFixPayload: Decodable {
    public let ts_unix_ms: Int
    public let source: String
    public let lat: Double
    public let lon: Double
    public let alt_m: Double?
    public let h_accuracy_m: Double
    public let v_accuracy_m: Double
    public let speed_mps: Double
    public let course_deg: Double
    public let battery_pct: Double
    public let seq: Int
}

public enum RelayHealth: Equatable, Sendable {
    case idle
    case streaming
    case degraded(reason: String)
}

// Issue #13: Latency tracking for end-to-end pipeline monitoring
public struct LatencyInfo: Codable, Equatable, Sendable {
    /// Time from GPS fix timestamp to when fix was received by relay service (ms)
    public var gpsToRelayMs: Double?
    
    /// Time from relay service receive to transport push (ms)
    public var relayToTransportMs: Double?
    
    /// WebSocket round-trip latency (ms)
    public var transportRttMs: Double?
    
    /// Total end-to-end latency from GPS timestamp to server receipt (ms)
    public var totalMs: Double?
    
    public init(
        gpsToRelayMs: Double? = nil,
        relayToTransportMs: Double? = nil,
        transportRttMs: Double? = nil,
        totalMs: Double? = nil
    ) {
        self.gpsToRelayMs = gpsToRelayMs
        self.relayToTransportMs = relayToTransportMs
        self.transportRttMs = transportRttMs
        self.totalMs = totalMs
    }
}

public struct RelayUpdate: Codable, Equatable, Sendable {
    public var base: LocationFix?
    public var remote: LocationFix?
    public var fused: LocationFix?
    
    // Issue #13: End-to-end latency tracking
    public var latency: LatencyInfo?
    
    /// Timestamp when this update was created (for server-side latency calculation)
    public var relayTimestamp: Date?

    public init(base: LocationFix? = nil, remote: LocationFix? = nil, fused: LocationFix? = nil, latency: LatencyInfo? = nil) {
        self.base = base
        self.remote = remote
        self.fused = fused
        self.latency = latency
        self.relayTimestamp = Date()
    }
}
