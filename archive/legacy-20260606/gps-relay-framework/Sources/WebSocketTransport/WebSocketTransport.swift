import Foundation
import LocationCore

#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// MARK: - Connection State

/// Represents the current state of the WebSocket connection
public enum ConnectionState: String, CustomStringConvertible, Sendable {
    case disconnected
    case connecting
    case connected
    case reconnecting
    case failed
    
    public var description: String {
        return rawValue
    }
}

// MARK: - Configuration

/// Payload encoding format for WebSocket messages
public enum PayloadEncoding: Sendable {
    /// JSON encoding (human readable, larger payload ~200+ bytes)
    case json
    /// CBOR encoding (binary, ~40% smaller payload ~80-90 bytes)
    /// Recommended for cellular connections to reduce data usage and latency
    case cbor
}

/// Configuration for WebSocket transport behavior
public struct WebSocketTransportConfiguration: @unchecked Sendable {
    /// Maximum number of reconnection attempts before giving up
    public var maxReconnectAttempts: Int

    /// Initial backoff delay in seconds
    public var initialBackoffDelay: TimeInterval

    /// Maximum backoff delay in seconds
    public var maxBackoffDelay: TimeInterval

    /// Maximum size of the message queue
    public var maxQueueSize: Int

    /// Custom HTTP headers to include in connection request
    public var customHeaders: [String: String]

    /// Bearer token for authentication (automatically adds Authorization header)
    public var bearerToken: String?

    /// Custom URLSessionConfiguration for advanced TLS settings
    public var sessionConfiguration: URLSessionConfiguration

    /// Allow ws:// connections (primarily for local development). Defaults to false.
    public var allowInsecureConnections: Bool

    /// Payload encoding format. CBOR is ~40% smaller than JSON.
    /// Recommended for cellular connections. Default is JSON for compatibility.
    public var payloadEncoding: PayloadEncoding

    public init(
        maxReconnectAttempts: Int = 10,
        initialBackoffDelay: TimeInterval = 1.0,
        maxBackoffDelay: TimeInterval = 30.0,
        maxQueueSize: Int = 1000,
        customHeaders: [String: String] = [:],
        bearerToken: String? = nil,
        sessionConfiguration: URLSessionConfiguration = .default,
        allowInsecureConnections: Bool = false,
        payloadEncoding: PayloadEncoding = .json
    ) {
        self.maxReconnectAttempts = maxReconnectAttempts
        self.initialBackoffDelay = initialBackoffDelay
        self.maxBackoffDelay = maxBackoffDelay
        self.maxQueueSize = maxQueueSize
        self.customHeaders = customHeaders
        self.bearerToken = bearerToken
        self.sessionConfiguration = sessionConfiguration
        self.allowInsecureConnections = allowInsecureConnections
        self.payloadEncoding = payloadEncoding
    }

    /// Configuration optimized for cellular connections (CBOR encoding)
    public static func cellular(
        bearerToken: String? = nil,
        customHeaders: [String: String] = [:]
    ) -> WebSocketTransportConfiguration {
        WebSocketTransportConfiguration(
            customHeaders: customHeaders,
            bearerToken: bearerToken,
            payloadEncoding: .cbor
        )
    }
}

// MARK: - Delegate Protocol

/// Delegate protocol for monitoring WebSocket connection health
@available(iOS 13.0, watchOS 6.0, macOS 10.15, *)
public protocol WebSocketTransportDelegate: AnyObject {
    /// Called when the connection state changes
    /// - Parameters:
    ///   - transport: The WebSocket transport instance
    ///   - state: The new connection state
    func webSocketTransport(_ transport: WebSocketTransport, didChangeState state: ConnectionState)
    
    /// Called when an error is encountered
    /// - Parameters:
    ///   - transport: The WebSocket transport instance
    ///   - error: The error that occurred
    func webSocketTransport(_ transport: WebSocketTransport, didEncounterError error: Error)
}

// MARK: - WebSocket Transport

