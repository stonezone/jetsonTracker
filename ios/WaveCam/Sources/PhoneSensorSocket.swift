import Foundation

/// Persistent websocket client for the phone-on-tripod sensor stream (Phase-3).
///
/// One connection carries the per-tick fix stream to `/api/v1/sensors/phone/ws`; on any
/// drop it reconnects with capped exponential backoff. This replaces the fire-and-forget
/// HTTP POST, whose failures didn't self-heal (a dropped post just vanished and the rig's
/// sample went stale). The pattern is lifted from the proven legacy `gps-relay-framework`
/// `WebSocketTransport`, trimmed to the reliability essentials — no CBOR, no quality
/// scoring, no app-level heartbeat (URLSession handles protocol keepalive; the receive
/// loop detects a remote close and triggers reconnect).
///
/// Frames are sent as JSON **text** (the backend reads `websocket.receive_json()` in text
/// mode). A frame sent while disconnected is dropped silently — the next tick (~1 s) carries
/// fresh data, so a momentary gap is harmless and never blocks the caller.
@MainActor
final class PhoneSensorSocket: NSObject {
    /// Returns the current ws endpoint + bearer token, or nil when there is nothing to
    /// connect to (mock mode / route not resolved yet). Re-read on every (re)connect so
    /// route failover and token changes are picked up without reconfiguring the socket.
    private let endpoint: () -> (url: URL, token: String?)?

    private var session: URLSession!
    private var task: URLSessionWebSocketTask?
    private(set) var connected = false
    private var running = false
    private var reconnectAttempts = 0
    private var reconnectWork: DispatchWorkItem?
    private static let maxBackoff: TimeInterval = 20

    init(endpoint: @escaping () -> (url: URL, token: String?)?) {
        self.endpoint = endpoint
        super.init()
        self.session = URLSession(configuration: .default, delegate: self, delegateQueue: .main)
    }

    func open() {
        guard !running else { return }
        running = true
        reconnectAttempts = 0
        connect()
    }

    func close() {
        running = false
        reconnectWork?.cancel(); reconnectWork = nil
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        connected = false
    }

    /// Send one fix frame. No-op if not currently connected — self-healing makes a brief
    /// gap harmless, and the call never blocks the 1 Hz publish loop.
    func send(_ body: [String: Any]) {
        guard connected, let task,
              let data = try? JSONSerialization.data(withJSONObject: body),
              let text = String(data: data, encoding: .utf8) else { return }
        task.send(.string(text)) { [weak self] error in
            guard error != nil else { return }
            Task { @MainActor in self?.handleDrop() }
        }
    }

    private func connect() {
        guard running, task == nil else { return }
        guard let ep = endpoint() else {
            // No endpoint yet (route not resolved): retry shortly.
            scheduleReconnect()
            return
        }
        var req = URLRequest(url: ep.url)
        req.timeoutInterval = 10
        if let token = ep.token, !token.isEmpty {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        let t = session.webSocketTask(with: req)
        task = t
        t.resume()
        receiveLoop()
    }

    /// The receive loop must run for the task to observe a remote close; inbound frames
    /// (pong/ack) are drained and ignored.
    private func receiveLoop() {
        task?.receive { [weak self] result in
            Task { @MainActor in
                guard let self else { return }
                switch result {
                case .failure: self.handleDrop()
                case .success: self.receiveLoop()
                }
            }
        }
    }

    private func handleDrop() {
        guard task != nil else { return }   // already torn down this connection
        connected = false
        let dead = task
        task = nil
        dead?.cancel(with: .abnormalClosure, reason: nil)
        scheduleReconnect()
    }

    private func scheduleReconnect() {
        guard running else { return }
        reconnectWork?.cancel()
        let delay = min(pow(2.0, Double(min(reconnectAttempts, 4))), Self.maxBackoff) // 1,2,4,8,16,20
        reconnectAttempts += 1
        let work = DispatchWorkItem { [weak self] in self?.connect() }
        reconnectWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work)
    }
}

extension PhoneSensorSocket: URLSessionWebSocketDelegate {
    nonisolated func urlSession(_ session: URLSession,
                                webSocketTask: URLSessionWebSocketTask,
                                didOpenWithProtocol protocol: String?) {
        Task { @MainActor [weak self] in
            self?.connected = true
            self?.reconnectAttempts = 0
        }
    }

    nonisolated func urlSession(_ session: URLSession,
                                webSocketTask: URLSessionWebSocketTask,
                                didCloseWith closeCode: URLSessionWebSocketTask.CloseCode,
                                reason: Data?) {
        Task { @MainActor [weak self] in self?.handleDrop() }
    }
}
