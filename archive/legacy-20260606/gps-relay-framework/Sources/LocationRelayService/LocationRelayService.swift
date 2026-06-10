import Foundation
#if canImport(LocationCore)
import LocationCore
#else
// Lightweight shims to allow compilation when LocationCore isn't available.
// These mirror only what's needed by LocationRelayService.
public struct LocationCoordinate: Codable, Equatable {
    public var latitude: Double
    public var longitude: Double
    public init(latitude: Double, longitude: Double) {
        self.latitude = latitude
        self.longitude = longitude
    }
}

public enum LocationSource: String, Codable {
    case iOS
    case watchOS
}

public struct LocationFix: Codable, Equatable {
    public var timestamp: Date
    public var source: LocationSource
    public var coordinate: LocationCoordinate
    public var altitudeMeters: Double?
    public var horizontalAccuracyMeters: Double
    public var verticalAccuracyMeters: Double
    public var speedMetersPerSecond: Double
    public var courseDegrees: Double
    public var headingDegrees: Double?
    public var batteryFraction: Double
    public var sequence: Int
    public init(timestamp: Date, source: LocationSource, coordinate: LocationCoordinate, altitudeMeters: Double?, horizontalAccuracyMeters: Double, verticalAccuracyMeters: Double, speedMetersPerSecond: Double, courseDegrees: Double, headingDegrees: Double?, batteryFraction: Double, sequence: Int) {
        self.timestamp = timestamp
        self.source = source
        self.coordinate = coordinate
        self.altitudeMeters = altitudeMeters
        self.horizontalAccuracyMeters = horizontalAccuracyMeters
        self.verticalAccuracyMeters = verticalAccuracyMeters
        self.speedMetersPerSecond = speedMetersPerSecond
        self.courseDegrees = courseDegrees
        self.headingDegrees = headingDegrees
        self.batteryFraction = batteryFraction
        self.sequence = sequence
    }
}

public enum RelayHealth: Equatable {
    case idle
    case streaming
    case degraded(reason: String)
}

public struct QualityThresholds: Equatable {
    public let maxHorizontalAccuracy: Double
    public let maxAge: TimeInterval
    public let maxSpeed: Double
}

public struct LocationConfig {
    public let desiredAccuracy: Double
    public let distanceFilter: Double
    public let estimatedBatteryUsePerHour: Double
    public let description: String
    public let qualityThresholds: QualityThresholds
}

public enum TrackingMode: String, CaseIterable {
    case realtime
    case balanced
    case powersaver
    case minimal

    public var configuration: LocationConfig {
        let thresholds = QualityThresholds(maxHorizontalAccuracy: 100, maxAge: 10, maxSpeed: 83.3)
        return LocationConfig(
            desiredAccuracy: 10,
            distanceFilter: 10,
            estimatedBatteryUsePerHour: 8,
            description: "",
            qualityThresholds: thresholds
        )
    }
}


public protocol LocationTransport: Sendable {
    func open()
    func close()
    func push(_ update: RelayUpdate)
}

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
#endif

public enum LocationRelayError: Error, Equatable {
    case authorizationDenied
    case authorizationRestricted
    case locationServicesDisabled
    case accuracyReduced
}

extension LocationRelayError: LocalizedError {
    public var errorDescription: String? {
        switch self {
        case .authorizationDenied:
            return "Location access denied. Enable in Settings > Privacy > Location Services."
        case .authorizationRestricted:
            return "Location access restricted. Check Screen Time or device management settings."
        case .locationServicesDisabled:
            return "Location Services disabled system-wide. Enable them in Settings."
        case .accuracyReduced:
            return "Precise location is disabled. Enable it in Settings for best tracking."
        }
    }
}

#if os(iOS)
import CoreLocation
import WatchConnectivity
import UIKit

public protocol LocationRelayDelegate: AnyObject {
    func didUpdate(_ update: RelayUpdate)
    func healthDidChange(_ health: RelayHealth)
    func watchConnectionDidChange(_ isConnected: Bool)
    func authorizationDidFail(_ error: LocationRelayError)
}

public extension LocationRelayDelegate {
    func authorizationDidFail(_ error: LocationRelayError) {}
}


public final class LocationRelayService: NSObject, @unchecked Sendable {
    public weak var delegate: LocationRelayDelegate? {
        didSet {
            guard delegate !== oldValue else { return }
            let currentHealth = health
            let watchState = isWatchConnected
            let latestUpdate = currentUpdate
            Task { @MainActor [weak self] in
                guard let self, let delegate = self.delegate else { return }
                delegate.healthDidChange(currentHealth)
                delegate.watchConnectionDidChange(watchState)
                if let latestUpdate {
                    delegate.didUpdate(latestUpdate)
                }
            }
        }
    }

    private let locationManager: LocationManagerProtocol
    public var trackingMode: TrackingMode {
        didSet {
            guard oldValue != trackingMode else { return }
            applyTrackingMode()
        }
    }
    public var qualityOverride: QualityThresholds?
    public var effectiveQualityThresholds: QualityThresholds {
        activeQualityThresholds
    }

    /// Helper to enable/disable watch relaying (default: false for Jetson-first vision)
    public var isWatchRelayEnabled: Bool = false {
        didSet {
            print("[LocationRelayService] Watch relay enabled: \(isWatchRelayEnabled)")
        }
    }