@available(iOS 13.0, watchOS 6.0, macOS 10.15, *)
public final class WebSocketTransport: NSObject, LocationTransport, URLSessionWebSocketDelegate, @unchecked Sendable {
    // Note: @unchecked Sendable because we manage thread safety via NSLock (queueLock)
    // and all mutable state is accessed on the main queue via URLSession delegate
    
    // MARK: - Properties
    
    private let url: URL
    private var session: URLSession!
    private var task: URLSessionWebSocketTask?
    private let jsonEncoder = JSONEncoder()
    private let cborEncoder = GPSCBOREncoder()
    private let configuration: WebSocketTransportConfiguration
    
    /// Current connection state
    private(set) public var connectionState: ConnectionState = .disconnected {
        didSet {
            if oldValue != connectionState {
                NSLog("[WebSocketTransport] State changed: %@ -> %@", oldValue.description, connectionState.description)
                delegate?.webSocketTransport(self, didChangeState: connectionState)
            }
        }
    }
    
    /// Delegate for connection health monitoring
    public weak var delegate: WebSocketTransportDelegate?
    
    // Reconnection state
    private var reconnectAttempts: Int = 0
    private var reconnectTimer: Timer?
    private var shouldReconnect: Bool = false
    
    // Message queue for when disconnected
    private var messageQueue: [RelayUpdate] = []
    private let queueLock = NSLock()
    
    // Issue #5: Application-level heartbeat tracking
    private var heartbeatTimer: Timer?
    private var lastPongTime: Date?
    private var pendingHeartbeats: [String: Date] = [:]  // correlationId -> sendTime
    private let heartbeatInterval: TimeInterval = 5.0
    private let heartbeatTimeout: TimeInterval = 15.0
    
    /// Current measured round-trip latency in milliseconds
    public private(set) var currentLatencyMs: Double = 0
    
    // Issue #19: Connection quality tracking for automatic mode degradation
    /// Connection quality score (0.0 = poor, 1.0 = excellent)
    public private(set) var connectionQuality: Double = 1.0
    
    /// Delegate callback when quality degrades significantly
    public var onQualityDegraded: ((Double) -> Void)?
    
    private var latencyHistory: [Double] = []
    private var reconnectHistory: [Date] = []
    private let latencyHistorySize = 10
    private let reconnectHistoryWindow: TimeInterval = 300  // 5 minutes
    
    // MARK: - Initialization
    
    /// Initialize WebSocket transport with URL and optional configuration
    /// - Parameters:
    ///   - url: WebSocket server URL
    ///   - configuration: Transport configuration (uses defaults if not provided)
    public init(url: URL, configuration: WebSocketTransportConfiguration = WebSocketTransportConfiguration()) {
        self.url = url
        self.configuration = configuration
        super.init()
        
        // Configure session
        self.session = URLSession(
            configuration: configuration.sessionConfiguration,
            delegate: self,
            delegateQueue: .main
        )
        
        jsonEncoder.outputFormatting = .withoutEscapingSlashes
        // Ensure RelayUpdate dates (e.g., relayTimestamp) are encoded consistently as unix ms.
        jsonEncoder.dateEncodingStrategy = .millisecondsSince1970
    }
    
    /// Legacy initializer for backward compatibility
    /// - Parameters:
    ///   - url: WebSocket server URL
    ///   - sessionConfiguration: URLSession configuration
    public convenience init(url: URL, sessionConfiguration: URLSessionConfiguration = .default) {
        var config = WebSocketTransportConfiguration()
        config.sessionConfiguration = sessionConfiguration
        self.init(url: url, configuration: config)
    }
    
    // MARK: - Public API
    
    /// Opens the WebSocket connection
    public func open() {
        guard task == nil else {
            NSLog("[WebSocketTransport] Connection already exists")
            return
        }
        
        shouldReconnect = true
        reconnectAttempts = 0
        connect()
    }
    
    /// Pushes a relay update to the server
    /// - Parameter update: The payload containing base/remote fixes
    public func push(_ update: RelayUpdate) {
        guard let task = task, connectionState == .connected else {
            // Queue message if not connected
            queueMessage(update)
            return
        }

        sendMessage(update, via: task)
    }
    
