import Foundation

// MARK: - Clock Offset Estimator

/// Estimates clock offset between two devices using RTT measurements.
/// This helps correct for clock drift when calculating latency from timestamps.
///
/// Uses the NTP-style algorithm: offset = (T2 - T1 + T3 - T4) / 2
/// where T1=send, T2=remote_receive, T3=remote_send, T4=receive
///
/// Simplified for one-way with RTT: offset ≈ (measuredLatency - RTT/2)
public final class ClockOffsetEstimator: @unchecked Sendable {

    private var offsetSamples: [Double] = []  // in seconds
    private let maxSamples = 10
    private let lock = NSLock()

    /// Current estimated clock offset in seconds (positive = remote ahead)
    public var estimatedOffset: Double {
        lock.lock()
        defer { lock.unlock() }
        guard !offsetSamples.isEmpty else { return 0 }
        // Use median for robustness against outliers
        let sorted = offsetSamples.sorted()
        return sorted[sorted.count / 2]
    }

    /// Record an RTT measurement to refine offset estimate
    /// - Parameters:
    ///   - rttSeconds: Round-trip time in seconds
    ///   - measuredOneWaySeconds: Measured one-way latency (remote_timestamp - local_timestamp)
    public func recordRTTSample(rttSeconds: Double, measuredOneWaySeconds: Double) {
        lock.lock()
        defer { lock.unlock() }

        // If clocks were perfectly synced, one-way latency would be RTT/2
        // Offset = measured - expected = measured - (RTT/2)
        let estimatedOneWay = rttSeconds / 2
        let offset = measuredOneWaySeconds - estimatedOneWay

        offsetSamples.append(offset)
        if offsetSamples.count > maxSamples {
            offsetSamples.removeFirst()
        }
    }

    /// Correct a measured latency using estimated clock offset
    public func correctedLatency(measuredSeconds: Double) -> Double {
        return measuredSeconds - estimatedOffset
    }

    /// Reset offset estimates
    public func reset() {
        lock.lock()
        defer { lock.unlock() }
        offsetSamples.removeAll()
    }
}

// MARK: - Connection Health Monitoring

/// End-to-end latency measurement for the GPS relay pipeline.
///
/// - Note: `watchToPhoneLatencyMs` compares GPS timestamp (atomic time from satellites)
///   to phone kernel time, which can have ±200ms drift. For accurate measurements,
///   use RTT-based correction via `ClockOffsetEstimator` or compare consecutive
///   packet deltas instead of absolute wall-clock times.
public struct LatencyMeasurement: Codable, Sendable {
    /// Time when GPS fix was captured on watch
    public let gpsTimestamp: Date

    /// Time when fix was received by phone
    public let phoneReceivedAt: Date

    /// Time when fix was sent to server
    public let serverSentAt: Date

    /// Time when server acknowledged (if available)
    public let serverAckAt: Date?

    /// Unique correlation ID for matching requests/responses
    public let correlationId: UUID

    /// GPS fix sequence number
    public let sequence: Int

    public init(
        gpsTimestamp: Date,
        phoneReceivedAt: Date = Date(),
        serverSentAt: Date = Date(),
        serverAckAt: Date? = nil,
        correlationId: UUID = UUID(),
        sequence: Int
    ) {
        self.gpsTimestamp = gpsTimestamp
        self.phoneReceivedAt = phoneReceivedAt
        self.serverSentAt = serverSentAt
        self.serverAckAt = serverAckAt
        self.correlationId = correlationId
        self.sequence = sequence
    }

    /// Total end-to-end latency in milliseconds (if server ack available)
    public var totalLatencyMs: Double? {
        guard let ack = serverAckAt else { return nil }
        return ack.timeIntervalSince(gpsTimestamp) * 1000
    }