    private let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .millisecondsSince1970
        return decoder
    }()
    private let decoderLock = NSLock()

    private func decodeFix(from data: Data) -> LocationFix? {
        decoderLock.lock()
        defer { decoderLock.unlock() }
        return try? decoder.decode(LocationFix.self, from: data)
    }
    private var lastWatchFixDate: Date?
    private var currentHeading: CLHeading?  // Track latest compass heading
    private var transports: [LocationTransport] = []
    private var health: RelayHealth = .idle {
        didSet {
            guard oldValue != health else { return }
            let newHealth = health
            Task { @MainActor [weak self, newHealth] in
                guard let delegate = self?.delegate else { return }
                delegate.healthDidChange(newHealth)
            }
        }
    }
    private var isWatchConnected: Bool = false {
        didSet {
            guard oldValue != isWatchConnected else { return }
            
            // Track connectivity transitions
            connectivityTransitions += 1
            
            // Log transition with structured prefix
            let transitionType = isWatchConnected ? "CONNECTED" : "DISCONNECTED"
            print("[CONNECTIVITY] Watch \(transitionType) (transition #\(connectivityTransitions))")
            
            let newState = isWatchConnected
            Task { @MainActor [weak self, newState] in
                guard let delegate = self?.delegate else { return }
                delegate.watchConnectionDidChange(newState)
            }
        }
    }
    private var watchSilenceTimer: Timer?
    private var baseSnapshotHeartbeatTimer: Timer?
    private var isPhoneLocationActive = false
    private var canStartLocationAfterAuth = false
    private var backgroundActivitySession: AnyObject?

    private var latestWatchFix: LocationFix?
    private var latestPhoneFix: LocationFix?
    private let fusionWindow: TimeInterval = 5
    private var lastSequenceBySource: [LocationFix.Source: Int] = [:]

    private struct PendingWatchMessage {
        let id: UUID
        let data: Data
        var retryCount: Int
        let firstFailureDate: Date
    }

    private let watchRetryQueue = DispatchQueue(label: "com.iostracker.locationRelay.watchRetryQueue")
    private var pendingWatchMessages: [UUID: PendingWatchMessage] = [:]
    private var pendingRetryWorkItems: [UUID: DispatchWorkItem] = [:]
    private let maxPendingMessages = 100
    private let maxPendingMessageAge: TimeInterval = 45
    private let baseRetryDelay: TimeInterval = 0.5
    private let maxRetryDelay: TimeInterval = 5
    private let maxWatchRetryAttempts = 3

    private var phoneSpeedSamples: [Double] = []
    private let phoneSpeedWindowSize = 12
    private var isInLowPowerMode = false
    private let baseSnapshotHeartbeatInterval: TimeInterval = 1.0

    private var fixTimestampsBySource: [LocationFix.Source: [Date]] = [:]
    private let healthWindow: TimeInterval = 10
    private var lastHealthLogTime: Date?
    
    // Issue #20: Compass heading rate limiting
    private var lastHeadingPublishTime: Date?
    private let minHeadingInterval: TimeInterval = 0.5  // Max 2Hz heading updates
    private let minHeadingChangeDegrees: Double = 2.0   // Min change to publish

    // MARK: - Telemetry Metrics (Phase 4.2)
    
    /// Total number of duplicate fixes detected and rejected
    private(set) var duplicateFixCount: Int = 0
    
    /// Total number of watch messages dropped (all reasons)
    private(set) var totalDroppedMessages: Int = 0
    
    /// Categorized drop counts by reason
    private(set) var dropReasons: [String: Int] = [:]
    
    /// Peak queue depth observed during session
    private(set) var peakQueueDepth: Int = 0
    
    /// Total number of watch connectivity transitions (connect/disconnect events)
    private(set) var connectivityTransitions: Int = 0

    /// Location fusion mode for combining multiple GPS sources.
    ///
    /// - Warning: For robot cameraman use cases, fusion should remain **disabled**.
    ///   Fusion creates a geographic midpoint between sources, which is wrong when
    ///   tracking a subject (watch) relative to a base station (phone/camera).
    ///   The robot needs `Vector = Subject - Base`, not the midpoint.
    ///
    /// - Note: Fusion is only appropriate when multiple trackers are on the **same subject**
    ///   (e.g., phone + watch both worn by the same person for redundancy).
    public enum FusionMode: Sendable {
        /// No fusion - base and remote are treated as independent sources (default)
        /// Use this for robot cameraman: base = camera position, remote = subject position
        case disabled

        /// Weighted average fusion based on GPS accuracy
        /// Only use when multiple devices track the SAME physical subject
        case weightedAverage
    }

    /// Fusion mode for combining base and remote locations.
    /// Default is `.disabled` - appropriate for robot cameraman tracking.
    ///
    /// - Warning: Do not enable fusion for robot cameraman use cases.
    ///   See `FusionMode` documentation for details.
    public var fusionMode: FusionMode = .disabled

    private(set) var currentUpdate: RelayUpdate?

    private var activeQualityThresholds: QualityThresholds {
        qualityOverride ?? trackingMode.configuration.qualityThresholds
    }

    public init(locationManager: LocationManagerProtocol = CLLocationManager(),
                trackingMode: TrackingMode = .balanced) {
        self.locationManager = locationManager
        self.trackingMode = trackingMode
        super.init()
        locationManager.delegate = self
        applyTrackingMode()
        configureWatchSession()
        Task { @MainActor in
            UIDevice.current.isBatteryMonitoringEnabled = true
        }
        let timer = Timer(timeInterval: 5, repeats: true) { [weak self] _ in
            self?.evaluateWatchSilence()
        }
        RunLoop.main.add(timer, forMode: .common)
        watchSilenceTimer = timer
    }

    public func start() {
        canStartLocationAfterAuth = true
        resetWatchRetryState()
        phoneSpeedSamples.removeAll()
        isInLowPowerMode = false
        fixTimestampsBySource.removeAll()

        // Reset telemetry metrics for new session
        duplicateFixCount = 0
        totalDroppedMessages = 0
        dropReasons.removeAll()
        peakQueueDepth = 0
        connectivityTransitions = 0

        requestAuthorizations()
        transports.forEach { $0.open() }
        startBaseSnapshotHeartbeat()
        if CLLocationManager.locationServicesEnabled(), isAuthorized(currentAuthorizationStatus()), !isPhoneLocationActive {
            startPhoneLocation()
        }
    }

    public func stop() {
        stopBaseSnapshotHeartbeat()
        stopPhoneLocation()
        transports.forEach { $0.close() }
        transports.removeAll()
        health = .idle
        watchSilenceTimer?.invalidate()
        watchSilenceTimer = nil
        canStartLocationAfterAuth = false
        latestWatchFix = nil
        latestPhoneFix = nil
        currentUpdate = nil
        lastSequenceBySource.removeAll()
        resetWatchRetryState()
        phoneSpeedSamples.removeAll()
        isInLowPowerMode = false
        fixTimestampsBySource.removeAll()

        // Reset metrics (keep values for telemetry access until next start)
        // Note: Metrics are NOT reset here to allow post-session telemetry access
    }

    public func currentSnapshot() -> RelayUpdate? {
        currentUpdate
    }

    public var currentFix: LocationFix? {
        currentUpdate?.remote ?? currentUpdate?.base ?? currentUpdate?.fused
    }

    @available(*, deprecated, message: "Use currentSnapshot() to access base/remote data")
    public func currentFixValue() -> LocationFix? {
        currentUpdate?.remote ?? currentUpdate?.base ?? currentUpdate?.fused
    }

    public func addTransport(_ transport: LocationTransport) {
        transports.append(transport)
        transport.open()
    }

    private func requestAuthorizations() {
        Task { @MainActor [weak self] in
            guard let self else { return }
            // Request authorization and rely on delegate callbacks to proceed.
            self.locationManager.requestWhenInUseAuthorization()
        }
    }

    private func configureWatchSession() {
        guard WCSession.isSupported() else {
            print("[LocationRelayService] WCSession not supported")
            return
        }
        print("[LocationRelayService] Activating WCSession")
        let session = WCSession.default
        session.delegate = self
        session.activate()
    }

    private func handleInboundFix(_ fix: LocationFix) {
        if fix.source == .watchOS && !isWatchRelayEnabled {
            // Drop watch fixes if relay is disabled (Jetson-first mode)
            return
        }

        let timeSkew = fix.timestamp.timeIntervalSinceNow
        if timeSkew > 15 {
            print("[LocationRelayService] Dropped fix seq=\(fix.sequence) due to future timestamp (+\(String(format: "%.1f", timeSkew))s)")
            return
        }

        if fix.source == .watchOS && !shouldAcceptWatchFix(fix) {
            print("[LocationRelayService] Rejected watch fix seq=\(fix.sequence) due to quality thresholds")
            return
        }

        if let lastSeq = lastSequenceBySource[fix.source] {
            if lastSeq == fix.sequence {
                duplicateFixCount += 1
                print("[DEDUPE] Duplicate fix rejected for \(fix.source) seq=\(fix.sequence) (total duplicates: \(duplicateFixCount))")
                return
            }
            if fix.sequence < lastSeq {
                // Late/out-of-order deliveries (especially from file transfer) can cause gimbal jumps.
                // For real-time tracking, prefer monotonic sequences.
                print("[DEDUPE] Out-of-order fix rejected for \(fix.source) seq=\(fix.sequence) < last=\(lastSeq)")
                return
            }
            if fix.source == .watchOS && fix.sequence > lastSeq + 1 {
                print("[LocationRelayService] Warning: gap detected in watch sequences (last=\(lastSeq), current=\(fix.sequence))")
            }
        }
        lastSequenceBySource[fix.source] = fix.sequence

        if fix.source == .watchOS {
            lastWatchFixDate = Date()
        }

        switch fix.source {
        case .watchOS:
            latestWatchFix = fix
        case .iOS:
            latestPhoneFix = fix
        }

        recordFixTimestamp(for: fix.source)

        // Fusion Disabled: robot cameraman tracks a single subject,
        // so we avoid averaging positions between phone and watch.
        let baseFix = baseFixForSnapshot()
        let snapshot = RelayUpdate(base: baseFix, remote: latestWatchFix, fused: nil)
        let transportSnapshot = RelayUpdate(
            base: fix.source == .iOS ? baseFix : nil,
            remote: fix.source == .watchOS ? latestWatchFix : nil,
            fused: nil
        )

        Task { @MainActor [weak self, snapshot] in
            guard let delegate = self?.delegate else { return }
            delegate.didUpdate(snapshot)
        }
        currentUpdate = snapshot
        transports.forEach { $0.push(transportSnapshot) }
        updateHealth()
        logStreamHealthIfNeeded(reason: "fix seq=\(fix.sequence)")
    }

    private func baseFixForSnapshot(now: Date = Date()) -> LocationFix? {
        if let refreshed = refreshedBaseFixIfNeeded(now: now) {
            recordFixTimestamp(for: .iOS)
            return refreshed
        }
        return latestPhoneFix
    }

    private func refreshedBaseFixIfNeeded(now: Date = Date()) -> LocationFix? {
        guard let baseFix = latestPhoneFix else { return nil }

        let age = now.timeIntervalSince(baseFix.timestamp)
        guard age > baseSnapshotHeartbeatInterval else {
            return nil
        }

        let batteryLevel = UIDevice.current.batteryLevel >= 0 ? Double(UIDevice.current.batteryLevel) : baseFix.batteryFraction
        let refreshed = LocationFix(
            timestamp: now,
            source: .iOS,
            coordinate: baseFix.coordinate,
            altitudeMeters: baseFix.altitudeMeters,
            horizontalAccuracyMeters: baseFix.horizontalAccuracyMeters,
            verticalAccuracyMeters: baseFix.verticalAccuracyMeters,
            speedMetersPerSecond: baseFix.speedMetersPerSecond,
            courseDegrees: baseFix.courseDegrees,
            headingDegrees: resolveHeading(from: currentHeading) ?? baseFix.headingDegrees,
            batteryFraction: batteryLevel,
            sequence: AtomicSequenceGenerator.shared.next()
        )
        latestPhoneFix = refreshed
        return refreshed
    }

    private func startBaseSnapshotHeartbeat() {
        stopBaseSnapshotHeartbeat()

        let timer = Timer(timeInterval: baseSnapshotHeartbeatInterval, repeats: true) { [weak self] _ in
            self?.publishBaseStationHeartbeat()
        }
        RunLoop.main.add(timer, forMode: .common)
        baseSnapshotHeartbeatTimer = timer
    }

    private func stopBaseSnapshotHeartbeat() {
        baseSnapshotHeartbeatTimer?.invalidate()
        baseSnapshotHeartbeatTimer = nil
    }

    private func publishBaseStationHeartbeat() {
        guard canStartLocationAfterAuth else { return }
        guard let refreshed = refreshedBaseFixIfNeeded() else { return }
        handleInboundFix(refreshed)
    }

    private func updateHealth() {
        let now = Date()
        let watchIsFresh: Bool
        if let watchDate = lastWatchFixDate {
            watchIsFresh = now.timeIntervalSince(watchDate) <= 10
        } else {
            watchIsFresh = false
        }

        let status: CLAuthorizationStatus
        if #available(iOS 14.0, *) {
            status = locationManager.authorizationStatus
        } else {
            status = CLLocationManager.authorizationStatus()
        }

        let servicesEnabled = CLLocationManager.locationServicesEnabled()

        if watchIsFresh {
            health = .streaming
            if isAuthorized(status) && canStartLocationAfterAuth && !isPhoneLocationActive {
                startPhoneLocation()
            }
            return
        }

        if !servicesEnabled {
            health = .degraded(reason: "Location Services disabled")
            return
        }

        if status == .denied || status == .restricted {
            health = .degraded(reason: "Location permission denied")
            return
        }

        if lastWatchFixDate == nil {
            health = .degraded(reason: "Awaiting watch GPS")
        } else {
            health = .degraded(reason: "Watch GPS not updating")
        }

        if isAuthorized(status) && canStartLocationAfterAuth && !isPhoneLocationActive {
            startPhoneLocation()
        }
    }

    private func evaluateWatchSilence() {
        let now = Date()
        // Use 10 second window to avoid false disconnection due to timer timing jitter
        if let watchDate = lastWatchFixDate, now.timeIntervalSince(watchDate) <= 10 {
            // Watch is actively sending data
            isWatchConnected = true
        } else {
            // Watch has stopped sending data or never sent any
            isWatchConnected = false
        }
        updateHealth()
    }

    private func applyTrackingMode() {
        let config = trackingMode.configuration
        locationManager.desiredAccuracy = config.desiredAccuracy
        locationManager.distanceFilter = config.distanceFilter
    }

    private func currentAuthorizationStatus() -> CLAuthorizationStatus {
        if #available(iOS 14.0, *) {
            return locationManager.authorizationStatus
        } else {
            return CLLocationManager.authorizationStatus()
        }
    }

    private func notifyAuthorizationFailure(_ error: LocationRelayError) {
        health = .degraded(reason: error.localizedDescription ?? "Authorization issue")
        Task { @MainActor [weak self, error] in
            self?.delegate?.authorizationDidFail(error)
        }
    }

    private func isAuthorized(_ status: CLAuthorizationStatus) -> Bool {
        switch status {
        case .authorizedAlways, .authorizedWhenInUse:
            return true
        default:
            return false
        }
    }

    private func handleAuthorizationChange(status: CLAuthorizationStatus, accuracy: CLAccuracyAuthorization?) {
        // Ensure health reflects current state when authorization changes
        updateHealth()

        switch status {
        case .authorizedAlways, .authorizedWhenInUse:
            if #available(iOS 13.0, *) {
                locationManager.allowsBackgroundLocationUpdates = true
            }
            if #available(iOS 14.0, *), let accuracy, accuracy == .reducedAccuracy {
                notifyAuthorizationFailure(.accuracyReduced)
            } else {
                updateHealth()
            }
            if self.canStartLocationAfterAuth && CLLocationManager.locationServicesEnabled() {
                self.startPhoneLocation()
            }
        case .restricted:
            notifyAuthorizationFailure(.authorizationRestricted)
        case .denied:
            notifyAuthorizationFailure(.authorizationDenied)
        default:
            break
        }
    }

    private func shouldAccept(_ location: CLLocation) -> Bool {
        return shouldAccept(location, for: .iOS)
    }

    private func shouldAccept(_ location: CLLocation, for source: LocationFix.Source) -> Bool {
        let thresholds = activeQualityThresholds
        guard location.horizontalAccuracy >= 0 else { return false }

        let accuracyLimit = source == .iOS ? thresholds.maxHorizontalAccuracy * 2 : thresholds.maxHorizontalAccuracy
        guard location.horizontalAccuracy <= accuracyLimit else { return false }

        let age = abs(location.timestamp.timeIntervalSinceNow)
        guard age <= thresholds.maxAge else { return false }

        if source == .watchOS, location.speed >= 0, location.speed > thresholds.maxSpeed {
            return false
        }

        return true
    }

    private func shouldAcceptWatchFix(_ fix: LocationFix) -> Bool {
        let thresholds = activeQualityThresholds
        guard fix.horizontalAccuracyMeters <= thresholds.maxHorizontalAccuracy else { return false }

        let age = abs(fix.timestamp.timeIntervalSinceNow)
        guard age <= thresholds.maxAge else { return false }

        if fix.speedMetersPerSecond >= 0, fix.speedMetersPerSecond > thresholds.maxSpeed {
            return false
        }

        return true
    }

    private func startPhoneLocation() {
        guard !isPhoneLocationActive else { return }
        guard canStartLocationAfterAuth else { return }
        isPhoneLocationActive = true
        if #available(iOS 15.0, *) {
            let session = CLBackgroundActivitySession()
            backgroundActivitySession = session
        }
        applyTrackingMode()
        locationManager.startUpdatingLocation()
        // Start heading updates to get compass direction
        if CLLocationManager.headingAvailable() {
            locationManager.startUpdatingHeading()
        }
    }

    private func stopPhoneLocation() {
        guard isPhoneLocationActive else { return }
        isPhoneLocationActive = false
        locationManager.stopUpdatingLocation()
        locationManager.stopUpdatingHeading()
        if #available(iOS 15.0, *), let session = backgroundActivitySession as? CLBackgroundActivitySession {
            session.invalidate()
        }
        backgroundActivitySession = nil
    }

    private func publishPhoneLocation(_ location: CLLocation) async {
        await MainActor.run {
            guard self.shouldAccept(location, for: .iOS) else {
                #if DEBUG
                print("[LocationRelayService] Phone fix rejected during publish phase")
                #endif
                return
            }

            let batteryLevel = UIDevice.current.batteryLevel >= 0 ? Double(UIDevice.current.batteryLevel) : 0

            let fix = LocationFix(
                timestamp: location.timestamp,
                source: .iOS,
                coordinate: .init(latitude: location.coordinate.latitude, longitude: location.coordinate.longitude),
                altitudeMeters: location.verticalAccuracy >= 0 ? location.altitude : nil,
                horizontalAccuracyMeters: location.horizontalAccuracy,
                verticalAccuracyMeters: max(location.verticalAccuracy, 0),
                speedMetersPerSecond: max(location.speed, 0),
                courseDegrees: location.course >= 0 ? location.course : 0,
                // Prefer trueHeading (GPS-corrected) over magneticHeading
                headingDegrees: (self.currentHeading?.trueHeading ?? -1) >= 0 ? self.currentHeading?.trueHeading : self.currentHeading?.magneticHeading,
                batteryFraction: batteryLevel,
                sequence: AtomicSequenceGenerator.shared.next()  // Issue #3: Use atomic generator
            )
            self.handleInboundFix(fix)
            self.updateBaseStationMotionState(with: location, generatedFix: fix)
        }
    }

    private func updateBaseStationMotionState(with location: CLLocation, generatedFix fix: LocationFix) {
        let speed = max(location.speed, 0)
        phoneSpeedSamples.append(speed)
        if phoneSpeedSamples.count > phoneSpeedWindowSize {
            phoneSpeedSamples.removeFirst(phoneSpeedSamples.count - phoneSpeedWindowSize)
        }

        let averageSpeed = phoneSpeedSamples.reduce(0, +) / Double(phoneSpeedSamples.count)
        let stationaryEntryThreshold: Double = 0.5
        let stationaryExitThreshold: Double = 1.5

        if !isInLowPowerMode && averageSpeed < stationaryEntryThreshold {
            enterBaseStationLowPowerMode()
        } else if isInLowPowerMode && averageSpeed > stationaryExitThreshold {
            exitBaseStationLowPowerMode()
        }

        // Ensure heading updates resume when movement detected
        if !isInLowPowerMode, CLLocationManager.headingAvailable() {
            locationManager.startUpdatingHeading()
        }
    }

    private func enterBaseStationLowPowerMode() {
        guard !isInLowPowerMode else { return }
        isInLowPowerMode = true
        print("[LocationRelayService] Entering base-station low power mode")
        locationManager.desiredAccuracy = kCLLocationAccuracyNearestTenMeters
        locationManager.distanceFilter = 20
        if CLLocationManager.headingAvailable() {
            locationManager.stopUpdatingHeading()
        }
    }

    private func exitBaseStationLowPowerMode() {
        guard isInLowPowerMode else { return }
        isInLowPowerMode = false
        print("[LocationRelayService] Exiting base-station low power mode")
        applyTrackingMode()
        if CLLocationManager.headingAvailable() {
            locationManager.startUpdatingHeading()
        }
    }

    private func recordFixTimestamp(for source: LocationFix.Source) {
        let now = Date()
        var timestamps = fixTimestampsBySource[source] ?? []
        timestamps.append(now)
        timestamps = timestamps.filter { now.timeIntervalSince($0) <= healthWindow }
        fixTimestampsBySource[source] = timestamps
    }

    private func resetWatchRetryState() {
        watchRetryQueue.async { [weak self] in
            guard let self else { return }
            self.pendingRetryWorkItems.values.forEach { $0.cancel() }
            self.pendingRetryWorkItems.removeAll()
            self.pendingWatchMessages.removeAll()
        }
    }

    private func handleIncomingWatchMessage(_ data: Data) {
        watchRetryQueue.async { [weak self] in
            guard let self else { return }
            if let fix = self.decodeFix(from: data) {
                DispatchQueue.main.async {
                    self.handleInboundFix(fix)
                }
            } else {
                self.enqueuePendingWatchMessage(data)
            }
        }
    }

    private func enqueuePendingWatchMessage(_ data: Data) {
        if pendingWatchMessages.count >= maxPendingMessages {
            if let oldest = pendingWatchMessages.values.min(by: { $0.firstFailureDate < $1.firstFailureDate }) {
                dropPendingWatchMessage(oldest, reason: "queue capacity")
            }
        }

        let message = PendingWatchMessage(id: UUID(), data: data, retryCount: 0, firstFailureDate: Date())
        pendingWatchMessages[message.id] = message

        // Track queue depth metrics
        let queueDepth = pendingWatchMessages.count
        if queueDepth > peakQueueDepth {
            peakQueueDepth = queueDepth
        }

        print("[QUEUE] Enqueued watch message (depth: \(queueDepth), peak: \(peakQueueDepth))")
        scheduleRetry(for: message)
    }

    private func scheduleRetry(for message: PendingWatchMessage) {
        pendingRetryWorkItems[message.id]?.cancel()
        let delay = min(baseRetryDelay * pow(2, Double(message.retryCount)), maxRetryDelay)
        let workItem = DispatchWorkItem { [weak self] in
            self?.retryPendingMessage(id: message.id)
        }
        pendingRetryWorkItems[message.id] = workItem
        watchRetryQueue.asyncAfter(deadline: .now() + delay, execute: workItem)
    }

    private func retryPendingMessage(id: UUID) {
        guard var message = pendingWatchMessages[id] else { return }
        pendingRetryWorkItems[id]?.cancel()
        pendingRetryWorkItems[id] = nil

        let age = Date().timeIntervalSince(message.firstFailureDate)
        if age > maxPendingMessageAge {
            dropPendingWatchMessage(message, reason: "stale (age=\(String(format: "%.1f", age))s)")
            return
        }

        message.retryCount += 1

        if let fix = decodeFix(from: message.data) {
            pendingWatchMessages.removeValue(forKey: id)
            DispatchQueue.main.async {
                self.handleInboundFix(fix)
            }
            return
        }

        if message.retryCount >= maxWatchRetryAttempts {
            dropPendingWatchMessage(message, reason: "max retries reached")
            return
        }

        pendingWatchMessages[id] = message
        scheduleRetry(for: message)
    }

    private func dropPendingWatchMessage(_ message: PendingWatchMessage, reason: String) {
        pendingRetryWorkItems[message.id]?.cancel()
        pendingRetryWorkItems.removeValue(forKey: message.id)
        pendingWatchMessages.removeValue(forKey: message.id)

        // Track drop statistics
        totalDroppedMessages += 1
        dropReasons[reason, default: 0] += 1

        let queueDepth = pendingWatchMessages.count
        print("[DROP] Watch message dropped after \(message.retryCount) retries, reason: \(reason) (total drops: \(totalDroppedMessages), queue depth: \(queueDepth))")
    }

    private func flushPendingWatchMessages() {
        watchRetryQueue.async { [weak self] in
            guard let self else { return }
            let pendingIDs = Array(self.pendingWatchMessages.keys)
            pendingIDs.forEach { self.retryPendingMessage(id: $0) }
        }
    }

    public struct StreamHealth {
        public struct FixHealth {
            public let isActive: Bool
            public let lastUpdateAge: TimeInterval?
            public let updateRate: Double
            public let signalQuality: Double
        }

        public let base: FixHealth
        public let remote: FixHealth
        public let overall: RelayHealth
    }

    public func streamHealthSnapshot(window: TimeInterval = 10) -> StreamHealth {
        let now = Date()
        let baseTimestamps = fixTimestampsBySource[.iOS] ?? []
        let remoteTimestamps = fixTimestampsBySource[.watchOS] ?? []

        let baseAge = latestPhoneFix.map { now.timeIntervalSince($0.timestamp) }
        let remoteAge = latestWatchFix.map { now.timeIntervalSince($0.timestamp) }

        let baseHealth = StreamHealth.FixHealth(
            isActive: isPhoneLocationActive,
            lastUpdateAge: baseAge,
            updateRate: Double(baseTimestamps.count) / window,
            signalQuality: signalQuality(for: latestPhoneFix, age: baseAge)
        )

        let remoteHealth = StreamHealth.FixHealth(
            isActive: isWatchConnected,
            lastUpdateAge: remoteAge,
            updateRate: Double(remoteTimestamps.count) / window,
            signalQuality: signalQuality(for: latestWatchFix, age: remoteAge)
        )

        let overall: RelayHealth
        switch (baseHealth.isActive, remoteHealth.isActive) {
        case (true, true):
            overall = .streaming
        case (false, false):
            overall = .idle
        default:
            overall = .degraded(reason: "Single stream active")
        }

        return StreamHealth(base: baseHealth, remote: remoteHealth, overall: overall)
    }

    private func signalQuality(for fix: LocationFix?, age: TimeInterval?) -> Double {
        guard let fix else { return 0 }
        let accuracyScore = max(0, 1.0 - (fix.horizontalAccuracyMeters / 100.0))
        let ageScore = age.map { max(0, 1.0 - ($0 / 30.0)) } ?? 0
        return (accuracyScore + ageScore) / 2.0
    }

    // MARK: - Telemetry Access (Phase 4.2)

    /// Snapshot of telemetry metrics for monitoring and debugging
    public struct TelemetryMetrics {
        /// Total duplicate fixes detected and rejected during session
        public let duplicateFixCount: Int

        /// Total watch messages dropped during session
        public let totalDroppedMessages: Int

        /// Breakdown of drop reasons with counts
        public let dropReasons: [String: Int]

        /// Current retry queue depth
        public let currentQueueDepth: Int

        /// Peak retry queue depth observed during session
        public let peakQueueDepth: Int

        /// Total connectivity state transitions (connect/disconnect events)
        public let connectivityTransitions: Int
    }

    /// Returns current telemetry metrics snapshot
    /// - Returns: Current metrics for logging and telemetry systems
    public func telemetrySnapshot() -> TelemetryMetrics {
        return TelemetryMetrics(
            duplicateFixCount: duplicateFixCount,
            totalDroppedMessages: totalDroppedMessages,
            dropReasons: dropReasons,
            currentQueueDepth: pendingWatchMessages.count,
            peakQueueDepth: peakQueueDepth,
            connectivityTransitions: connectivityTransitions
        )
    }

    private func logStreamHealthIfNeeded(reason: String? = nil) {
        let now = Date()
        if let last = lastHealthLogTime, now.timeIntervalSince(last) < 5 {
            return
        }
        lastHealthLogTime = now
        let snapshot = streamHealthSnapshot(window: healthWindow)
        let queueDepth = pendingWatchMessages.count
        print("""
[HEALTH] Base: \(snapshot.base.isActive ? "✅" : "⚠️") age=\(snapshot.base.lastUpdateAge.map { String(format: "%.1fs", $0) } ?? "—") rate=\(String(format: "%.2f/s", snapshot.base.updateRate)) quality=\(String(format: "%.2f", snapshot.base.signalQuality))
[HEALTH] Remote: \(snapshot.remote.isActive ? "✅" : "⚠️") age=\(snapshot.remote.lastUpdateAge.map { String(format: "%.1fs", $0) } ?? "—") rate=\(String(format: "%.2f/s", snapshot.remote.updateRate)) quality=\(String(format: "%.2f", snapshot.remote.signalQuality))
[HEALTH] Overall: \(snapshot.overall)\(reason.map { " // " + $0 } ?? "")
[HEALTH] Metrics: queue=\(queueDepth) peak=\(peakQueueDepth) drops=\(totalDroppedMessages) dupes=\(duplicateFixCount) transitions=\(connectivityTransitions)
""")
    }

    /// Creates a fused location by averaging base and remote GPS sources.
    ///
    /// - Warning: This creates a geographic midpoint between the two sources.
    ///   Do NOT use for robot cameraman tracking where base ≠ subject.
    ///   Only appropriate when both devices track the same physical subject.
    ///
    /// The fusion uses accuracy-weighted averaging:
    /// - Higher accuracy measurements contribute more to the final position
    /// - Stale measurements (> fusionWindow) are ignored
}

