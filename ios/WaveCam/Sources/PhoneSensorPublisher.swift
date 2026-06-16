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
    private let socket: PhoneSensorSocket

    private var publishTimer: Task<Void, Never>?
    private var running = false
    private var sensorsActive = false
    private var postFailingSince: Date?

    /// POSTs failing this long -> stop sensors (GPS Best + 20Hz motion are the
    /// battery cost; the 1Hz timer itself is negligible). Battery guard is now
    /// driven by actual POST outcome, not the status-poll `connected` flag, which
    /// could wedge the feed when a transient /status read failed (2026-06-16).
    private static let sensorIdleGrace: TimeInterval = 30

    // Latest sensor snapshots (written from callbacks, read on publish timer).
    // All callbacks and the timer run on MainActor so no explicit lock needed.
    private var latestHeadingDeg: Double? = nil
    private var latestHeadingAcc: Double = -1   // default: invalid
    private var latestLat: Double? = nil
    private var latestLon: Double? = nil
    private var latestHAcc: Double? = nil
    private var latestTrueHeadingDeg: Double? = nil
    private var latestAltM: Double? = nil
    private var latestAltAcc: Double? = nil
    private let altimeter = CMAltimeter()
    private var latestBaroRelM: Double? = nil

    // Bump detection state.
    private var bumpPending = false
    private var bumpStartTime: Date? = nil

    // MARK: - Init

    init(client: WaveCamClient) {
        self.client = client
        self.socket = PhoneSensorSocket(endpoint: { [client] in client.phoneSensorWSEndpoint })
        super.init()
        locationManager.delegate = self
    }

    // MARK: - Lifecycle

    func start() {
        guard !running else { return }
        running = true
        // Gather immediately while foregrounded. The POST is NOT gated on the
        // status-poll's `connected` flag — a transient /status failure must not
        // wedge the independent, idempotent sensor feed. Battery is protected by
        // stopping sensors after sustained POST failures (publish) + on background.
        if client.mode == .live { startSensors() }
        socket.open()
        startPublishTimer()
    }

    func stop() {
        guard running else { return }
        running = false
        publishTimer?.cancel()
        publishTimer = nil
        socket.close()
        stopSensors()
    }

    private func startSensors() {
        guard !sensorsActive else { return }
        sensorsActive = true
        // Location first: trueHeading needs an active location fix (gps-relay-framework order).
        startLocation()
        startHeading()
        startAccelerometer()
        startAltimeter()
    }

    private func stopSensors() {
        guard sensorsActive else { return }
        sensorsActive = false
        locationManager.stopUpdatingHeading()
        locationManager.stopUpdatingLocation()
        motionManager.stopDeviceMotionUpdates()
        altimeter.stopRelativeAltitudeUpdates()
    }

    // MARK: - Heading

    private func startHeading() {
        guard CLLocationManager.headingAvailable() else { return }
        // The phone mounts landscape on the rig; without this the heading is off by 90°.
        locationManager.headingOrientation = .landscapeRight
        // Clean restart: a bare startUpdatingHeading() is a no-op if CoreLocation already
        // considers heading "updating" but stopped delivering; stop→start forces a fresh start.
        locationManager.stopUpdatingHeading()
        locationManager.startUpdatingHeading()
    }

    // MARK: - Altimeter

    private func startAltimeter() {
        guard CMAltimeter.isRelativeAltitudeAvailable() else { return }
        altimeter.startRelativeAltitudeUpdates(to: .main) { [weak self] data, _ in
            guard let data else { return }
            self?.latestBaroRelM = data.relativeAltitude.doubleValue
        }
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
        guard motionManager.isDeviceMotionAvailable else { return }
        motionManager.deviceMotionUpdateInterval = 1.0 / Self.accelHz
        // deviceMotion separates gravity properly — the |raw|-1g proxy missed
        // horizontal bumps that rotate rather than add to the gravity vector
        // (review 2026-06-12).
        motionManager.startDeviceMotionUpdates(to: .main) { [weak self] data, _ in
            guard let self, let data else { return }
            let ua = data.userAcceleration
            let userMag = sqrt(ua.x * ua.x + ua.y * ua.y + ua.z * ua.z)
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
        // Stream over the persistent, self-healing websocket rather than a fire-and-forget
        // POST. Sent whenever foreground + live; the socket no-ops while disconnected and
        // reconnects on its own, so a flaky link recovers instead of silently dropping fixes.
        guard client.mode == .live else { return }

        // Heading can silently STOP delivering after a magnetometer disturbance or a
        // background cycle while the location stream keeps flowing (observed: GPS keeps
        // updating, compass goes stale). Re-assert it each tick — idempotent if already
        // running, a restart if it dropped. This is the robustness the proven
        // gps-relay-framework relies on (it re-calls startUpdatingHeading continuously).
        if sensorsActive, CLLocationManager.headingAvailable() {
            locationManager.startUpdatingHeading()
        }

        var body: [String: Any] = ["bump": bumpPending]
        bumpPending = false

        if let h = latestHeadingDeg { body["heading_deg"] = h }
        body["heading_acc"] = latestHeadingAcc
        if let lat = latestLat { body["lat"] = lat }
        if let lon = latestLon { body["lon"] = lon }
        if let hAcc = latestHAcc { body["h_acc"] = hAcc }
        if let th = latestTrueHeadingDeg { body["true_heading_deg"] = th }
        if let alt = latestAltM { body["alt_m"] = alt }
        if let altAcc = latestAltAcc { body["alt_acc"] = altAcc }
        if let baro = latestBaroRelM { body["baro_rel_m"] = baro }

        socket.send(body)
        client.notePhoneSensorStream(connected: socket.connected)

        // Battery guard on the live stream state: after a sustained disconnect (rig
        // unreachable) stop the high-power GPS Best + 20 Hz motion; reconnection restarts
        // them. startSensors() is idempotent.
        if socket.connected {
            postFailingSince = nil
            startSensors()
        } else {
            if postFailingSince == nil {
                postFailingSince = Date()
            } else if Date().timeIntervalSince(postFailingSince!) > Self.sensorIdleGrace {
                stopSensors()
            }
        }
    }
}

