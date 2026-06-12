import CoreLocation
import CoreMotion
import Foundation

/// Phase-3 T3.1: phone-on-tripod sensor publisher.
///
/// While the app is foregrounded and connected to the Orin, this object
/// POSTs to /api/v1/sensors/phone at 1 Hz:
///   - CLLocationManager heading (magneticHeading + headingAccuracy)
///   - CLLocationManager location (lat/lon/horizontalAccuracy) — when-in-use
///   - bump=true when CMMotionManager detects userAcceleration magnitude >1.2g
///     sustained <0.3s (one-shot flag cleared after each POST)
///
/// The backend ignores posts when sensors.enabled=false, so the publisher
/// does not need to know the server-side toggle — it always posts when
/// connected in live mode.
///
/// Denial of location or heading permission degrades silently — we post
/// whatever fields are available. CLHeading.headingAccuracy < 0 signals
/// invalid heading to the backend per the iOS convention.
///
/// Called from WaveCamApp on scene phase changes; `start()` / `stop()`
/// are idempotent.
@MainActor
final class PhoneSensorPublisher: NSObject {

    // MARK: - Configuration

    /// Accelerometer update interval (20 Hz).
    private static let accelHz: Double = 20.0

    /// Bump threshold: userAcceleration magnitude in g-forces.
    private static let bumpThreshold: Double = 1.2

    /// Maximum bump duration to count as an impulse (not a sustained lean).
    private static let bumpMaxDuration: TimeInterval = 0.3

    /// Publish interval (1 Hz).
    private static let publishInterval: TimeInterval = 1.0

    // MARK: - State

    private let client: WaveCamClient
    private let locationManager = CLLocationManager()
    private let motionManager = CMMotionManager()

    private var publishTimer: Task<Void, Never>?
    private var running = false

    // Latest sensor snapshots (written from callbacks, read on publish timer).
    // All callbacks and the timer run on MainActor so no explicit lock needed.
    private var latestHeadingDeg: Double? = nil
    private var latestHeadingAcc: Double = -1   // default: invalid
    private var latestLat: Double? = nil
    private var latestLon: Double? = nil
    private var latestHAcc: Double? = nil

    // Bump detection state.
    private var bumpPending = false
    private var bumpStartTime: Date? = nil

    // MARK: - Init

    init(client: WaveCamClient) {
        self.client = client
        super.init()
        locationManager.delegate = self
    }

    // MARK: - Lifecycle

    func start() {
        guard !running else { return }
        running = true
        startHeading()
        startLocation()
        startAccelerometer()
        startPublishTimer()
    }

    func stop() {
        guard running else { return }
        running = false
        publishTimer?.cancel()
        publishTimer = nil
        locationManager.stopUpdatingHeading()
        locationManager.stopUpdatingLocation()
        motionManager.stopAccelerometerUpdates()
    }

    // MARK: - Heading

    private func startHeading() {
        guard CLLocationManager.headingAvailable() else { return }
        locationManager.startUpdatingHeading()
    }

    // MARK: - Location

    private func startLocation() {
        let status = locationManager.authorizationStatus
        switch status {
        case .notDetermined:
            // Request once; if denied the delegate will not start updates.
            locationManager.requestWhenInUseAuthorization()
        case .authorizedWhenInUse, .authorizedAlways:
            locationManager.desiredAccuracy = kCLLocationAccuracyBest
            locationManager.startUpdatingLocation()
        default:
            // Denied or restricted — degrade silently (no location fields posted).
            break
        }
    }

    // MARK: - Accelerometer

    private func startAccelerometer() {
        guard motionManager.isAccelerometerAvailable else { return }
        motionManager.accelerometerUpdateInterval = 1.0 / Self.accelHz
        motionManager.startAccelerometerUpdates(to: .main) { [weak self] data, _ in
            guard let self, let data else { return }
            let acc = data.acceleration
            let mag = sqrt(acc.x * acc.x + acc.y * acc.y + acc.z * acc.z)
            // Subtract 1g gravity (device at rest = ~1g total).
            let userMag = abs(mag - 1.0)
            if userMag > Self.bumpThreshold {
                if self.bumpStartTime == nil {
                    self.bumpStartTime = Date()
                }
            } else {
                if let start = self.bumpStartTime {
                    let duration = Date().timeIntervalSince(start)
                    if duration < Self.bumpMaxDuration {
                        self.bumpPending = true
                    }
                }
                self.bumpStartTime = nil
            }
        }
    }

    // MARK: - 1 Hz publish

    private func startPublishTimer() {
        publishTimer?.cancel()
        publishTimer = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(Self.publishInterval))
                await self?.publish()
            }
        }
    }

    private func publish() async {
        guard client.connected else { return }
        var body: [String: Any] = ["bump": bumpPending]
        bumpPending = false

        body["heading_deg"] = latestHeadingDeg
        body["heading_acc"] = latestHeadingAcc
        if let lat = latestLat { body["lat"] = lat }
        if let lon = latestLon { body["lon"] = lon }
        if let hAcc = latestHAcc { body["h_acc"] = hAcc }

        await client.postPhoneSensor(body)
    }
}

// MARK: - CLLocationManagerDelegate

extension PhoneSensorPublisher: CLLocationManagerDelegate {

    nonisolated func locationManager(_ manager: CLLocationManager,
                                     didUpdateHeading newHeading: CLHeading) {
        // headingAccuracy < 0 means invalid — pass it through as-is so the backend
        // can distinguish "no calibration" from a valid low-accuracy reading.
        Task { @MainActor [weak self] in
            self?.latestHeadingDeg = newHeading.magneticHeading
            self?.latestHeadingAcc = newHeading.headingAccuracy
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager,
                                     didUpdateLocations locations: [CLLocation]) {
        guard let loc = locations.last else { return }
        Task { @MainActor [weak self] in
            self?.latestLat = loc.coordinate.latitude
            self?.latestLon = loc.coordinate.longitude
            self?.latestHAcc = loc.horizontalAccuracy
        }
    }

    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        Task { @MainActor [weak self] in
            guard let self else { return }
            switch manager.authorizationStatus {
            case .authorizedWhenInUse, .authorizedAlways:
                if self.running {
                    manager.desiredAccuracy = kCLLocationAccuracyBest
                    manager.startUpdatingLocation()
                }
            default:
                // Denied or restricted — clear any stale position data.
                self.latestLat = nil
                self.latestLon = nil
                self.latestHAcc = nil
            }
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager,
                                     didFailWithError error: Error) {
        // Degrade silently — the publisher continues with whatever fields are valid.
    }
}