extension LocationRelayService: CLLocationManagerDelegate {
    public func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        if #available(iOS 14.0, *) {
            handleAuthorizationChange(status: manager.authorizationStatus, accuracy: manager.accuracyAuthorization)
        }
    }

    public func locationManager(_ manager: CLLocationManager, didChangeAuthorization status: CLAuthorizationStatus) {
        if #available(iOS 14.0, *) {
            handleAuthorizationChange(status: status, accuracy: manager.accuracyAuthorization)
        } else {
            handleAuthorizationChange(status: status, accuracy: nil)
        }
    }

    public func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {

        guard let latest = locations.last else { return }
        guard shouldAccept(latest) else {
            #if DEBUG
            let thresholds = activeQualityThresholds
            print("[LocationRelayService] Rejected phone fix (accuracy=\(latest.horizontalAccuracy)m age=\(abs(latest.timestamp.timeIntervalSinceNow))s speed=\(latest.speed)) thresholds=\(thresholds)")
            #endif
            return
        }
        Task {
            await publishPhoneLocation(latest)
        }
    }

    public func locationManager(_ manager: CLLocationManager, didUpdateHeading newHeading: CLHeading) {
        // Store the latest heading for use when publishing location fixes
        let previousResolvedHeading = resolveHeading(from: currentHeading)
        currentHeading = newHeading

        // Issue #20: Rate limit heading-only updates to reduce bandwidth
        // Use trueHeading (accounts for magnetic declination) when available for gimbal accuracy
        guard let resolvedHeading = resolveHeading(from: newHeading) else { return }

        let now = Date()

        // Check time-based rate limit
        if let lastTime = lastHeadingPublishTime,
           now.timeIntervalSince(lastTime) < minHeadingInterval {
            return  // Too soon since last heading publish
        }

        // Check if heading changed enough to warrant update
        if let prevHeading = previousResolvedHeading {
            var delta = abs(resolvedHeading - prevHeading)
            if delta > 180 { delta = 360 - delta }  // Handle wrap-around
            if delta < minHeadingChangeDegrees {
                return  // Change too small
            }
        }

        lastHeadingPublishTime = now

        Task { [resolvedHeading] in
            await self.publishHeadingUpdate(resolvedHeading)
        }
    }

    /// Resolve best heading from CLHeading.
    /// Prefers trueHeading (corrected for magnetic declination) for accurate gimbal control.
    /// Falls back to magneticHeading if trueHeading is unavailable (requires location services).
    private func resolveHeading(from heading: CLHeading?) -> Double? {
        guard let heading else { return nil }

        // trueHeading is -1 if location services unavailable or heading invalid
        // trueHeading accounts for magnetic declination - essential for gimbal accuracy
        if heading.trueHeading >= 0 {
            return heading.trueHeading
        }

        // Fallback to magnetic heading (less accurate but always available when compass works)
        let magnetic = heading.magneticHeading
        return magnetic.isNaN ? nil : magnetic
    }

    private func publishHeadingUpdate(_ heading: Double) async {
        await MainActor.run {
            guard let baseFix = self.latestPhoneFix else { return }

            let batteryLevel = UIDevice.current.batteryLevel >= 0 ? Double(UIDevice.current.batteryLevel) : 0

            let updatedFix = LocationFix(
                timestamp: Date(),
                source: .iOS,
                coordinate: baseFix.coordinate,
                altitudeMeters: baseFix.altitudeMeters,
                horizontalAccuracyMeters: baseFix.horizontalAccuracyMeters,
                verticalAccuracyMeters: baseFix.verticalAccuracyMeters,
                speedMetersPerSecond: baseFix.speedMetersPerSecond,
                courseDegrees: baseFix.courseDegrees,
                headingDegrees: heading,  // Now uses trueHeading when available
                batteryFraction: batteryLevel,
                sequence: AtomicSequenceGenerator.shared.next()  // Issue #3: Use atomic generator
            )

            self.handleInboundFix(updatedFix)
        }
    }

    public func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        health = .degraded(reason: error.localizedDescription)
    }
}