    /// Watch → Phone latency in milliseconds (uncorrected for clock drift)
    ///
    /// - Warning: This compares GPS atomic time to phone kernel time.
    ///   Clock drift between devices can cause ±200ms errors or negative values.
    ///   For accurate measurement, use `correctedWatchToPhoneLatencyMs(using:)`.
    public var watchToPhoneLatencyMs: Double {
        phoneReceivedAt.timeIntervalSince(gpsTimestamp) * 1000
    }

    /// Watch → Phone latency corrected for clock offset
    /// - Parameter estimator: Clock offset estimator with RTT samples
    public func correctedWatchToPhoneLatencyMs(using estimator: ClockOffsetEstimator) -> Double {
        let uncorrected = phoneReceivedAt.timeIntervalSince(gpsTimestamp)
        let corrected = estimator.correctedLatency(measuredSeconds: uncorrected)
        return max(0, corrected * 1000)  // Clamp to non-negative
    }

    /// Phone → Server latency in milliseconds (if ack available)
    public var phoneToServerLatencyMs: Double? {
        guard let ack = serverAckAt else { return nil }
        return ack.timeIntervalSince(serverSentAt) * 1000
    }
}

// MARK: - Connection Quality Tracker

/// Tracks connection quality metrics over a sliding window
public final class ConnectionQualityTracker: @unchecked Sendable {
    
    public struct QualitySnapshot: Sendable {
        public let avgLatencyMs: Double
        public let p95LatencyMs: Double
        public let packetLossRate: Double
        public let messagesPerSecond: Double
        public let lastUpdateAge: TimeInterval
        public let connectionScore: Double  // 0-100
        public let isHealthy: Bool
    }
    
    private let windowSeconds: TimeInterval
    private var latencySamples: [(timestamp: Date, latencyMs: Double)] = []
    private var messageTimestamps: [Date] = []
    private var expectedSequences: [Int: Date] = [:]
    private var receivedSequences: [Int: Date] = [:]
    private var lastReceivedAt: Date?
    private let lock = NSLock()
    
    // Thresholds
    private let maxHealthyLatencyMs: Double = 500
    private let maxHealthyPacketLoss: Double = 0.05
    private let minHealthyMessageRate: Double = 0.5
    
    public init(windowSeconds: TimeInterval = 30.0) {
        self.windowSeconds = windowSeconds
    }
    
    /// Record a successful message receipt with latency
    public func recordMessage(sequence: Int, latencyMs: Double) {
        lock.lock()
        defer { lock.unlock() }
        
        let now = Date()
        latencySamples.append((now, latencyMs))
        messageTimestamps.append(now)
        receivedSequences[sequence] = now
        lastReceivedAt = now
        
        pruneOldData(now: now)
    }
    
    /// Record an expected sequence (for packet loss tracking)
    public func expectSequence(_ sequence: Int) {
        lock.lock()
        defer { lock.unlock() }
        expectedSequences[sequence] = Date()
    }
    
