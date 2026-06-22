import Foundation
import Observation

/// State + guards for map-based base placement and heading. Pure (no UIKit/MapKit)
/// so it is unit-testable; the MapKit view feeds it coordinates and tile-load events.
@Observable
final class MapPlacementModel {
    enum Mode { case base, headingLookAt, headingArrow }

    static let minLookAtMeters = 50.0   // V2: a short look-at produces a bad heading
    static let radiusFloorM = 2.5
    static let slopPoints = 8.0         // assumed placement slop, in screen points

    var mode: Mode = .base
    var baseLat: Double?
    var baseLon: Double?
    var lookAtLat: Double?
    var lookAtLon: Double?
    var arrowBearingDeg: Double = 0
    var tilesLoaded = false
    /// Last error radius computed from the live map zoom (the view updates this on region change).
    var lastErrorRadiusM = MapPlacementModel.radiusFloorM

    /// Calibration v2: how high the camera is above the SURFACE the subject sits on
    /// (the water for surf; the ground for a tracker test) — NOT altitude above sea level.
    /// Tilt depression is camera-vs-subject height only. Default ~1.5 m (tripod on the beach).
    var baseHeightM: Double = 1.5
    /// Manual coordinate entry (alternative to dropping the pin). Decimal degrees.
    var manualLatText: String = ""
    var manualLonText: String = ""
    /// Manual heading entry (deg true) — the primary heading path (phone compass / nav).
    var manualHeadingDeg: Double?

    /// Parsed manual coordinate, or nil if either field is blank/invalid/out of range.
    var parsedManualCoord: (lat: Double, lon: Double)? {
        guard let la = Double(manualLatText.trimmingCharacters(in: .whitespaces)),
              let lo = Double(manualLonText.trimmingCharacters(in: .whitespaces)),
              (-90.0...90.0).contains(la), (-180.0...180.0).contains(lo) else { return nil }
        return (la, lo)
    }

    /// Predicted tilt depression (deg, negative = down) at a given range for the entered
    /// base height — shown live so an implausible height is caught as the operator types.
    func predictedDepressionDeg(atMeters d: Double) -> Double {
        GeoMath.elevationDeg(baseAltM: baseHeightM, distanceM: d)
    }

    var lookAtDistanceM: Double? {
        guard let bla = baseLat, let blo = baseLon, let lla = lookAtLat, let llo = lookAtLon else { return nil }
        return GeoMath.haversineMeters(fromLat: bla, fromLon: blo, toLat: lla, toLon: llo)
    }
    var lookAtBearingDeg: Double? {
        guard let bla = baseLat, let blo = baseLon, let lla = lookAtLat, let llo = lookAtLon else { return nil }
        return GeoMath.bearingDeg(fromLat: bla, fromLon: blo, toLat: lla, toLon: llo)
    }
    var isLookAtValid: Bool { (lookAtDistanceM ?? 0) >= Self.minLookAtMeters }

    /// V4: error radius scales with map zoom; never the optimistic fixed 3 m.
    func errorRadiusM(metersAcross: Double, screenWidthPoints: Double) -> Double {
        let metersPerPoint = metersAcross / max(screenWidthPoints, 1)
        return max(Self.radiusFloorM, metersPerPoint * Self.slopPoints)
    }

    var canConfirmLocation: Bool { baseLat != nil && baseLon != nil && tilesLoaded }
    var canConfirmHeading: Bool {
        guard tilesLoaded else { return false }   // V5
        switch mode {
        case .headingLookAt: return isLookAtValid   // V2
        case .headingArrow: return true
        case .base: return false
        }
    }
}
