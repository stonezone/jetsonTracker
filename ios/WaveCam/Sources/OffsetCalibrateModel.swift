import Foundation
import Observation

/// State + gating for the Calibration v2 offset-aim step. Its OWN model (not a branch in
/// MapPlacementModel — keeps that one scoped to placement+heading). Pure (no MapKit) so the
/// fix-quality gating and the offset interpretation are unit-testable. Inputs come from
/// `status.gps` (distance/bearing/sats/age/stale); the raw tracker lat/lon isn't in status,
/// so the tracker pin is derived via GeoMath.destination, and the offset itself uses the
/// backend's own live fix (the handler falls back to it when no coords are sent).
@Observable
final class OffsetCalibrateModel {
    enum OffsetBand { case small, moderate, large }

    static let minSats = 6
    static let minDistanceM = 30.0      // want ~50-100 m; warn below 30

    var baseLat: Double?
    var baseLon: Double?
    var baseHeightM: Double = 2.0

    // From status.gps:
    var targetSats: Int?
    var targetAgeSec: Double?           // nil => no fix/packets from the remote yet
    var stale: Bool?                    // backend's authoritative staleness flag
    var distanceM: Double?              // base -> tracker
    var bearingDeg: Double?             // base -> tracker

    /// Tracker position for the dual-pin map, derived from base + status distance/bearing
    /// (status has no raw tracker coords).
    var trackerCoord: (lat: Double, lon: Double)? {
        guard let bla = baseLat, let blo = baseLon, let d = distanceM, let b = bearingDeg else { return nil }
        return GeoMath.destination(fromLat: bla, fromLon: blo, bearingDeg: b, distanceM: d)
    }

    /// Blocking reason, or nil when a capture is safe. Distinguishes failure modes (FU-4)
    /// because their recovery differs: no fix vs stale vs weak vs too-close.
    var gateMessage: String? {
        if targetAgeSec == nil && distanceM == nil { return "No fix from the tracker yet — give it open sky." }
        if stale == true { return "Tracker fix is stale — wait for a fresh packet." }
        if (targetSats ?? 0) < Self.minSats { return "Weak fix (few satellites) — wait for a better lock." }
        if (distanceM ?? 0) < Self.minDistanceM { return "Move the tracker farther out (≈50–100 m) for a clean offset." }
        return nil
    }

    var canCapture: Bool { gateMessage == nil }

    /// Interpretation band for the offset readout (FU-7): small = compass was good,
    /// moderate = expected, large = warn (tracker too close, mis-aim, or wrong base height).
    func offsetBand(_ offsetDeg: Double) -> OffsetBand {
        let a = abs(offsetDeg)
        if a <= 5 { return .small }
        if a <= 15 { return .moderate }
        return .large
    }
}
