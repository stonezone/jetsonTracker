import Foundation
import LocationCore

#if os(watchOS)
import CoreLocation
import HealthKit
import WatchConnectivity
import WatchKit

public protocol WatchLocationProviderDelegate: AnyObject {
    func didProduce(_ fix: LocationFix)
    func didFail(_ error: Error)
    func didUpdateMetrics(_ metrics: WatchDirectTransport.Metrics)
}

/// Manages workout-driven location capture on watchOS and relays fixes to the phone.
public final class WatchLocationProvider: NSObject, @unchecked Sendable {
    public weak var delegate: WatchLocationProviderDelegate?

    private let workoutStore = HKHealthStore()
    private let locationManager = CLLocationManager()
    private var workoutSession: HKWorkoutSession?
    private var workoutBuilder: HKLiveWorkoutBuilder?
    private var wcSession: WCSession { WCSession.default }
    private let encoder = JSONEncoder()
    private let fileManager = FileManager.default
    private var lastContextSequence: Int?
    private var lastContextPushDate: Date?
    private var lastContextAccuracy: Double?

    // APPLICATION CONTEXT CONFIGURATION
    // Application context is for SNAPSHOT data, not streaming. Apple throttles aggressively.
    // Real-time streaming uses: Bluetooth (sendMessageData) or Direct LTE WebSocket
    // Context serves as a backup snapshot that survives app restarts
    private let contextPushInterval: TimeInterval = 10.0  // Relaxed: real-time via direct transport, context is backup only
    private let contextAccuracyDelta: Double = 5.0  // Was: 2.0m, less aggressive for snapshots
    private var activeFileTransfers: [WCSessionFileTransfer: (url: URL, fix: LocationFix)] = [:]
    private let transferLock = NSLock()  // Thread safety for activeFileTransfers

    // Context update failure tracking (Issue #1)
    private var consecutiveContextFailures: Int = 0
    private let maxContextFailures: Int = 5
    private var contextUpdatesDisabled: Bool = false

    // Performance tracking
    private var fixCount: Int = 0
    private var sessionStartTime: Date?

    // Issue #21: File transfer exponential backoff
    private var fileTransferRetryCount: Int = 0
    private let maxFileTransferRetries: Int = 5
    private let baseRetryDelay: TimeInterval = 0.5

    // MARK: - Direct WebSocket Transport (LTE Bypass)
    // When iPhone is not reachable via Bluetooth, send directly to server over LTE
    // This avoids WCSession's iCloud relay which has 10-60s latency
    private var directTransport: WatchDirectTransport?
    private var useDirectTransportWhenAvailable: Bool = true

    /// Statistics for transport path usage
    public private(set) var bluetoothSendCount: Int = 0
    public private(set) var directLTESendCount: Int = 0
    public private(set) var fileTransferSendCount: Int = 0

    public override init() {
        super.init()
        WKInterfaceDevice.current().isBatteryMonitoringEnabled = true
        locationManager.delegate = self
        encoder.outputFormatting = [.withoutEscapingSlashes]
    }

    // MARK: - Direct Transport Configuration

    /// Configure direct WebSocket connection to server for LTE bypass.
    /// Call this before startWorkoutAndStreaming() to enable direct mode.
    /// - Parameters:
    ///   - serverURL: WebSocket URL for direct connection (e.g., wss://your-server.com/watch)
    ///   - bearerToken: Optional authentication token
    ///   - deviceId: Optional device identifier for server-side tracking
    public func configureDirectTransport(
        serverURL: URL,
        bearerToken: String? = nil,
        deviceId: String? = nil
    ) {
        var config = WatchDirectTransport.Configuration()
        config.serverURL = serverURL
        config.bearerToken = bearerToken
        config.deviceId = deviceId ?? UUID().uuidString

        directTransport = WatchDirectTransport(configuration: config)
        directTransport?.onStateChanged = { [weak self] state in
            print("[WatchLocationProvider] Direct transport state: \(state)")
            self?.handleDirectTransportStateChange(state)
        }
        directTransport?.onError = { error in
            print("[WatchLocationProvider] Direct transport error: \(error.localizedDescription)")
            // Direct LTE/WebSocket is an optional low-latency path. Do not mark
            // the workout as failed while WatchConnectivity/file transfer can
            // still deliver fixes through the phone.
        }
        directTransport?.onMetricsChanged = { [weak self] metrics in
            self?.delegate?.didUpdateMetrics(metrics)
        }

        print("[WatchLocationProvider] Direct transport configured for \(serverURL.absoluteString)")
    }

