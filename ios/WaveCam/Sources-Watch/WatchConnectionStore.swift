import Foundation

/// Stores the Orin bearer token and base URLs synced from the paired iPhone via
/// WCSession.updateApplicationContext. WatchClient reads from this at request time.
/// Defaults fall back to the hardcoded tether/Wi-Fi addresses so the watch works
/// out of the box before the first phone sync.
@MainActor
final class WatchConnectionStore {
    static let shared = WatchConnectionStore()
    private init() {}

    var token: String?
    var tetherURLString: String = "http://172.20.10.8:8088/api/v1"
    var wifiURLString: String   = "http://192.168.1.155:8088/api/v1"

    var tetherURL: URL { URL(string: tetherURLString) ?? URL(string: "http://172.20.10.8:8088/api/v1")! }
    var wifiURL:   URL { URL(string: wifiURLString)   ?? URL(string: "http://192.168.1.155:8088/api/v1")! }

    /// Apply values received from the iPhone application context.
    func apply(_ context: [String: Any]) {
        if let t = context["wavecam_auth_token"] as? String {
            token = t.isEmpty ? nil : t
        }
        if let s = context["wavecam_tether_url"] as? String, !s.isEmpty {
            tetherURLString = s
        }
        if let s = context["wavecam_wifi_url"] as? String, !s.isEmpty {
            wifiURLString = s
        }
    }
}