    /// Get current quality snapshot
    public func snapshot() -> QualitySnapshot {
        lock.lock()
        defer { lock.unlock() }
        
        let now = Date()
        pruneOldData(now: now)
        
        // Calculate average latency
        let avgLatency: Double
        if latencySamples.isEmpty {
            avgLatency = 0
        } else {
            avgLatency = latencySamples.map { $0.latencyMs }.reduce(0, +) / Double(latencySamples.count)
        }
        
        // Calculate p95 latency
        let p95Latency: Double
        if latencySamples.count >= 20 {
            let sorted = latencySamples.map { $0.latencyMs }.sorted()
            let p95Index = Int(Double(sorted.count) * 0.95)
            p95Latency = sorted[min(p95Index, sorted.count - 1)]
        } else {
            p95Latency = avgLatency
        }
        
        // Calculate packet loss
        let packetLoss: Double
        if expectedSequences.isEmpty {
            packetLoss = 0
        } else {
            let lost = expectedSequences.keys.filter { receivedSequences[$0] == nil }.count
            packetLoss = Double(lost) / Double(expectedSequences.count)
        }
        
        // Calculate message rate
        let messageRate = Double(messageTimestamps.count) / windowSeconds
        
        // Calculate last update age
        let lastAge = lastReceivedAt.map { now.timeIntervalSince($0) } ?? Double.infinity
        
        // Calculate connection score (0-100)
        var score: Double = 100
        
        // Penalize high latency (up to -40 points)
        if avgLatency > maxHealthyLatencyMs {
            score -= min(40, (avgLatency - maxHealthyLatencyMs) / 10)
        }
        
        // Penalize packet loss (up to -30 points)
        if packetLoss > maxHealthyPacketLoss {
            score -= min(30, (packetLoss - maxHealthyPacketLoss) * 500)
        }
        
        // Penalize low message rate (up to -20 points)
        if messageRate < minHealthyMessageRate {
            score -= min(20, (minHealthyMessageRate - messageRate) * 40)
        }
        
        // Penalize stale connection (up to -10 points)
        if lastAge > 5 {
            score -= min(10, (lastAge - 5) * 2)
        }
        
        score = max(0, min(100, score))
        
        let isHealthy = score >= 70 &&
                        avgLatency <= maxHealthyLatencyMs &&
                        packetLoss <= maxHealthyPacketLoss &&
                        lastAge < 10
        
        return QualitySnapshot(
            avgLatencyMs: avgLatency,
            p95LatencyMs: p95Latency,
            packetLossRate: packetLoss,
            messagesPerSecond: messageRate,
            lastUpdateAge: lastAge,
            connectionScore: score,
            isHealthy: isHealthy
        )
    }
    
    /// Reset all tracking
    public func reset() {
        lock.lock()
        defer { lock.unlock() }
        latencySamples.removeAll()
        messageTimestamps.removeAll()
        expectedSequences.removeAll()
        receivedSequences.removeAll()
        lastReceivedAt = nil
    }
    
    private func pruneOldData(now: Date) {
        let cutoff = now.addingTimeInterval(-windowSeconds)
        latencySamples = latencySamples.filter { $0.timestamp > cutoff }
        messageTimestamps = messageTimestamps.filter { $0 > cutoff }
        
        // Keep sequence tracking for a longer window to catch delayed arrivals, but prune it.
        let seqCutoff = now.addingTimeInterval(-windowSeconds * 2)
        expectedSequences = expectedSequences.filter { $0.value > seqCutoff }
        receivedSequences = receivedSequences.filter { $0.value > seqCutoff }
    }
}

// MARK: - Heartbeat Protocol

/// Application-level heartbeat for proactive connection health detection
public struct HeartbeatMessage: Codable, Sendable {
    public enum MessageType: String, Codable, Sendable {
        case ping
        case pong
    }
    
    public let type: MessageType
    public let timestamp: Date
    public let sequence: Int
    public let correlationId: UUID
    
    public init(type: MessageType, sequence: Int, correlationId: UUID = UUID()) {
        self.type = type
        self.timestamp = Date()
        self.sequence = sequence
        self.correlationId = correlationId
    }
}

/// Manages heartbeat protocol for connection health
public final class HeartbeatManager: @unchecked Sendable {
    
    public enum State: Sendable {
        case healthy
        case degraded(missedCount: Int)
        case dead
    }
    
    public typealias SendHandler = @Sendable (HeartbeatMessage) -> Void
    public typealias StateHandler = @Sendable (State) -> Void
    
    private let interval: TimeInterval
    private let timeout: TimeInterval
    private let maxMissed: Int
    
    private var timer: Timer?
    private var pendingPings: [UUID: (timestamp: Date, sequence: Int)] = [:]
    private var missedCount: Int = 0
    private var sequence: Int = 0
    private var latencySamples: [Double] = []
    private let maxLatencySamples = 20
    private let lock = NSLock()
    
    private var sendHandler: SendHandler?
    private var stateHandler: StateHandler?
    
    public private(set) var state: State = .healthy
    public private(set) var lastRTT: TimeInterval?
    public var averageRTT: TimeInterval? {
        lock.lock()
        defer { lock.unlock() }
        guard !latencySamples.isEmpty else { return nil }
        return latencySamples.reduce(0, +) / Double(latencySamples.count)
    }
    
