import Foundation
import LocationCore
import Network

#if os(watchOS)

/// Direct WebSocket transport for Watch → Server communication when iPhone is not reachable.
/// This bypasses WCSession's iCloud relay to achieve lower latency over LTE.
@available(watchOS 6.0, *)
public final class WatchDirectTransport: NSObject, URLSessionWebSocketDelegate {

    // MARK: - Types

    public enum ConnectionState: String, Sendable {
        case disconnected
        case connecting
        case connected
        case reconnecting
        case failed
    }

    public struct Configuration {
        /// Server URL for direct connection (wss://your-server/watch)
        public var serverURL: URL?

        /// Maximum reconnection attempts before giving up.
        ///
        /// Field tracking should keep trying for the full session. A transient
        /// LTE/tunnel failure should not permanently disable the direct path.
        public var maxReconnectAttempts: Int = Int.max

        /// Initial backoff delay in seconds
        public var initialBackoffDelay: TimeInterval = 1.0

        /// Maximum backoff delay in seconds
        public var maxBackoffDelay: TimeInterval = 30.0

        /// Optional bearer token for authentication
        public var bearerToken: String?

        /// Device identifier to include in connection
        public var deviceId: String?

        public init() {}
    }

    // MARK: - Properties

    public var configuration: Configuration

    private var session: URLSession!
    private var task: URLSessionWebSocketTask?
    private let encoder = JSONEncoder()

    /// Current connection state
    public private(set) var connectionState: ConnectionState = .disconnected {
        didSet {
            if oldValue != connectionState {
                lastStateChangedAt = Date()
                print("[WatchDirectTransport] State: \(oldValue) -> \(connectionState)")
                onStateChanged?(connectionState)
                emitMetrics()
            }
        }
    }

    /// Callback when connection state changes
    public var onStateChanged: ((ConnectionState) -> Void)?

    /// Callback when an error occurs
    public var onError: ((Error) -> Void)?

    /// Current measured round-trip latency in milliseconds
    public private(set) var currentLatencyMs: Double = 0

    // MARK: - Telemetry (for "tunnel up" UX)

    public struct Metrics: Sendable, Equatable {
        public var connectionState: ConnectionState
        public var rttMs: Double
        public var lastPongAt: Date?
        public var lastAckAt: Date?
        public var lastAckSeq: Int?
        public var lastSendAt: Date?
        public var queueDepth: Int
        public var totalDropped: Int
        public var sendRateHz: Double
        public var connectAttemptCount: Int
        public var didOpenCount: Int
        public var lastStateChangedAt: Date?
        public var lastErrorMessage: String?
        public var lastCloseCode: Int?
        public var lastCloseReason: String?
        public var lastHTTPProbeAt: Date?
        public var lastHTTPProbeStatus: Int?
        public var lastHTTPProbeError: String?
        public var networkPathStatus: String
        public var networkUsesWiFi: Bool
        public var networkUsesCellular: Bool
        public var networkIsExpensive: Bool
        public var networkIsConstrained: Bool

        public init(
            connectionState: ConnectionState,
            rttMs: Double,
            lastPongAt: Date?,
            lastAckAt: Date?,
            lastAckSeq: Int?,
            lastSendAt: Date?,
            queueDepth: Int,
            totalDropped: Int,
            sendRateHz: Double,
            connectAttemptCount: Int,
            didOpenCount: Int,
            lastStateChangedAt: Date?,
            lastErrorMessage: String?,
            lastCloseCode: Int?,
            lastCloseReason: String?,
            lastHTTPProbeAt: Date?,
            lastHTTPProbeStatus: Int?,
            lastHTTPProbeError: String?,
            networkPathStatus: String,
            networkUsesWiFi: Bool,
            networkUsesCellular: Bool,
            networkIsExpensive: Bool,
            networkIsConstrained: Bool
        ) {
            self.connectionState = connectionState
            self.rttMs = rttMs
            self.lastPongAt = lastPongAt
            self.lastAckAt = lastAckAt
            self.lastAckSeq = lastAckSeq
            self.lastSendAt = lastSendAt
            self.queueDepth = queueDepth
            self.totalDropped = totalDropped
            self.sendRateHz = sendRateHz
            self.connectAttemptCount = connectAttemptCount
            self.didOpenCount = didOpenCount
            self.lastStateChangedAt = lastStateChangedAt
            self.lastErrorMessage = lastErrorMessage
            self.lastCloseCode = lastCloseCode
            self.lastCloseReason = lastCloseReason
            self.lastHTTPProbeAt = lastHTTPProbeAt
            self.lastHTTPProbeStatus = lastHTTPProbeStatus
            self.lastHTTPProbeError = lastHTTPProbeError
            self.networkPathStatus = networkPathStatus
            self.networkUsesWiFi = networkUsesWiFi
            self.networkUsesCellular = networkUsesCellular
            self.networkIsExpensive = networkIsExpensive
            self.networkIsConstrained = networkIsConstrained
        }
    }