    /// Enable or disable direct transport usage when iPhone is not reachable
    public func setDirectTransportEnabled(_ enabled: Bool) {
        useDirectTransportWhenAvailable = enabled
        print("[WatchLocationProvider] Direct transport enabled: \(enabled)")
    }

    private func handleDirectTransportStateChange(_ state: WatchDirectTransport.ConnectionState) {
        // Could notify delegate or update UI about connection mode
    }

    public func startWorkoutAndStreaming(activity: HKWorkoutActivityType = .other) {
        requestAuthorizationsIfNeeded()
        startWorkoutSession(activity: activity)
        // Extended runtime session requires special entitlement - omitting for now
        // The workout session itself keeps the app active
        configureWatchConnectivity()

        // Open direct transport if configured (for LTE bypass)
        if useDirectTransportWhenAvailable, let transport = directTransport, transport.isConfigured {
            transport.open()
            print("[WatchLocationProvider] Direct transport opened for LTE bypass")
        }

        // Configure for maximum update frequency
        locationManager.activityType = .other  // .other provides most frequent updates
        locationManager.desiredAccuracy = kCLLocationAccuracyBest
        locationManager.distanceFilter = kCLDistanceFilterNone
        // watchOS doesn't need allowsBackgroundLocationUpdates - the workout session handles this

        locationManager.startUpdatingLocation()
    }

    public func stop() {
        locationManager.stopUpdatingLocation()

        // Close direct transport if open
        directTransport?.close()

        // Only end workout if it's actually running
        if workoutSession?.state == .running {
            workoutSession?.end()
        }
        workoutBuilder?.endCollection(withEnd: Date()) { [weak self] _, error in
            if let error {
                self?.delegate?.didFail(error)
            }
            self?.workoutBuilder?.finishWorkout { _, finishError in
                if let finishError {
                    self?.delegate?.didFail(finishError)
                }
            }
        }
        workoutSession = nil
        workoutBuilder = nil
        lastContextSequence = nil
        lastContextPushDate = nil
        lastContextAccuracy = nil
        transferLock.lock()
        activeFileTransfers.removeAll()
        transferLock.unlock()

        // Issue #1: Reset context failure tracking
        consecutiveContextFailures = 0
        contextUpdatesDisabled = false

        // Issue #21: Reset file transfer retry state
        fileTransferRetryCount = 0

        // Log transport usage statistics
        print("[WatchLocationProvider] Session stats - Bluetooth: \(bluetoothSendCount), Direct LTE: \(directLTESendCount), File: \(fileTransferSendCount)")
    }

    private func requestAuthorizationsIfNeeded() {
        var readTypes: Set<HKObjectType> = []
        if let heartRate = HKObjectType.quantityType(forIdentifier: .heartRate) {
            readTypes.insert(heartRate)
        }
        workoutStore.requestAuthorization(toShare: [], read: readTypes) { _, _ in }

        // Request maximum available accuracy for workout GPS capture
        locationManager.requestWhenInUseAuthorization()
    }

    private func startWorkoutSession(activity: HKWorkoutActivityType) {
        let configuration = HKWorkoutConfiguration()
        configuration.activityType = activity
        configuration.locationType = .outdoor
        do {
            let session = try HKWorkoutSession(healthStore: workoutStore, configuration: configuration)
            let builder = session.associatedWorkoutBuilder()
            builder.dataSource = HKLiveWorkoutDataSource(healthStore: workoutStore, workoutConfiguration: configuration)
            session.delegate = self
            builder.delegate = self
            session.startActivity(with: Date())
            builder.beginCollection(withStart: Date()) { [weak self] _, error in
                if let error {
                    self?.delegate?.didFail(error)
                }
            }
            workoutSession = session
            workoutBuilder = builder
        } catch {
            delegate?.didFail(error)
        }
    }