    public init(
        interval: TimeInterval = 5.0,
        timeout: TimeInterval = 10.0,
        maxMissed: Int = 3
    ) {
        self.interval = interval
        self.timeout = timeout
        self.maxMissed = maxMissed
    }
    
    /// Start heartbeat monitoring
    public func start(sendHandler: @escaping SendHandler, stateHandler: @escaping StateHandler) {
        lock.lock()
        defer { lock.unlock() }
        
        self.sendHandler = sendHandler
        self.stateHandler = stateHandler
        
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            self?.sendPing()
        }
        
        missedCount = 0
        state = .healthy
    }
    
    /// Stop heartbeat monitoring
    public func stop() {
        lock.lock()
        defer { lock.unlock() }
        
        timer?.invalidate()
        timer = nil
        pendingPings.removeAll()
        sendHandler = nil
        stateHandler = nil
    }
    
    /// Handle received pong message
    public func receivedPong(_ message: HeartbeatMessage) {
        lock.lock()
        defer { lock.unlock() }
        
        guard let pending = pendingPings.removeValue(forKey: message.correlationId) else {
            return
        }
        
        let rtt = Date().timeIntervalSince(pending.timestamp)
        lastRTT = rtt
        
        latencySamples.append(rtt)
        if latencySamples.count > maxLatencySamples {
            latencySamples.removeFirst()
        }
        
        // Reset missed count on successful pong
        if missedCount > 0 {
            missedCount = 0
            updateState(.healthy)
        }
    }
    
    /// Create a pong response for a received ping
    public func createPong(for ping: HeartbeatMessage) -> HeartbeatMessage {
        HeartbeatMessage(
            type: .pong,
            sequence: ping.sequence,
            correlationId: ping.correlationId
        )
    }
    
    private func sendPing() {
        lock.lock()
        
        // Check for timed out pings
        let now = Date()
        let timedOut = pendingPings.filter { now.timeIntervalSince($0.value.timestamp) > timeout }
        
        for (id, _) in timedOut {
            pendingPings.removeValue(forKey: id)
            missedCount += 1
        }
        
        // Update state based on missed count
        let newState: State
        if missedCount >= maxMissed {
            newState = .dead
        } else if missedCount > 0 {
            newState = .degraded(missedCount: missedCount)
        } else {
            newState = .healthy
        }
        
        if !statesEqual(state, newState) {
            updateState(newState)
        }
        
        // Send new ping
        sequence += 1
        let ping = HeartbeatMessage(type: .ping, sequence: sequence)
        pendingPings[ping.correlationId] = (now, sequence)
        
        let handler = sendHandler
        lock.unlock()
        
        handler?(ping)
    }
    
    private func updateState(_ newState: State) {
        state = newState
        let handler = stateHandler
        DispatchQueue.main.async {
            handler?(newState)
        }
    }
    
    private func statesEqual(_ lhs: State, _ rhs: State) -> Bool {
        switch (lhs, rhs) {
        case (.healthy, .healthy), (.dead, .dead):
            return true
        case (.degraded(let a), .degraded(let b)):
            return a == b
        default:
            return false
        }
    }
}

// MARK: - Jitter Buffer

/// Simple jitter buffer for smoothing GPS updates
public final class JitterBuffer<T>: @unchecked Sendable {
    
    private struct Entry {
        let item: T
        let sequence: Int
        let receivedAt: Date
    }
    
    private let bufferSize: Int
    private let maxDelay: TimeInterval
    private var buffer: [Entry] = []
    private var lastEmittedSequence: Int = -1
    private let lock = NSLock()
    
    public init(bufferSize: Int = 3, maxDelay: TimeInterval = 0.2) {
        self.bufferSize = bufferSize
        self.maxDelay = maxDelay
    }
    