    /// Callback when transport metrics change (intended for watch UI indicators).
    public var onMetricsChanged: ((Metrics) -> Void)?

    // Reconnection state
    private var reconnectAttempts: Int = 0
    private var reconnectTimer: Timer?
    private var shouldReconnect: Bool = false

    // Message queue for when disconnected (keep small for memory on Watch)
    private var messageQueue: [LocationFix] = []
    private let maxQueueSize = 20
    private let queueLock = NSLock()
    private var totalDropped: Int = 0

    private var connectAttemptCount: Int = 0
    private var didOpenCount: Int = 0
    private var lastStateChangedAt: Date?
    private var lastErrorMessage: String?
    private var lastCloseCode: Int?
    private var lastCloseReason: String?
    private var lastHTTPProbeAt: Date?
    private var lastHTTPProbeStatus: Int?
    private var lastHTTPProbeError: String?
    private var httpProbeInFlight: Bool = false
    private let pathMonitor = NWPathMonitor()
    private let pathMonitorQueue = DispatchQueue(label: "WatchDirectTransport.NWPathMonitor")
    private var networkPathStatus: String = "unknown"
    private var networkUsesWiFi: Bool = false
    private var networkUsesCellular: Bool = false
    private var networkIsExpensive: Bool = false
    private var networkIsConstrained: Bool = false
    private var lastAckAt: Date?
    private var lastAckSeq: Int?
    private var lastSendAt: Date?
    private var recentSendTimes: [Date] = []
    private let sendRateWindowCount: Int = 10

    // Heartbeat
    private var heartbeatTimer: Timer?
    private var lastPongTime: Date?
    private var pendingHeartbeats: [String: Date] = [:]
    private let heartbeatInterval: TimeInterval = 10.0  // Less frequent on Watch to save battery
    private let heartbeatTimeout: TimeInterval = 30.0

    // MARK: - Initialization

    public init(configuration: Configuration = Configuration()) {
        self.configuration = configuration
        super.init()

        let sessionConfig = URLSessionConfiguration.default
        sessionConfig.timeoutIntervalForRequest = 30
        // WebSocket tasks are long-lived. A short resource timeout forces
        // reconnect churn during normal tracking sessions.
        sessionConfig.timeoutIntervalForResource = 24 * 60 * 60
        // Allow cellular for direct LTE connection
        sessionConfig.allowsCellularAccess = true
        if #available(watchOS 6.0, *) {
            sessionConfig.allowsExpensiveNetworkAccess = true
            sessionConfig.allowsConstrainedNetworkAccess = true
        }
        sessionConfig.waitsForConnectivity = true

        self.session = URLSession(
            configuration: sessionConfig,
            delegate: self,
            delegateQueue: .main
        )