// MARK: - CLLocationManagerDelegate

extension PhoneSensorPublisher: CLLocationManagerDelegate {

    nonisolated func locationManager(_ manager: CLLocationManager,
                                     didUpdateHeading newHeading: CLHeading) {
        // headingAccuracy < 0 means invalid — pass it through as-is so the backend
        // can distinguish "no calibration" from a valid low-accuracy reading.
        Task { @MainActor [weak self] in
            guard let self else { return }
            // magneticHeading can be NaN on a bad read; NaN would fail JSON serialization and
            // drop the WHOLE fix, so treat it as absent (gps-relay-framework resolveHeading).
            let mag = newHeading.magneticHeading
            self.latestHeadingDeg = mag.isNaN ? nil : mag
            self.latestHeadingAcc = newHeading.headingAccuracy
            // trueHeading < 0 means no magnetic calibration yet — treat as absent.
            self.latestTrueHeadingDeg = newHeading.trueHeading >= 0 ? newHeading.trueHeading : nil
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager,
                                     didUpdateLocations locations: [CLLocation]) {
        guard let loc = locations.last else { return }
        Task { @MainActor [weak self] in
            guard let self else { return }
            // iOS sets horizontalAccuracy < 0 to flag an invalid fix, where lat/lon are
            // meaningless. A negative h_acc also violates the backend's ge=0 bound and 422s
            // the ENTIRE sample (dropping a valid heading with it), so drop the location
            // fields until the fix is valid rather than poison the whole POST.
            if loc.horizontalAccuracy >= 0 {
                self.latestLat = loc.coordinate.latitude
                self.latestLon = loc.coordinate.longitude
                self.latestHAcc = loc.horizontalAccuracy
            } else {
                self.latestLat = nil
                self.latestLon = nil
                self.latestHAcc = nil
            }
            // verticalAccuracy < 0 flags an invalid altitude.
            if loc.verticalAccuracy >= 0 {
                self.latestAltM = loc.altitude
                self.latestAltAcc = loc.verticalAccuracy
            } else {
                self.latestAltM = nil
                self.latestAltAcc = nil
            }
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
                    // Heading must be (re)started AFTER auth is granted — if startSensors() ran
                    // first (auth still .notDetermined) the initial startUpdatingHeading never
                    // delivered. Re-run it on the grant edge (the proposal's §6.1 fix).
                    self.startHeading()
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
