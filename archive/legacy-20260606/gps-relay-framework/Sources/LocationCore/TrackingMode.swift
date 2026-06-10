#if canImport(CoreLocation) && (os(iOS) || os(watchOS))
import Foundation
import CoreLocation

public struct LocationConfig: Equatable, Sendable {
    public let desiredAccuracy: CLLocationAccuracy
    public let distanceFilter: CLLocationDistance
    public let estimatedBatteryUsePerHour: Double
    public let description: String
    public let qualityThresholds: QualityThresholds

    public init(
        desiredAccuracy: CLLocationAccuracy,
        distanceFilter: CLLocationDistance,
        estimatedBatteryUsePerHour: Double,
        description: String,
        qualityThresholds: QualityThresholds
    ) {
        self.desiredAccuracy = desiredAccuracy
        self.distanceFilter = distanceFilter
        self.estimatedBatteryUsePerHour = estimatedBatteryUsePerHour
        self.description = description
        self.qualityThresholds = qualityThresholds
    }
}

public struct QualityThresholds: Equatable, Sendable {
    public let maxHorizontalAccuracy: CLLocationAccuracy
    public let maxAge: TimeInterval
    public let maxSpeed: CLLocationSpeed

    public init(
        maxHorizontalAccuracy: CLLocationAccuracy,
        maxAge: TimeInterval,
        maxSpeed: CLLocationSpeed
    ) {
        self.maxHorizontalAccuracy = maxHorizontalAccuracy
        self.maxAge = maxAge
        self.maxSpeed = maxSpeed
    }
}

public enum TrackingMode: String, CaseIterable, Codable, Sendable {
    case realtime
    case balanced
    case powersaver
    case minimal

    public var configuration: LocationConfig {
        switch self {
        case .realtime:
            // Issue #8: Tighter thresholds for precision gimbal tracking
            return LocationConfig(
                desiredAccuracy: kCLLocationAccuracyBest,
                distanceFilter: kCLDistanceFilterNone,
                estimatedBatteryUsePerHour: 15.0,
                description: "Best accuracy, continuous updates",
                qualityThresholds: QualityThresholds(
                    maxHorizontalAccuracy: 15,  // Was 30, tighter for gimbal
                    maxAge: 3,                   // Was 5, fresher for tracking
                    maxSpeed: 30                 // Was 83.3 (~186mph), now ~67mph - realistic for surfing
                )
            )
        case .balanced:
            // Issue #8: Tighter thresholds
            return LocationConfig(
                desiredAccuracy: kCLLocationAccuracyNearestTenMeters,
                distanceFilter: 10.0,
                estimatedBatteryUsePerHour: 8.0,
                description: "Good accuracy, suitable for most sessions",
                qualityThresholds: QualityThresholds(
                    maxHorizontalAccuracy: 30,   // Was 50
                    maxAge: 5,                    // Was 10
                    maxSpeed: 50                  // Was 83.3, now ~112mph
                )
            )
        case .powersaver:
            return LocationConfig(
                desiredAccuracy: kCLLocationAccuracyHundredMeters,
                distanceFilter: 50.0,
                estimatedBatteryUsePerHour: 4.0,
                description: "Reduced updates, optimized for long sessions",
                qualityThresholds: QualityThresholds(
                    maxHorizontalAccuracy: 100,
                    maxAge: 30,
                    maxSpeed: 83.3
                )
            )
        case .minimal:
            return LocationConfig(
                desiredAccuracy: kCLLocationAccuracyKilometer,
                distanceFilter: 500.0,
                estimatedBatteryUsePerHour: 1.0,
                description: "Minimal background tracking",
                qualityThresholds: QualityThresholds(
                    maxHorizontalAccuracy: 500,
                    maxAge: 60,
                    maxSpeed: 150
                )
            )
        }
    }
}
#endif
