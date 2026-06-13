import Foundation
import CoreLocation
import CoreMotion
import HealthKit
import WatchConnectivity

// MARK: - Watch Session Record types

struct WatchGPSSample: Encodable {
    var kind = "gps"               // scorer dispatches on this
    let timestamp: Double          // unix seconds
    let lat: Double
    let lon: Double
    let h_acc: Double              // horizontal accuracy metres (negative = invalid)
    let speed: Double              // m/s, negative = invalid
    let course: Double             // degrees true, negative = invalid
}

struct WatchMotionSample: Encodable {
    var kind = "motion"            // scorer dispatches on this
    let timestamp: Double          // unix seconds
    let heading: Double?           // degrees true, nil if invalid (<0 from CMDeviceMotion)
    let accel_mag: Double          // |userAcceleration| in g
    let yaw: Double                // attitude yaw radians
}

// MARK: - Recorder

/// Records GPS + motion to a JSONL file for offline scoring against the shadow estimator.
/// One record type per line, tagged by "kind": "gps" or "motion".
@MainActor
final class WatchSessionRecorder: NSObject, ObservableObject {

    // MARK: Published state

    @Published private(set) var isRecording = false
    @Published private(set) var startPending = false
    @Published private(set) var gpsSampleCount = 0
    @Published private(set) var motionSampleCount = 0
    @Published private(set) var statusMessage: String = ""

    // MARK: Private

    private let healthStore = HKHealthStore()
    private let locationManager = CLLocationManager()
    private let motionManager = CMMotionManager()
    private var workoutSession: HKWorkoutSession?
    private var fileHandle: FileHandle?
    private var outputURL: URL?
    private var motionTimer: Timer?

    // GPS target: 1 Hz; motion target: 4 Hz
    private let gpsInterval: TimeInterval = 1.0
    private var lastGPSWrite: Date = .distantPast
    private let motionInterval: TimeInterval = 0.25

    // MARK: - Lifecycle

    override init() {
        super.init()
        locationManager.delegate = self
        locationManager.desiredAccuracy = kCLLocationAccuracyBest
        locationManager.distanceFilter = kCLDistanceFilterNone
    }

    // MARK: - Public API

    func startRecording() {
        // startPending closes the async re-entrancy window: isRecording only
        // flips true AFTER the HealthKit authorization round-trip, and the
        // first-run permission sheet holds that window open for seconds. A
        // second Start during it would leak a live HKWorkoutSession, an open
        // FileHandle, and a repeating Timer.
        guard !isRecording && !startPending else { return }
        startPending = true
        requestPermissionsAndStart()
    }

    func stopRecording() {
        if startPending {
            startPending = false   // cancels a pending start mid-authorization
            return
        }
        guard isRecording else { return }
        tearDown()
    }

    // MARK: - Permission + startup

    private func requestPermissionsAndStart() {
        // HKWorkoutSession requires NSHealthUpdateUsageDescription.
        // We only start a workout session for sensor background access;
        // we do not read or write health quantities.
        guard HKHealthStore.isHealthDataAvailable() else {
            startPending = false
            statusMessage = "HealthKit unavailable"
            return
        }
        // Workout type needed for HKWorkoutSession
        let workoutType = HKObjectType.workoutType()
        healthStore.requestAuthorization(toShare: [workoutType], read: []) { [weak self] ok, err in
            DispatchQueue.main.async {
                guard let self, self.startPending else { return }  // Stop tapped mid-auth
                // ok=true only means the request completed — check the actual grant.
                let granted = self.healthStore.authorizationStatus(for: workoutType) == .sharingAuthorized
                if ok && granted {
                    self.startWorkoutAndSensors()
                } else {
                    self.startPending = false
                    self.statusMessage = granted ? "HealthKit error" : "HealthKit denied"
                }
            }
        }
    }

    private func startWorkoutAndSensors() {
        // Workout session keeps wrist sensors running while the watch face is down.
        let config = HKWorkoutConfiguration()
        config.activityType = .surfingSports
        config.locationType = .outdoor

        do {
            let session = try HKWorkoutSession(healthStore: healthStore, configuration: config)
            session.delegate = self
            workoutSession = session
            session.startActivity(with: Date())
        } catch {
            startPending = false
            statusMessage = "Workout session error: \(error.localizedDescription)"
            return
        }

        openOutputFile()
        startLocation()
        startMotion()

        startPending = false
        // isRecording flips in the HKWorkoutSessionDelegate .running transition.
        gpsSampleCount = 0
        motionSampleCount = 0
        statusMessage = "Recording"
    }