        encoder.outputFormatting = .withoutEscapingSlashes
        encoder.dateEncodingStrategy = .millisecondsSince1970
        startPathMonitoring()
    }

    deinit {
        pathMonitor.cancel()
    }

    // MARK: - Public API

    /// Opens the direct WebSocket connection to the server
    public func open() {
        guard configuration.serverURL != nil else {
            print("[WatchDirectTransport] No server URL configured, cannot open")
            return
        }

        guard task == nil else {
            print("[WatchDirectTransport] Connection already exists")
            return
        }

        shouldReconnect = true
        reconnectAttempts = 0
        connect()
    }

    /// Pushes a location fix directly to the server
    public func push(_ fix: LocationFix) {
        guard let task = task, connectionState == .connected else {
            queueMessage(fix)
            return
        }

        sendFix(fix, via: task)
    }

    /// Closes the WebSocket connection
    public func close() {
        shouldReconnect = false
        cancelReconnectTimer()
        stopHeartbeat()

        task?.cancel(with: .goingAway, reason: nil)
        task = nil

        connectionState = .disconnected

        queueLock.lock()
        messageQueue.removeAll()
        queueLock.unlock()

        lastAckAt = nil
        lastAckSeq = nil
        lastSendAt = nil
        recentSendTimes.removeAll()
        totalDropped = 0
        lastErrorMessage = nil
        lastCloseCode = nil
        lastCloseReason = nil
        lastHTTPProbeAt = nil
        lastHTTPProbeStatus = nil
        lastHTTPProbeError = nil
        httpProbeInFlight = false

        emitMetrics()
    }

    /// Check if we have a valid server URL configured
    public var isConfigured: Bool {
        return configuration.serverURL != nil
    }

    // MARK: - Private Methods - Connection

    private func connect() {
        guard let url = configuration.serverURL else { return }
        guard task == nil else { return }

        connectAttemptCount += 1
        lastStateChangedAt = Date()
        connectionState = reconnectAttempts > 0 ? .reconnecting : .connecting

        var request = URLRequest(url: url)
        if #available(watchOS 6.0, *) {
            request.allowsExpensiveNetworkAccess = true
            request.allowsConstrainedNetworkAccess = true
        }

        // Add authentication if provided
        if let token = configuration.bearerToken {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        // Add device identifier
        if let deviceId = configuration.deviceId {
            request.setValue(deviceId, forHTTPHeaderField: "X-Device-Id")
        }

        // Identify as watch client
        request.setValue("watch", forHTTPHeaderField: "X-Client-Type")

        let task = session.webSocketTask(with: request)
        self.task = task
        task.resume()
        startReceiving()
        probeHTTPSReachability()

        print("[WatchDirectTransport] Connecting to \(url.absoluteString) (attempt \(connectAttemptCount))")
    }

    private func handleSuccessfulConnection() {
        didOpenCount += 1
        lastErrorMessage = nil
        lastCloseCode = nil
        lastCloseReason = nil
        connectionState = .connected
        reconnectAttempts = 0

        startHeartbeat()
        flushMessageQueue()
        emitMetrics()
    }

    private func handleConnectionFailure(error: Error) {
        lastErrorMessage = describe(error)
        onError?(error)
        probeHTTPSReachability()

        if task == nil, connectionState == .reconnecting, reconnectTimer != nil {
            emitMetrics()
            return
        }

        guard shouldReconnect else {
            connectionState = .disconnected
            emitMetrics()
            return
        }

        if reconnectAttempts >= configuration.maxReconnectAttempts {
            print("[WatchDirectTransport] Max reconnection attempts reached")
            connectionState = .failed
            shouldReconnect = false
            emitMetrics()
            return
        }

        let delay = calculateBackoffDelay()
        print("[WatchDirectTransport] Reconnecting in \(String(format: "%.1f", delay))s")

        cancelReconnectTimer()
        stopHeartbeat()
        task?.cancel(with: .abnormalClosure, reason: nil)
        task = nil

        connectionState = .reconnecting
        reconnectAttempts += 1
        emitMetrics()

        reconnectTimer = Timer.scheduledTimer(withTimeInterval: delay, repeats: false) { [weak self] _ in
            self?.connect()
        }
    }

    private func calculateBackoffDelay() -> TimeInterval {
        let exponent = min(reconnectAttempts, 4)
        let delay = configuration.initialBackoffDelay * pow(2.0, Double(exponent))
        return min(delay, configuration.maxBackoffDelay)
    }

    private func cancelReconnectTimer() {
        reconnectTimer?.invalidate()
        reconnectTimer = nil
    }

    // MARK: - Private Methods - Messaging

    private func sendFix(_ fix: LocationFix, via task: URLSessionWebSocketTask) {
        do {
            // Unify ingest payload with iOS: send RelayUpdate(remote:).
            let update = RelayUpdate(base: nil, remote: fix, fused: nil, latency: nil)
            let data = try encoder.encode(update)

            task.send(.data(data)) { [weak self] error in
                if let error = error {
                    guard let self else { return }
                    print("[WatchDirectTransport] Send error: \(error.localizedDescription)")
                    self.lastErrorMessage = self.describe(error)
                    self.onError?(error)
                    self.emitMetrics()
                    return
                }
                self?.recordSend()
            }
        } catch {
            print("[WatchDirectTransport] Encode error: \(error.localizedDescription)")
            lastErrorMessage = describe(error)
            onError?(error)
            emitMetrics()
        }
    }

    private func queueMessage(_ fix: LocationFix) {
        do {
            queueLock.lock()
            defer { queueLock.unlock() }

            if messageQueue.count >= maxQueueSize {
                // Remove oldest - for real-time tracking, newest matters most
                messageQueue.removeFirst()
                totalDropped += 1
            }

            messageQueue.append(fix)
        }

        emitMetrics()
    }

    private func flushMessageQueue() {
        queueLock.lock()
        // Send newest first
        let messages = Array(messageQueue.suffix(5).reversed())
        messageQueue.removeAll()
        queueLock.unlock()

        guard !messages.isEmpty, let task = task else { return }

        print("[WatchDirectTransport] Flushing \(messages.count) queued messages")
        for fix in messages {
            sendFix(fix, via: task)
        }
        emitMetrics()
    }

    private func startReceiving() {
        task?.receive { [weak self] result in
            switch result {
            case .failure(let error):
                guard let self else { return }
                print("[WatchDirectTransport] Receive error: \(error.localizedDescription)")
                self.handleConnectionFailure(error: error)
                return
            case .success(let message):
                switch message {
                case .string(let text):
                    if let data = text.data(using: .utf8) {
                        self?.handleServerMessage(data)
                    }
                case .data(let data):
                    self?.handleServerMessage(data)
                @unknown default:
                    break
                }
            }
            self?.startReceiving()
        }
    }

    private func handleServerMessage(_ data: Data) {
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = json["type"] as? String else {
            return
        }

        switch type {
        case "pong":
            handleHeartbeatResponse(json)
        case "ack":
            handleAck(json)
        default:
            break
        }
    }

    private func handleAck(_ json: [String: Any]) {
        lastAckAt = Date()
        if let seq = json["seq"] as? Int {
            lastAckSeq = seq
        } else if let seq = json["seq"] as? NSNumber {
            lastAckSeq = seq.intValue
        }
        emitMetrics()
    }

    // MARK: - Heartbeat

    private func startHeartbeat() {
        stopHeartbeat()
        lastPongTime = Date()
        pendingHeartbeats.removeAll()
        emitMetrics()

        heartbeatTimer = Timer.scheduledTimer(withTimeInterval: heartbeatInterval, repeats: true) { [weak self] _ in
            self?.sendHeartbeat()
        }
    }

    private func stopHeartbeat() {
        heartbeatTimer?.invalidate()
        heartbeatTimer = nil
        pendingHeartbeats.removeAll()
        emitMetrics()
    }

    private func sendHeartbeat() {
        guard let task = task, connectionState == .connected else { return }

        // Check for timeout
        if let lastPong = lastPongTime, Date().timeIntervalSince(lastPong) > heartbeatTimeout {
            print("[WatchDirectTransport] Heartbeat timeout, reconnecting")
            task.cancel(with: .abnormalClosure, reason: nil)
            self.task = nil
            handleConnectionFailure(error: NSError(
                domain: "WatchDirectTransport",
                code: -1,
                userInfo: [NSLocalizedDescriptionKey: "Heartbeat timeout"]
            ))
            return
        }

        let correlationId = UUID().uuidString.prefix(8).lowercased()
        let heartbeat: [String: Any] = [
            "type": "ping",
            "id": String(correlationId),
            "ts": Date().timeIntervalSince1970
        ]

        do {
            let data = try JSONSerialization.data(withJSONObject: heartbeat)
            pendingHeartbeats[String(correlationId)] = Date()

            task.send(.data(data)) { _ in }
        } catch {
            print("[WatchDirectTransport] Heartbeat encode error: \(error.localizedDescription)")
            lastErrorMessage = describe(error)
            emitMetrics()
        }
    }

    private func handleHeartbeatResponse(_ json: [String: Any]) {
        guard let correlationId = json["id"] as? String else { return }

        lastPongTime = Date()

        if let sendTime = pendingHeartbeats.removeValue(forKey: correlationId) {
            let rtt = Date().timeIntervalSince(sendTime) * 1000
            currentLatencyMs = rtt
            print("[WatchDirectTransport] Heartbeat RTT: \(String(format: "%.0f", rtt))ms")
        }
        emitMetrics()
    }

    private func recordSend() {
        let now = Date()
        lastSendAt = now
        recentSendTimes.append(now)
        if recentSendTimes.count > sendRateWindowCount {
            recentSendTimes.removeFirst(recentSendTimes.count - sendRateWindowCount)
        }
        emitMetrics()
    }

    private func computeSendRateHz(now: Date = Date()) -> Double {
        guard recentSendTimes.count >= 2 else { return 0 }
        guard let first = recentSendTimes.first, let last = recentSendTimes.last else { return 0 }
        let span = max(0.001, last.timeIntervalSince(first))
        return Double(recentSendTimes.count - 1) / span
    }

    private func currentQueueDepth() -> Int {
        queueLock.lock()
        let depth = messageQueue.count
        queueLock.unlock()
        return depth
    }

    private func emitMetrics() {
        let metrics = Metrics(
            connectionState: connectionState,
            rttMs: currentLatencyMs,
            lastPongAt: lastPongTime,
            lastAckAt: lastAckAt,
            lastAckSeq: lastAckSeq,
            lastSendAt: lastSendAt,
            queueDepth: currentQueueDepth(),
            totalDropped: totalDropped,
            sendRateHz: computeSendRateHz(),
            connectAttemptCount: connectAttemptCount,
            didOpenCount: didOpenCount,
            lastStateChangedAt: lastStateChangedAt,
            lastErrorMessage: lastErrorMessage,
            lastCloseCode: lastCloseCode,
            lastCloseReason: lastCloseReason,
            lastHTTPProbeAt: lastHTTPProbeAt,
            lastHTTPProbeStatus: lastHTTPProbeStatus,
            lastHTTPProbeError: lastHTTPProbeError,
            networkPathStatus: networkPathStatus,
            networkUsesWiFi: networkUsesWiFi,
            networkUsesCellular: networkUsesCellular,
            networkIsExpensive: networkIsExpensive,
            networkIsConstrained: networkIsConstrained
        )
        onMetricsChanged?(metrics)
    }

    private func startPathMonitoring() {
        pathMonitor.pathUpdateHandler = { [weak self] path in
            DispatchQueue.main.async {
                guard let self else { return }
                switch path.status {
                case .satisfied:
                    self.networkPathStatus = "satisfied"
                case .unsatisfied:
                    self.networkPathStatus = "unsatisfied"
                case .requiresConnection:
                    self.networkPathStatus = "requiresConnection"
                @unknown default:
                    self.networkPathStatus = "unknown"
                }
                self.networkUsesWiFi = path.usesInterfaceType(.wifi)
                self.networkUsesCellular = path.usesInterfaceType(.cellular)
                self.networkIsExpensive = path.isExpensive
                if #available(watchOS 6.0, *) {
                    self.networkIsConstrained = path.isConstrained
                } else {
                    self.networkIsConstrained = false
                }
                print(
                    "[WatchDirectTransport] NWPath status=\(self.networkPathStatus) " +
                    "wifi=\(self.networkUsesWiFi) cellular=\(self.networkUsesCellular) " +
                    "expensive=\(self.networkIsExpensive) constrained=\(self.networkIsConstrained)"
                )
                self.emitMetrics()
            }
        }
        pathMonitor.start(queue: pathMonitorQueue)
    }

    private func probeHTTPSReachability() {
        guard !httpProbeInFlight, let serverURL = configuration.serverURL else { return }
        guard var components = URLComponents(url: serverURL, resolvingAgainstBaseURL: false) else { return }

        switch components.scheme?.lowercased() {
        case "wss":
            components.scheme = "https"
        case "ws":
            components.scheme = "http"
        default:
            return
        }
        guard let url = components.url else { return }

        var request = URLRequest(url: url)
        request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        request.timeoutInterval = 15
        if #available(watchOS 6.0, *) {
            request.allowsExpensiveNetworkAccess = true
            request.allowsConstrainedNetworkAccess = true
        }

        httpProbeInFlight = true
        lastHTTPProbeAt = Date()
        lastHTTPProbeStatus = nil
        lastHTTPProbeError = nil
        emitMetrics()

        session.dataTask(with: request) { [weak self] _, response, error in
            DispatchQueue.main.async {
                guard let self else { return }
                self.httpProbeInFlight = false
                self.lastHTTPProbeAt = Date()
                if let error {
                    self.lastHTTPProbeStatus = nil
                    self.lastHTTPProbeError = self.describe(error)
                    print("[WatchDirectTransport] HTTPS probe error: \(self.lastHTTPProbeError ?? "unknown")")
                } else if let http = response as? HTTPURLResponse {
                    self.lastHTTPProbeStatus = http.statusCode
                    self.lastHTTPProbeError = nil
                    print("[WatchDirectTransport] HTTPS probe status: \(http.statusCode)")
                } else {
                    self.lastHTTPProbeStatus = nil
                    self.lastHTTPProbeError = "No HTTP response"
                    print("[WatchDirectTransport] HTTPS probe returned no HTTP response")
                }
                self.emitMetrics()
            }
        }.resume()
    }

    private func describe(_ error: Error) -> String {
        let nsError = error as NSError
        return "\(nsError.domain) \(nsError.code): \(nsError.localizedDescription)"
    }

    // MARK: - URLSessionWebSocketDelegate

    public func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask, didOpenWithProtocol protocol: String?) {
        print("[WatchDirectTransport] Connected")
        handleSuccessfulConnection()
    }

    public func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask, didCloseWith closeCode: URLSessionWebSocketTask.CloseCode, reason: Data?) {
        let reasonString = reason.flatMap { String(data: $0, encoding: .utf8) }
        lastCloseCode = Int(closeCode.rawValue)
        lastCloseReason = reasonString
        print("[WatchDirectTransport] Closed with code \(closeCode.rawValue), reason: \(reasonString ?? "none")")
        task = nil

        if shouldReconnect {
            handleConnectionFailure(error: NSError(
                domain: "WatchDirectTransport",
                code: Int(closeCode.rawValue),
                userInfo: [NSLocalizedDescriptionKey: "Connection closed: \(reasonString ?? "no reason")"]
            ))
        } else {
            connectionState = .disconnected
        }
    }

    public func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard let webSocketTask = self.task, task === webSocketTask else {
            return
        }
        if let error = error {
            print("[WatchDirectTransport] Task error: \(error.localizedDescription)")
            lastErrorMessage = describe(error)
            self.task = nil
            handleConnectionFailure(error: error)
        }
    }

    public func urlSession(_ session: URLSession, taskIsWaitingForConnectivity task: URLSessionTask) {
        guard let webSocketTask = self.task, task === webSocketTask else {
            return
        }
        lastErrorMessage = "Waiting for network connectivity"
        print("[WatchDirectTransport] Task is waiting for connectivity")
        emitMetrics()
    }
}

#endif