    private func configureWatchConnectivity() {
        if WCSession.isSupported() {
            print("[WatchLocationProvider] Activating WCSession")
            wcSession.delegate = self
            wcSession.activate()
        } else {
            print("[WatchLocationProvider] WCSession not supported")
        }
    }

    private func publishFix(_ fix: LocationFix) {
        delegate?.didProduce(fix)
        print("[WatchLocationProvider] Session state: \(wcSession.activationState.rawValue), reachable: \(wcSession.isReachable)")

        // Transport priority for "Jetson-first" vision:
        // 1. Direct WebSocket over LTE - primary path for latency & independence
        // 2. Bluetooth (sendMessageData) - backup/debug
        // 3. File transfer - final fallback

        // PATH 1: Direct Transport (Primary, but only while actually connected)
        if useDirectTransportWhenAvailable,
           let transport = directTransport,
           transport.connectionState == .connected {
            print("[WatchLocationProvider] Using direct LTE transport (Primary)")
            transport.push(fix)
            directLTESendCount += 1

            // Avoid duplicate live sends: do not also send the same fix over WCSession.
            updateApplicationContextWithFix(fix)
            return
        }

        if useDirectTransportWhenAvailable,
           let transport = directTransport,
           transport.isConfigured {
            print("[WatchLocationProvider] Direct LTE not connected (\(transport.connectionState)); using phone relay fallback")
        }

        // PATH 2: Bluetooth (Fallback/Debug)
        // Used when direct transport is unavailable or reconnecting.
        if wcSession.activationState == .activated && wcSession.isReachable {
            sendViaBluetooth(fix)
        } else {
            // PATH 3: File Transfer (Deep Fallback)
            print("[WatchLocationProvider] Not reachable, using file transfer fallback")
            queueBackgroundTransfer(for: fix)
            fileTransferSendCount += 1
        }

        // Always update application context as a backup snapshot (throttled)
        updateApplicationContextWithFix(fix)
    }

    /// Send fix via Bluetooth (WCSession interactive messaging)
    private func sendViaBluetooth(_ fix: LocationFix) {
        do {
            let data = try encoder.encode(fix)
            let sendTime = Date()
            print("[WatchLocationProvider] Sending via Bluetooth (\(data.count) bytes)")

            // Issue #2: Add delivery confirmation with reply handler
            wcSession.sendMessageData(data, replyHandler: { [weak self] reply in
                // Delivery confirmed - calculate round-trip time
                let rtt = Date().timeIntervalSince(sendTime) * 1000
                print("[WatchLocationProvider] ✓ Bluetooth delivered, RTT: \(String(format: "%.0f", rtt))ms")
                self?.bluetoothSendCount += 1
            }) { [weak self] error in
                print("[WatchLocationProvider] Bluetooth send failed: \(error.localizedDescription)")
                // Fall back to direct transport or file transfer
                self?.handleBluetoothSendFailure(fix: fix)
            }
        } catch {
            print("[WatchLocationProvider] Encode error: \(error.localizedDescription)")
            delegate?.didFail(error)
            handleBluetoothSendFailure(fix: fix)
        }
    }

    /// Handle Bluetooth send failure - try direct transport, then file transfer
    private func handleBluetoothSendFailure(fix: LocationFix) {
        if useDirectTransportWhenAvailable,
           let transport = directTransport,
           transport.connectionState == .connected {
            print("[WatchLocationProvider] Bluetooth failed, falling back to direct LTE")
            transport.push(fix)
            directLTESendCount += 1
        } else {
            print("[WatchLocationProvider] Bluetooth failed, falling back to file transfer")
            queueBackgroundTransfer(for: fix)
            fileTransferSendCount += 1
        }
    }