    /// Closes the WebSocket connection
    public func close() {
        shouldReconnect = false
        cancelReconnectTimer()
        stopHeartbeat()  // Issue #5
        
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        
        connectionState = .disconnected
        
        // Clear message queue
        queueLock.lock()
        messageQueue.removeAll()
        queueLock.unlock()
    }
    
    // MARK: - Private Methods - Connection Management
    
    private func connect() {
        guard task == nil else { return }
        
        connectionState = reconnectAttempts > 0 ? .reconnecting : .connecting
        
        guard validateURLScheme() else { return }
        
        // Create request with custom headers
        var request = URLRequest(url: url)
        
        // Add bearer token if provided
        if let bearerToken = configuration.bearerToken {
            request.setValue("Bearer \(bearerToken)", forHTTPHeaderField: "Authorization")
        }
        
        // Add custom headers
        for (key, value) in configuration.customHeaders {
            request.setValue(value, forHTTPHeaderField: key)
        }
        
        // Create and start WebSocket task
        let task = session.webSocketTask(with: request)
        self.task = task
        task.resume()
        receivePings()
        
        NSLog("[WebSocketTransport] Connecting to %@ (attempt %d)", url.absoluteString, reconnectAttempts + 1)
    }
    
    private func validateURLScheme() -> Bool {
        guard let scheme = url.scheme?.lowercased() else {
            reportValidationError(message: "WebSocket URL is missing a scheme (ws:// or wss://)")
            return false
        }

        switch scheme {
        case "wss":
            return true
        case "ws":
            if configuration.allowInsecureConnections {
                NSLog("[WebSocketTransport] ⚠️ Using insecure ws:// connection to %@", url.absoluteString)
                return true
            } else {
                reportValidationError(message: "Insecure ws:// connections are disabled. Enable allowInsecureConnections for development.")
                return false
            }
        default:
            reportValidationError(message: "Unsupported WebSocket scheme '\(scheme)'. Use ws:// or wss://.")
            return false
        }
    }

    private func reportValidationError(message: String) {
        let error = NSError(
            domain: "WebSocketTransport",
            code: -1000,
            userInfo: [NSLocalizedDescriptionKey: message]
        )
        delegate?.webSocketTransport(self, didEncounterError: error)
        NSLog("[WebSocketTransport] Validation error: %@", message)
        shouldReconnect = false
        connectionState = .failed
    }

    // Issue #12: Error classification for adaptive backoff
    private enum ErrorCategory {
        case network      // DNS, connectivity, timeout
        case auth         // 401, 403, auth failures
        case serverError  // 5xx errors
        case clientError  // 4xx errors (except auth)
        case unknown
        
        var shouldRetry: Bool {
            switch self {
            case .network, .serverError, .unknown: return true
            case .auth, .clientError: return false  // Don't retry auth/client errors
            }
        }
        
        var backoffMultiplier: Double {
            switch self {
            case .network: return 1.0      // Standard backoff
            case .serverError: return 1.5  // Server overloaded, back off more
            case .auth: return 0           // No retry
            case .clientError: return 0    // No retry
            case .unknown: return 1.0
            }
        }
    }
    
    private func classifyError(_ error: Error) -> ErrorCategory {
        let nsError = error as NSError
        
        // Check for URL errors
        switch nsError.code {
        case NSURLErrorNotConnectedToInternet,
             NSURLErrorNetworkConnectionLost,
             NSURLErrorDNSLookupFailed,
             NSURLErrorCannotFindHost,
             NSURLErrorCannotConnectToHost,
             NSURLErrorTimedOut:
            return .network
        case NSURLErrorUserAuthenticationRequired:
            return .auth
        default:
            break
        }
        
        // Check for WebSocket close codes in error info
        if let closeCode = nsError.userInfo["closeCode"] as? Int {
            switch closeCode {
            case 1008: return .auth       // Policy violation (often auth)
            case 1000...1003: return .clientError
            case 1011, 1012, 1013: return .serverError
            default: return .unknown
            }
        }
        
        return .unknown
    }
    