    /// Add item to buffer, returns item to emit (if any)
    public func add(_ item: T, sequence: Int) -> T? {
        lock.lock()
        defer { lock.unlock() }
        
        let now = Date()
        
        // Add to buffer
        buffer.append(Entry(item: item, sequence: sequence, receivedAt: now))
        
        // Sort by sequence
        buffer.sort { $0.sequence < $1.sequence }
        
        // Trim old entries
        buffer = buffer.filter { now.timeIntervalSince($0.receivedAt) < maxDelay * 2 }
        
        // Emit if we have enough buffered or oldest is past max delay
        if buffer.count >= bufferSize || 
           (buffer.first.map { now.timeIntervalSince($0.receivedAt) >= maxDelay } ?? false) {
            return emitNext()
        }
        
        return nil
    }
    
    /// Force emit next item (for flush scenarios)
    public func flush() -> [T] {
        lock.lock()
        defer { lock.unlock() }
        
        let items = buffer.sorted { $0.sequence < $1.sequence }.map { $0.item }
        buffer.removeAll()
        return items
    }
    
    private func emitNext() -> T? {
        guard let first = buffer.first else { return nil }
        
        // Skip if out of order and we've already emitted a higher sequence
        if first.sequence <= lastEmittedSequence {
            buffer.removeFirst()
            return emitNext()  // Try next
        }
        
        lastEmittedSequence = first.sequence
        buffer.removeFirst()
        return first.item
    }
}

// MARK: - GPS Jump Detector

/// Detects and filters GPS jumps (erroneous large position changes)
public final class GPSJumpDetector: @unchecked Sendable {
    
    public struct FilterResult: Sendable {
        public let isValid: Bool
        public let reason: String?
        public let correctedCoordinate: LocationFix.Coordinate?
    }
    
    private var lastValidFix: LocationFix?
    private let lock = NSLock()
    
    /// Maximum believable speed in m/s (about 200 km/h for water sports)
    public var maxSpeed: Double = 55.0
    
    /// Minimum time between fixes to evaluate (seconds)
    public var minTimeDelta: TimeInterval = 0.1
    
    /// Maximum acceleration in m/s² (about 3G)
    public var maxAcceleration: Double = 30.0
    
    public init() {}
    
    /// Validate a GPS fix against previous fixes
    public func validate(_ fix: LocationFix) -> FilterResult {
        lock.lock()
        defer { lock.unlock() }
        
        guard let last = lastValidFix else {
            lastValidFix = fix
            return FilterResult(isValid: true, reason: nil, correctedCoordinate: nil)
        }
        
        let timeDelta = fix.timestamp.timeIntervalSince(last.timestamp)
        
        // If too little time has passed, can't validate
        guard timeDelta >= minTimeDelta else {
            return FilterResult(isValid: true, reason: nil, correctedCoordinate: nil)
        }
        
        // Calculate distance
        let distance = last.coordinate.distance(to: fix.coordinate)
        let impliedSpeed = distance / timeDelta
        
        // Check for impossible speed
        if impliedSpeed > maxSpeed {
            return FilterResult(
                isValid: false,
                reason: "Impossible speed: \(String(format: "%.1f", impliedSpeed)) m/s over \(String(format: "%.2f", timeDelta))s",
                correctedCoordinate: nil
            )
        }
        
        // Check for impossible acceleration
        let speedChange = abs(fix.speedMetersPerSecond - last.speedMetersPerSecond)
        let impliedAcceleration = speedChange / timeDelta
        
        if impliedAcceleration > maxAcceleration && fix.speedMetersPerSecond > 5 {
            return FilterResult(
                isValid: false,
                reason: "Impossible acceleration: \(String(format: "%.1f", impliedAcceleration)) m/s²",
                correctedCoordinate: nil
            )
        }
        
        // Valid fix
        lastValidFix = fix
        return FilterResult(isValid: true, reason: nil, correctedCoordinate: nil)
    }
    
    /// Reset detector state
    public func reset() {
        lock.lock()
        defer { lock.unlock() }
        lastValidFix = nil
    }
}