    // MARK: - Output file

    private func openOutputFile() {
        let dir = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let ts = Int(Date().timeIntervalSince1970)
        let url = dir.appendingPathComponent("watch_session_\(ts).jsonl")
        outputURL = url
        FileManager.default.createFile(atPath: url.path, contents: nil)
        fileHandle = try? FileHandle(forWritingTo: url)
    }

    private func appendLine<T: Encodable>(_ sample: T) {
        guard let data = try? Self.encoder.encode(sample),
              let newline = "\n".data(using: .utf8),
              let fh = fileHandle else { return }
        fh.write(data)
        fh.write(newline)
    }

    private static let encoder = JSONEncoder()

    // MARK: - Location

    private func startLocation() {
        locationManager.requestWhenInUseAuthorization()
        locationManager.startUpdatingLocation()
    }

    // MARK: - Motion (4 Hz)

    private func startMotion() {
        guard motionManager.isDeviceMotionAvailable else { return }
        motionManager.deviceMotionUpdateInterval = motionInterval
        motionManager.startDeviceMotionUpdates(using: .xMagneticNorthZVertical)

        motionTimer = Timer.scheduledTimer(withTimeInterval: motionInterval, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.writeMotionSample()
            }
        }
    }

    private func writeMotionSample() {
        guard let dm = motionManager.deviceMotion else { return }
        let now = Date().timeIntervalSince1970
        let ua = dm.userAcceleration
        appendLine(WatchMotionSample(
            timestamp: now,
            heading: dm.heading >= 0 ? dm.heading : nil,
            accel_mag: sqrt(ua.x * ua.x + ua.y * ua.y + ua.z * ua.z),
            yaw: dm.attitude.yaw
        ))
        motionSampleCount += 1
    }

    // MARK: - Teardown

    private func tearDown() {
        motionTimer?.invalidate()
        motionTimer = nil
        motionManager.stopDeviceMotionUpdates()
        locationManager.stopUpdatingLocation()
        workoutSession?.end()
        workoutSession = nil
        fileHandle?.closeFile()
        fileHandle = nil
        isRecording = false
        statusMessage = "Stopped"

        if let url = outputURL {
            transferFileToPhone(url)
        }
    }

    // MARK: - WCSession transfer

    private func transferFileToPhone(_ url: URL) {
        guard WCSession.isSupported() else { return }
        let session = WCSession.default
        // Activate if not already active (done by WaveCamWatchApp at startup).
        // transferFile is queued and survives backgrounding.
        session.transferFile(url, metadata: ["kind": "watch_session"])
        statusMessage = "Sent to iPhone"
    }
}

// MARK: - CLLocationManagerDelegate

extension WatchSessionRecorder: CLLocationManagerDelegate {
    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let loc = locations.last else { return }
        let now = Date()
        // Throttle to ~1 Hz
        Task { @MainActor [weak self] in
            guard let self, self.isRecording else { return }
            guard now.timeIntervalSince(self.lastGPSWrite) >= self.gpsInterval else { return }
            self.lastGPSWrite = now
            self.appendLine(WatchGPSSample(
                timestamp: loc.timestamp.timeIntervalSince1970,
                lat: loc.coordinate.latitude,
                lon: loc.coordinate.longitude,
                h_acc: loc.horizontalAccuracy,
                speed: loc.speed,
                course: loc.course
            ))
            self.gpsSampleCount += 1
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        Task { @MainActor [weak self] in
            self?.statusMessage = "GPS error"
        }
    }
}

// MARK: - HKWorkoutSessionDelegate

extension WatchSessionRecorder: HKWorkoutSessionDelegate {
    nonisolated func workoutSession(_ workoutSession: HKWorkoutSession,
                                    didChangeTo toState: HKWorkoutSessionState,
                                    from fromState: HKWorkoutSessionState, date: Date) {
        // isRecording reflects the SESSION's truth, not our optimism: flip it
        // only when HealthKit confirms .running (review 2026-06-12).
        Task { @MainActor [weak self] in
            guard let self else { return }
            if toState == .running { self.isRecording = true }
            if toState == .ended || toState == .stopped { self.isRecording = false }
        }
    }

    nonisolated func workoutSession(_ workoutSession: HKWorkoutSession,
                                    didFailWithError error: Error) {
        Task { @MainActor [weak self] in
            self?.statusMessage = "Workout error: \(error.localizedDescription)"
        }
    }
}