extension LocationRelayService: WCSessionDelegate {
    public func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {
        print("[LocationRelayService] WCSession activation completed with state: \(activationState.rawValue), reachable: \(session.isReachable), error: \(error?.localizedDescription ?? "none")")
        if let error {
            health = .degraded(reason: error.localizedDescription)
        }
        isWatchConnected = session.isReachable && activationState == .activated
        print("[LocationRelayService] isWatchConnected set to: \(isWatchConnected)")
    }

    public func sessionDidBecomeInactive(_ session: WCSession) {
        // Issue #10: Properly handle session becoming inactive
        // Called when the session can no longer be used to modify or add any new transfers
        // This occurs when the user switches to a different Apple Watch
        print("[LocationRelayService] WCSession became inactive")
        isWatchConnected = false
        health = .degraded(reason: "Watch session inactive")
    }

    public func sessionDidDeactivate(_ session: WCSession) {
        // Issue #10: Properly handle session deactivation
        // Called when all outstanding messages and transfers have been delivered
        // After this is called, we should reactivate the session for the new watch
        print("[LocationRelayService] WCSession deactivated, reactivating...")
        
        // Reset watch-related state for new session
        lastWatchFixDate = nil
        latestWatchFix = nil
        lastSequenceBySource[.watchOS] = nil
        
        // Reactivate for new watch
        session.activate()
    }

