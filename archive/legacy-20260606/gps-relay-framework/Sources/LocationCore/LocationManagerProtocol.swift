#if canImport(CoreLocation) && os(iOS)
import CoreLocation

/// Protocol abstraction over `CLLocationManager` used to keep the relay service portable and testable.
public protocol LocationManagerProtocol: AnyObject {
    var delegate: CLLocationManagerDelegate? { get set }
    var desiredAccuracy: CLLocationAccuracy { get set }
    var distanceFilter: CLLocationDistance { get set }
    var allowsBackgroundLocationUpdates: Bool { get set }
    @available(iOS 14.0, *)
    var authorizationStatus: CLAuthorizationStatus { get }

    func requestWhenInUseAuthorization()
    func startUpdatingLocation()
    func stopUpdatingLocation()
    func startUpdatingHeading()
    func stopUpdatingHeading()
}

extension CLLocationManager: LocationManagerProtocol {}
#else
public protocol LocationManagerProtocol: AnyObject {}
#endif
