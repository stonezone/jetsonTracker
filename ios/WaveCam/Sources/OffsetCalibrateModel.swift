import Foundation
import Observation

/// State + gating for the Calibration v2 offset-aim step. Its OWN model (not a branch in
/// MapPlacementModel — keeps that one scoped to placement+heading). Pure (no MapKit) so the
/// fix-quality / LoRa-freshness gating and the offset interpretation are unit-testable.
@Observable
final class OffsetCalibrateModel {
    enum OffsetBand { case small, moderate, large }

    // Quality thresholds for accepting a capture (review FU-2).
    static let minSats = 6
    static let maxHdop = 2.0
    static let maxFixAgeSec = 3.0
    static let maxLoraAgeSec = 5.0

    var baseLat: Double?
    var baseLon: Double?
    var baseHeightM: Double = 2.0
    var trackerLat: Double?
    var trackerLon: Double?

    // Live tracker fix quality + LoRa link freshness (fed from status).
    var sats: Int?
    var hdop: Double?
    var fixAgeSec: Double?
    var loraAgeSec: Double?      // nil => no packets from the remote at all

    var distanceM: Double? {
        guard let bla = baseLat, let blo = baseLon, let tla = trackerLat, let tlo = trackerLon else { return nil }
        return GeoMath.haversineMeters(fromLat: bla, fromLon: blo, toLat: tla, toLon: tlo)
    }
    var bearingDeg: Double? {
        guard let bla = baseLat, let blo = baseLon, let tla = trackerLat, let tlo = trackerLon else { return nil }
        return GeoMath.bearingDeg(fromLat: bla, fromLon: blo, toLat: tla, toLon: tlo)
    }

    /// Blocking reason, or nil when a capture is safe. Precedence distinguishes the three
    /// failure modes (FU-4) because their recovery differs: link down vs stale vs no GPS.
    var gateMessage: String? {
        if loraAgeSec == nil { return "No packets from the tracker — check the LoRa link." }
        if (loraAgeSec ?? 0) > Self.maxLoraAgeSec { return "Tracker fix is stale — wait for a fresh packet." }
        if trackerLat == nil || trackerLon == nil { return "Waiting for a GPS fix from the tracker…" }
        if (sats ?? 0) < Self.minSats || (hdop ?? 99) > Self.maxHdop || (fixAgeSec ?? 99) > Self.maxFixAgeSec {
            return "Weak fix (check sats / HDOP) — wait for a better lock."
        }
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