    public func sessionReachabilityDidChange(_ session: WCSession) {
        if session.isReachable && session.activationState == .activated {
            isWatchConnected = true
            flushPendingWatchMessages()
        }
    }

    public func session(_ session: WCSession, didReceiveApplicationContext applicationContext: [String : Any]) {
        print("[LocationRelayService] Received application context update")
        isWatchConnected = true  // We just received context, so watch is connected
        guard let data = applicationContext["latestFix"] as? Data,
              let fix = decodeFix(from: data) else {
            print("[LocationRelayService] Failed to decode fix from context")
            return
        }
        print("[LocationRelayService] Decoded context fix: lat=\(fix.coordinate.latitude), lon=\(fix.coordinate.longitude)")
        handleInboundFix(fix)
    }

    public func session(_ session: WCSession, didReceiveMessageData messageData: Data) {
        print("[LocationRelayService] Received message data: \(messageData.count) bytes")
        isWatchConnected = true  // We just received data, so watch is definitely connected
        handleIncomingWatchMessage(messageData)
    }
    
    // Issue #2: Handle message with reply handler for delivery confirmation
    public func session(_ session: WCSession, didReceiveMessageData messageData: Data, replyHandler: @escaping (Data) -> Void) {
        print("[LocationRelayService] Received message data with reply handler: \(messageData.count) bytes")
        isWatchConnected = true
        handleIncomingWatchMessage(messageData)
        
        // Send acknowledgment back to watch
        let ack: [String: Any] = ["ack": true, "ts": Date().timeIntervalSince1970]
        if let ackData = try? JSONSerialization.data(withJSONObject: ack) {
            replyHandler(ackData)
        } else {
            replyHandler(Data())
        }
    }

    public func session(_ session: WCSession, didReceive file: WCSessionFile) {
        isWatchConnected = true  // We just received a file, so watch is connected
        guard let data = try? Data(contentsOf: file.fileURL), let fix = decodeFix(from: data) else {
            return
        }
        handleInboundFix(fix)
    }
}
#else

public protocol LocationRelayDelegate: AnyObject {
    func didUpdate(_ update: RelayUpdate)
    func healthDidChange(_ health: RelayHealth)
    func watchConnectionDidChange(_ isConnected: Bool)
    func authorizationDidFail(_ error: LocationRelayError)
}

public extension LocationRelayDelegate {
    func authorizationDidFail(_ error: LocationRelayError) {}
}

public final class LocationRelayService {
    public weak var delegate: LocationRelayDelegate?

    public init() {}

    public func start() {
        assertionFailure("LocationRelayService is iOS only")
    }

    public func stop() {}

    public func currentSnapshot() -> RelayUpdate? { nil }

    public func addTransport(_ transport: LocationTransport) {
        transport.open()
    }
}
#endif