    private func handleConnectionFailure(error: Error) {
        delegate?.webSocketTransport(self, didEncounterError: error)
        
        // Issue #12: Classify error for adaptive backoff
        let errorCategory = classifyError(error)
        
        guard shouldReconnect else {
            connectionState = .disconnected
            return
        }
        
        // Issue #12: Don't retry auth/client errors
        if !errorCategory.shouldRetry {
            NSLog("[WebSocketTransport] Non-retryable error (%@): %@", String(describing: errorCategory), error.localizedDescription)
            connectionState = .failed
            shouldReconnect = false
            return
        }
        
        if reconnectAttempts >= configuration.maxReconnectAttempts {
            NSLog("[WebSocketTransport] Max reconnection attempts (%d) reached", configuration.maxReconnectAttempts)
            connectionState = .failed
            shouldReconnect = false
            return
        }

        // Prevent reconnect timer buildup on repeated callbacks.
        cancelReconnectTimer()

        // Ensure a stalled/half-open task doesn't block reconnect attempts.
        // connect() requires task == nil, so aggressively cancel and clear.
        stopHeartbeat()
        task?.cancel(with: .abnormalClosure, reason: nil)
        task = nil

        // Schedule reconnection with adaptive exponential backoff
        let backoffDelay = calculateBackoffDelay(multiplier: errorCategory.backoffMultiplier)
        NSLog("[WebSocketTransport] Scheduling reconnection in %.1f seconds (category: %@)", backoffDelay, String(describing: errorCategory))
        
        connectionState = .reconnecting
        reconnectAttempts += 1
        
        // Issue #19: Track reconnection for quality calculation
        recordReconnectAttempt()
        
        reconnectTimer = Timer.scheduledTimer(
            withTimeInterval: backoffDelay,
            repeats: false
        ) { [weak self] _ in
            self?.connect()
        }
    }
    
    private func calculateBackoffDelay(multiplier: Double = 1.0) -> TimeInterval {
        // Exponential backoff: 1s, 2s, 4s, 8s, 16s, max 30s
        // Issue #12: Apply category-based multiplier
        let exponent = min(reconnectAttempts, 5) // Cap at 2^5 = 32
        let baseDelay = configuration.initialBackoffDelay * pow(2.0, Double(exponent))
        let adjustedDelay = baseDelay * max(multiplier, 0.5)  // Never less than 50% of base
        return min(adjustedDelay, configuration.maxBackoffDelay)
    }
    
    private func cancelReconnectTimer() {
        reconnectTimer?.invalidate()
        reconnectTimer = nil
    }
    
    private func handleSuccessfulConnection() {
        connectionState = .connected
        reconnectAttempts = 0
        
        // Issue #5: Start application-level heartbeat
        startHeartbeat()
        
        // Flush queued messages
        flushMessageQueue()
    }
    
    // MARK: - Private Methods - Message Handling
    
    private func sendMessage(_ update: RelayUpdate, via task: URLSessionWebSocketTask) {
        do {
            let data: Data
            switch configuration.payloadEncoding {
            case .json:
                data = try jsonEncoder.encode(update)
            case .cbor:
                data = try cborEncoder.encode(update)
            }

            task.send(.data(data)) { [weak self] error in
                guard let self, let error = error else { return }
                NSLog("[WebSocketTransport] Send error: %@", String(describing: error))
                self.delegate?.webSocketTransport(self, didEncounterError: error)
            }
        } catch {
            NSLog("[WebSocketTransport] Encoding error: %@", String(describing: error))
            delegate?.webSocketTransport(self, didEncounterError: error)
        }
    }

    private func queueMessage(_ update: RelayUpdate) {
        queueLock.lock()
        defer { queueLock.unlock() }

        // Enforce queue size limit
        if messageQueue.count >= configuration.maxQueueSize {
            // Remove oldest message
            messageQueue.removeFirst()
            NSLog("[WebSocketTransport] Queue full, dropping oldest message")
        }

        messageQueue.append(update)
        NSLog("[WebSocketTransport] Queued message (queue size: %d)", messageQueue.count)
    }