    /// Send via direct transport if available (even without WCSession)
    private func sendViaDirectTransportIfAvailable(_ fix: LocationFix) {
        if useDirectTransportWhenAvailable,
           let transport = directTransport,
           transport.connectionState == .connected {
            print("[WatchLocationProvider] Using direct LTE transport (WCSession unavailable)")
            transport.push(fix)
            directLTESendCount += 1
        }
    }

    private func updateApplicationContextWithFix(_ fix: LocationFix) {
        guard wcSession.activationState == .activated else { return }

        // Issue #1: Skip context updates if disabled due to repeated failures
        guard !contextUpdatesDisabled else {
            // Still use file transfer as backup
            queueBackgroundTransfer(for: fix)
            return
        }

        let now = Date()
        if lastContextSequence == fix.sequence {
            return
        }

        if let lastPush = lastContextPushDate,
           now.timeIntervalSince(lastPush) < contextPushInterval,
           let lastAccuracy = lastContextAccuracy,
           abs(lastAccuracy - fix.horizontalAccuracyMeters) < contextAccuracyDelta {
            return
        }
        do {
            let data = try encoder.encode(fix)
            let metadata: [String: Any] = [
                "seq": fix.sequence,
                "timestamp": fix.timestamp.timeIntervalSince1970,
                "accuracy": fix.horizontalAccuracyMeters
            ]
            let context: [String: Any] = [
                "latestFix": data,
                "metadata": metadata
            ]
            try wcSession.updateApplicationContext(context)

            // Issue #1: Reset failure counter on success
            consecutiveContextFailures = 0
            print("[WatchLocationProvider] Updated application context with latest fix")
            lastContextSequence = fix.sequence
            lastContextPushDate = now
            lastContextAccuracy = fix.horizontalAccuracyMeters
        } catch {
            // Issue #1: Track failures and disable after threshold
            consecutiveContextFailures += 1
            print("[WatchLocationProvider] Context update failed (\(consecutiveContextFailures)/\(maxContextFailures)): \(error.localizedDescription)")

            if consecutiveContextFailures >= maxContextFailures {
                contextUpdatesDisabled = true
                print("[WatchLocationProvider] ⚠️ Context updates disabled after \(maxContextFailures) failures, using file transfer only")
            }

            // Always queue file transfer as backup on failure
            queueBackgroundTransfer(for: fix)
        }
    }

    private func queueBackgroundTransfer(for fix: LocationFix) {
        guard wcSession.activationState == .activated else { return }
        do {
            let data = try encoder.encode(fix)
            let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            try data.write(to: url)
            let transfer = wcSession.transferFile(url, metadata: ["sequence": fix.sequence])
            transferLock.lock()
            activeFileTransfers[transfer] = (url, fix)
            transferLock.unlock()
            print("[WatchLocationProvider] Queued file transfer")
        } catch {
            delegate?.didFail(error)
        }
    }
}

extension WatchLocationProvider: CLLocationManagerDelegate {
    public func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let latest = locations.last else { return }

        // Track performance
        fixCount += 1
        let now = Date()
        if sessionStartTime == nil { sessionStartTime = now }

        // Log update rate periodically
        if fixCount % 10 == 0, let start = sessionStartTime {
            let elapsed = now.timeIntervalSince(start)
            let rate = Double(fixCount) / elapsed
            print("[WatchLocationProvider] Performance: \(fixCount) fixes in \(String(format: "%.1f", elapsed))s = \(String(format: "%.2f", rate)) Hz")
        }

