import Foundation

/// Spherical geometry, matching the backend's `gps_geo` bearing/haversine so a
/// map-computed bearing equals what the rig computes for the same coordinates.
enum GeoMath {
    /// Forward azimuth from point 1 to point 2, in degrees [0, 360).
    static func bearingDeg(fromLat lat1: Double, fromLon lon1: Double,
                           toLat lat2: Double, toLon lon2: Double) -> Double {
        let p1 = lat1 * .pi / 180, p2 = lat2 * .pi / 180
        let dl = (lon2 - lon1) * .pi / 180
        let y = sin(dl) * cos(p2)
        let x = cos(p1) * sin(p2) - sin(p1) * cos(p2) * cos(dl)
        let deg = atan2(y, x) * 180 / .pi
        return (deg.truncatingRemainder(dividingBy: 360) + 360).truncatingRemainder(dividingBy: 360)
    }

    /// Great-circle distance in meters.
    static func haversineMeters(fromLat lat1: Double, fromLon lon1: Double,
                                toLat lat2: Double, toLon lon2: Double) -> Double {
        let r = 6_371_000.0
        let p1 = lat1 * .pi / 180, p2 = lat2 * .pi / 180
        let dp = (lat2 - lat1) * .pi / 180, dl = (lon2 - lon1) * .pi / 180
        let a = sin(dp/2) * sin(dp/2) + cos(p1) * cos(p2) * sin(dl/2) * sin(dl/2)
        return r * 2 * atan2(sqrt(a), sqrt(1 - a))
    }
}