    private func flushMessageQueue() {
        queueLock.lock()
        // Reverse to send newest first - for real-time tracking, fresh data matters most
        let allMessages = Array(messageQueue.reversed())
        // Only flush most recent messages to avoid latency buildup
        let maxFlushSize = 10
        let messagesToSend = Array(allMessages.prefix(maxFlushSize))
        let droppedCount = allMessages.count - messagesToSend.count
        messageQueue.removeAll()
        queueLock.unlock()
        
        guard !messagesToSend.isEmpty, let task = task else { return }
        
        if droppedCount > 0 {
            NSLog("[WebSocketTransport] Flushing %d messages (newest first), dropped %d stale", 
                  messagesToSend.count, droppedCount)
        } else {
            NSLog("[WebSocketTransport] Flushing %d queued messages (newest first)", messagesToSend.count)
        }
        
        for update in messagesToSend {
            sendMessage(update, via: task)
        }
    }
    
    private func receivePings() {
        guard let task = task else { return }
        task.receive { [weak self] result in
            switch result {
            case .failure(let error):
                NSLog("[WebSocketTransport] Receive error: %@", String(describing: error))
                self?.handleReceiveError(error)
            case .success(let message):
                // Log received message for debugging
                switch message {
                case .string(let text):
                    NSLog("[WebSocketTransport] Received text: %@", text)
                    // Issue #5: Check for heartbeat pong in text format
                    if let data = text.data(using: .utf8) {
                        self?.handleHeartbeatResponse(data)
                    }
                case .data(let data):
                    NSLog("[WebSocketTransport] Received data: %d bytes", data.count)
                    // Issue #5: Check for heartbeat pong
                    self?.handleHeartbeatResponse(data)
                @unknown default:
                    break
                }
            }
            self?.receivePings()
        }
    }
    
    private func handleReceiveError(_ error: Error) {
        delegate?.webSocketTransport(self, didEncounterError: error)
        
        // Connection likely closed, will be handled in didCompleteWithError
    }
    
    // MARK: - Issue #5: Application-Level Heartbeat
    
    private func startHeartbeat() {
        stopHeartbeat()
        lastPongTime = Date()
        pendingHeartbeats.removeAll()
        
        DispatchQueue.main.async { [weak self] in
            guard let self = self else { return }
            self.heartbeatTimer = Timer.scheduledTimer(withTimeInterval: self.heartbeatInterval, repeats: true) { [weak self] _ in
                self?.sendHeartbeat()
            }
        }
        NSLog("[WebSocketTransport] Heartbeat started (interval: %.1fs)", heartbeatInterval)
    }
    
    private func stopHeartbeat() {
        heartbeatTimer?.invalidate()
        heartbeatTimer = nil
        pendingHeartbeats.removeAll()
    }
    