        let device = WKInterfaceDevice.current()
        device.isBatteryMonitoringEnabled = true
        let fix = LocationFix(
            timestamp: latest.timestamp,
            source: .watchOS,
            coordinate: .init(latitude: latest.coordinate.latitude, longitude: latest.coordinate.longitude),
            altitudeMeters: latest.verticalAccuracy >= 0 ? latest.altitude : nil,
            horizontalAccuracyMeters: latest.horizontalAccuracy,
            verticalAccuracyMeters: max(latest.verticalAccuracy, 0),
            speedMetersPerSecond: max(latest.speed, 0),
            courseDegrees: latest.course >= 0 ? latest.course : 0,
            headingDegrees: nil,  // Apple Watch doesn't have compass
            batteryFraction: device.batteryLevel >= 0 ? Double(device.batteryLevel) : 0,
            sequence: AtomicSequenceGenerator.shared.next()  // Issue #3: Use atomic generator
        )
        publishFix(fix)
    }

    public func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        delegate?.didFail(error)
    }
}

extension WatchLocationProvider: WCSessionDelegate {
    public func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {
        print("[WatchLocationProvider] WCSession activation completed with state: \(activationState.rawValue), error: \(error?.localizedDescription ?? "none")")
        if let error {
            delegate?.didFail(error)
        }
    }

    public func sessionReachabilityDidChange(_ session: WCSession) {
        // Intentionally left blank; reachability is checked during send.
    }

#if os(watchOS)
    public func session(_ session: WCSession, didReceiveMessageData messageData: Data) {}
    public func session(_ session: WCSession, didReceive file: WCSessionFile) {}

    public func session(_ session: WCSession, didFinish fileTransfer: WCSessionFileTransfer, error: Error?) {
        transferLock.lock()
        let record = activeFileTransfers.removeValue(forKey: fileTransfer)
        transferLock.unlock()

        guard let record = record else { return }
        defer { try? fileManager.removeItem(at: record.url) }

        if let error {
            // Issue #21: Exponential backoff on file transfer failures
            fileTransferRetryCount += 1

            if fileTransferRetryCount > maxFileTransferRetries {
                print("[WatchLocationProvider] File transfer failed after \(maxFileTransferRetries) retries, giving up: \(error.localizedDescription)")
                fileTransferRetryCount = 0  // Reset for next transfer
                return
            }

            let delay = baseRetryDelay * pow(2.0, Double(fileTransferRetryCount - 1))
            print("[WatchLocationProvider] File transfer failed (attempt \(fileTransferRetryCount)/\(maxFileTransferRetries)): \(error.localizedDescription). Retrying in \(String(format: "%.1f", delay))s…")

            // Schedule retry with exponential backoff
            DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
                self?.queueBackgroundTransfer(for: record.fix)
            }
        } else {
            print("[WatchLocationProvider] File transfer completed successfully")
            fileTransferRetryCount = 0  // Reset on success
        }
    }
#endif
}

extension WatchLocationProvider: HKWorkoutSessionDelegate {
    public func workoutSession(_ workoutSession: HKWorkoutSession, didChangeTo toState: HKWorkoutSessionState, from fromState: HKWorkoutSessionState, date: Date) {
        guard toState == .ended || toState == .stopped else { return }
        workoutBuilder?.endCollection(withEnd: date) { [weak self] _, error in
            if let error {
                self?.delegate?.didFail(error)
            }
            self?.workoutBuilder?.finishWorkout { _, finishError in
                if let finishError {
                    self?.delegate?.didFail(finishError)
                }
            }
        }
    }

    public func workoutSession(_ workoutSession: HKWorkoutSession, didFailWithError error: Error) {
        delegate?.didFail(error)
    }
}

extension WatchLocationProvider: HKLiveWorkoutBuilderDelegate {
    public func workoutBuilder(_ workoutBuilder: HKLiveWorkoutBuilder, didCollectDataOf collectedTypes: Set<HKSampleType>) {}

    public func workoutBuilderDidCollectEvent(_ workoutBuilder: HKLiveWorkoutBuilder) {}
}

#else

public protocol WatchLocationProviderDelegate: AnyObject {
    func didProduce(_ fix: LocationFix)
    func didFail(_ error: Error)
}

public final class WatchLocationProvider {
    public weak var delegate: WatchLocationProviderDelegate?

    public init() {}

    public func startWorkoutAndStreaming(activity: Int = 0) {
        assertionFailure("WatchLocationProvider is only available on watchOS")
    }

    public func stop() {}
}
#endif