    private func sendHeartbeat() {
        guard let task = task, connectionState == .connected else { return }
        
        // Check for heartbeat timeout
        if let lastPong = lastPongTime, Date().timeIntervalSince(lastPong) > heartbeatTimeout {
            NSLog("[WebSocketTransport] ⚠️ Heartbeat timeout (%.1fs since last pong), reconnecting", Date().timeIntervalSince(lastPong))
            handleConnectionFailure(error: NSError(
                domain: "WebSocketTransport",
                code: -2,
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
            
            task.send(.data(data)) { [weak self] error in
                if let error = error {
                    NSLog("[WebSocketTransport] Heartbeat send error: %@", error.localizedDescription)
                }
                _ = self
            }
        } catch {
            NSLog("[WebSocketTransport] Heartbeat encode error: %@", error.localizedDescription)
        }
    }
    
    private func handleHeartbeatResponse(_ data: Data) {
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = json["type"] as? String,
              type == "pong",
              let correlationId = json["id"] as? String else {
            return
        }
        
        lastPongTime = Date()
        
        if let sendTime = pendingHeartbeats.removeValue(forKey: correlationId) {
            let rtt = Date().timeIntervalSince(sendTime) * 1000
            currentLatencyMs = rtt
            
            // Issue #19: Track latency history for quality calculation
            latencyHistory.append(rtt)
            if latencyHistory.count > latencyHistorySize {
                latencyHistory.removeFirst()
            }
            updateConnectionQuality()
            
            NSLog("[WebSocketTransport] Heartbeat pong received, RTT: %.0fms, quality: %.2f", rtt, connectionQuality)
        }
    }
    
    // MARK: - Issue #19: Connection Quality Tracking
    
    private func updateConnectionQuality() {
        // Calculate quality based on:
        // 1. Average latency (lower is better)
        // 2. Latency variance (lower is better)
        // 3. Recent reconnection frequency (fewer is better)
        
        var quality = 1.0
        
        // Latency component (50% weight)
        if !latencyHistory.isEmpty {
            let avgLatency = latencyHistory.reduce(0, +) / Double(latencyHistory.count)
            // Scale: 0-50ms = excellent, 50-200ms = good, 200-500ms = fair, >500ms = poor
            let latencyScore = max(0, min(1, 1 - (avgLatency - 50) / 450))
            quality *= (0.5 + 0.5 * latencyScore)
        }
        
        // Variance component (25% weight)
        if latencyHistory.count >= 3 {
            let avg = latencyHistory.reduce(0, +) / Double(latencyHistory.count)
            let variance = latencyHistory.map { pow($0 - avg, 2) }.reduce(0, +) / Double(latencyHistory.count)
            let stdDev = sqrt(variance)
            // Low variance is good - scale: 0-20ms = excellent, >100ms = poor
            let varianceScore = max(0, min(1, 1 - stdDev / 100))
            quality *= (0.75 + 0.25 * varianceScore)
        }
        
        // Reconnection frequency component (25% weight)
        let now = Date()
        reconnectHistory = reconnectHistory.filter { now.timeIntervalSince($0) <= reconnectHistoryWindow }
        let reconnectScore = max(0, min(1, 1 - Double(reconnectHistory.count) / 5))
        quality *= (0.75 + 0.25 * reconnectScore)
        
        let previousQuality = connectionQuality
        connectionQuality = quality
        
        // Notify if quality dropped significantly
        if previousQuality > 0.5 && connectionQuality <= 0.5 {
            NSLog("[WebSocketTransport] ⚠️ Connection quality degraded: %.2f -> %.2f", previousQuality, connectionQuality)
            onQualityDegraded?(connectionQuality)
        }
    }
    
    private func recordReconnectAttempt() {
        reconnectHistory.append(Date())
        updateConnectionQuality()
    }
    
    // MARK: - URLSessionWebSocketDelegate
    
    public func urlSession(
        _ session: URLSession,
        webSocketTask: URLSessionWebSocketTask,
        didOpenWithProtocol protocol: String?
    ) {
        NSLog("[WebSocketTransport] Connected to %@", webSocketTask.currentRequest?.url?.absoluteString ?? url.absoluteString)
        handleSuccessfulConnection()
    }
    
    public func urlSession(
        _ session: URLSession,
        webSocketTask: URLSessionWebSocketTask,
        didCloseWith closeCode: URLSessionWebSocketTask.CloseCode,
        reason: Data?
    ) {
        let reasonString = reason.flatMap { String(data: $0, encoding: .utf8) } ?? "none"
        NSLog("[WebSocketTransport] Closed with code %d, reason: %@", closeCode.rawValue, reasonString)
        
        task = nil
        
        if shouldReconnect {
            // Treat as connection failure and attempt reconnection
            let error = NSError(
                domain: "WebSocketTransport",
                code: Int(closeCode.rawValue),
                userInfo: [NSLocalizedDescriptionKey: "WebSocket closed with code \(closeCode.rawValue)"]
            )
            handleConnectionFailure(error: error)
        } else {
            connectionState = .disconnected
        }
    }
    
    public func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        if let error = error {
            NSLog("[WebSocketTransport] Task completed with error: %@", String(describing: error))
            self.task = nil
            handleConnectionFailure(error: error)
        } else {
            NSLog("[WebSocketTransport] Task completed cleanly")
            self.task = nil
            
            if shouldReconnect {
                // Unexpected clean completion, reconnect
                let error = NSError(
                    domain: "WebSocketTransport",
                    code: -1,
                    userInfo: [NSLocalizedDescriptionKey: "WebSocket disconnected unexpectedly"]
                )
                handleConnectionFailure(error: error)
            } else {
                connectionState = .disconnected
            }
        }
    }
}
