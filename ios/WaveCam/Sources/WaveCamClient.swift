import Foundation
import Observation

enum WaveCamDefaults {
    static let tetherBaseURLString = "http://172.20.10.8:8088/api/v1"
    static let wifiBaseURLString = "http://192.168.1.155:8088/api/v1"
    static let baseURLString = tetherBaseURLString
    static let legacyLANBaseURLString = wifiBaseURLString
    static let modeKey = "wavecam.mode"
    /// Legacy single-route key. Kept for migration from older builds.
    static let baseURLKey = "wavecam.baseURL"
    static let tetherBaseURLKey = "wavecam.tetherBaseURL"
    static let wifiBaseURLKey = "wavecam.wifiBaseURL"
    static let tokenKey = "wavecam.authToken"
    static let mockFallbackKey = "wavecam.mockFallbackEnabled"

    static var baseURL: URL {
        tetherBaseURL
    }

    static var tetherBaseURL: URL {
        URL(string: tetherBaseURLString)!
    }

    static var wifiBaseURL: URL {
        URL(string: wifiBaseURLString)!
    }

}

extension String {
    /// Single source of truth for autonomous PTZ owners, shared by the owner label
    /// and the action-row autonomy check so they cannot drift apart (review #P2-C).
    static let autonomousPTZOwners: Set<String> = ["vision_follow", "gps_tracker", "testbed"]

    var isAutonomousPTZOwner: Bool { String.autonomousPTZOwners.contains(self) }

    /// Operator-friendly PTZ owner label -- hides engine internals like
    /// vision_follow / testbed / gps_tracker behind a single AUTO (review #16).
    var ptzOwnerLabel: String {
        if isAutonomousPTZOwner { return "AUTO" }
        switch self {
        case "manual": return "MANUAL"
        case "idle", "": return "IDLE"
        default: return uppercased()
        }
    }
}

// MARK: - Status models
// Mirror docs/superpowers/specs/2026-06-01-wavecam-control-api-spec.md  GET /api/v1/status

struct WCStatus: Codable, Sendable {
    var revision: Int
    var timeUnixMs: Int?
    var session: Session
    var safety: Safety
    var ptz: PTZ
    var tracking: Tracking
    var gps: GPS?
    var media: Media?
    var services: [String: String]?
    var network: Network?

    struct Session: Codable, Sendable {
        var state: String
        var mode: String?
        var startedAtUnixMs: Int?
    }
    struct Safety: Codable, Sendable {
        var killed: Bool
        var killReason: String?
        var lastKillAtUnixMs: Int?
    }
    struct PTZ: Codable, Sendable {
        var owner: String
        var enabled: Bool?
        var panTiltCmd: String?
        var zoomState: String?
    }
    struct Tracking: Codable, Sendable {
        var locked: Bool
        var state: String
        var confidence: Double
        var fps: Double
        var hasColor: Bool?
        var hasPerson: Bool?
        var matched: Bool?
    }
    struct GPS: Codable, Sendable {
        var source: String?
        var targetAgeSec: Double?
        var baseAgeSec: Double?
        var distanceM: Double?
        var bearingDeg: Double?
        var stale: Bool?
    }
    struct Media: Codable, Sendable {
        var recording: Bool
        var segmentName: String?
        var freeGb: Double?
    }
    struct Network: Codable, Sendable {
        var cameraLan: Bool?
        var uplink: Bool?
        var cloudflare: Bool?
    }
}

private struct WCControlResponse: Codable, Sendable {
    var ok: Bool?
    var code: String?
    var message: String?
    var status: WCStatus?
}

extension WCStatus {
    /// Canned snapshot for mock mode + SwiftUI previews.
    static func mockTracking(killed: Bool = false) -> WCStatus {
        WCStatus(
            revision: 1834,
            timeUnixMs: 1_780_309_200_123,
            session: .init(state: killed ? "KILLED" : "TRACKING", mode: "vision_gps", startedAtUnixMs: nil),
            safety: .init(killed: killed, killReason: killed ? "operator" : nil, lastKillAtUnixMs: nil),
            ptz: .init(owner: killed ? "idle" : "vision_follow", enabled: true, panTiltCmd: "p4/t0", zoomState: "hold"),
            tracking: .init(locked: !killed, state: killed ? "IDLE" : "LOCKED",
                            confidence: 0.91, fps: 26, hasColor: true, hasPerson: true, matched: true),
            gps: .init(source: "lora", targetAgeSec: 0.9, baseAgeSec: 120,
                       distanceM: 148.2, bearingDeg: 247.1, stale: false),
            media: .init(recording: true, segmentName: "20260601-123000.mp4", freeGb: 377.8),
            services: ["wavecam": "running", "supervisor": "running", "gps_server": "running",
                       "cloudflared": "degraded"],
            network: .init(cameraLan: true, uplink: true, cloudflare: true)
        )
    }
}

/// Subset of GET /api/v1/config we bind in the Tune panel (decoder is convertFromSnakeCase).
struct WCConfig: Codable, Sendable {
    var current: Current
    var supported: Supported?
    var restartRequiredKeys: [String]?

    /// Feature flags advertised by the backend's `supported` block in GET /api/v1/config.
    /// Unknown flags default to nil (absent = unsupported). iOS must never assume a flag is
    /// true without confirmation — a missing key means the endpoint or feature is absent.
    struct Supported: Codable, Sendable {
        var ptzHome: Bool?
    }

    struct Current: Codable, Sendable {
        var ptz: PTZ
        var fusion: Fusion
        var color: ColorCfg
        var detector: Detector
        var web: Web

        struct PTZ: Codable, Sendable {
            var deadzone: Double
            var maxPanSpeed: Int
            var maxTiltSpeed: Int
            var ffGain: Double
            var cinematicZoomEnabled: Bool?
            var zoomTargetFrac: Double?
        }
        struct Fusion: Codable, Sendable {
            var requirePerson: Bool
            var personAimY: Double
        }
        struct ColorCfg: Codable, Sendable {
            var preset: String
        }
        struct Detector: Codable, Sendable {
            var conf: Double
            var personClass: Int
            var model: String?
        }
        struct Web: Codable, Sendable {
            var showMask: Bool
        }
    }
}

// MARK: - Client

/// The single seam to the Orin Control API. `.mock` returns canned, locally-mutable
/// state for offline UI checks; `.live` talks to the real FastAPI surface.
/// Safety: KILL is the only path the UI must never gate behind anything else.
@MainActor
@Observable
final class WaveCamClient {
    enum Mode: String, CaseIterable, Identifiable, Hashable {
        case live
        case mock

        var id: String { rawValue }
    }

    enum ConnectionRoute: String, Hashable {
        case tether
        case wifi
        case custom
        case mock
        case mockFallback
        case offline

        var label: String {
            switch self {
            case .tether: return "USB TETHER"
            case .wifi: return "WIFI"
            case .custom: return "CUSTOM"
            case .mock: return "MOCK"
            case .mockFallback: return "MOCK FALLBACK"
            case .offline: return "OFFLINE"
            }
        }

        var shortLabel: String {
            switch self {
            case .tether: return "USB"
            case .wifi: return "WIFI"
            case .custom: return "CUSTOM"
            case .mock: return "MOCK"
            case .mockFallback: return "MOCK"
            case .offline: return "OFFLINE"
            }
        }
    }

    var mode: Mode
    var baseURL: URL
    var tetherBaseURL: URL
    var wifiBaseURL: URL
    var token: String?
    var mockFallbackEnabled: Bool

    private(set) var status: WCStatus?
    private(set) var connected: Bool = false
    private(set) var activeRoute: ConnectionRoute = .offline
    private(set) var lastError: String?
    /// Last failed *command* (e.g. safety stop). Kept separate from `lastError` so the
    /// 1Hz status poll never wipes a failed-KILL message before the operator sees it (review #2).
    private(set) var lastCommandError: String?
    /// Last failed PTZ/control command. Separate from `lastError` so a transient status-poll
    /// failure can't paint a false "PTZ failed" banner on the PTZ screen (review #P1-C).
    private(set) var lastControlError: String?

    /// Optimistic local KILL latch: set the instant the operator hits Emergency Stop so the
    /// latch overlay appears immediately, before the ~1Hz poll round-trips (review: optimistic KILL).
    private(set) var optimisticKilled = false

    private var mockKilled = false
    private var pollTask: Task<Void, Never>?
    private var nextTetherProbeAt = Date.distantPast
    private let tetherRecheckInterval: TimeInterval = 15

    init(mode: Mode = .live,
         baseURL: URL = WaveCamDefaults.baseURL,
         tetherBaseURL: URL? = nil,
         wifiBaseURL: URL = WaveCamDefaults.wifiBaseURL,
         token: String? = nil,
         mockFallbackEnabled: Bool = false) {
        self.mode = mode
        self.baseURL = baseURL
        self.tetherBaseURL = tetherBaseURL ?? baseURL
        self.wifiBaseURL = wifiBaseURL
        self.token = token
        self.mockFallbackEnabled = mockFallbackEnabled
        self.activeRoute = mode == .mock ? .mock : .offline
    }

    var killed: Bool { status?.safety.killed ?? false }
    /// What the UI treats as latched: the confirmed rig latch OR an operator stop we've
    /// issued but not yet confirmed. Fail-safe — stays latched until resume clears it.
    var effectiveKilled: Bool { optimisticKilled || killed }
    /// True only when the live API failed and we're substituting mock telemetry (NOT
    /// deliberate mock mode), so the UI can warn loudly that the feed is fake. (review H2)
    var isShowingMockData: Bool { mode != .mock && activeRoute == .mockFallback }
    var owner: String { status?.ptz.owner ?? "idle" }

    func configure(mode: Mode, baseURL: URL, token: String?, mockFallbackEnabled: Bool) {
        configure(
            mode: mode,
            tetherBaseURL: baseURL,
            wifiBaseURL: WaveCamDefaults.wifiBaseURL,
            token: token,
            mockFallbackEnabled: mockFallbackEnabled
        )
    }

    func configure(mode: Mode,
                   tetherBaseURL: URL,
                   wifiBaseURL: URL,
                   token: String?,
                   mockFallbackEnabled: Bool) {
        self.mode = mode
        self.tetherBaseURL = tetherBaseURL
        self.wifiBaseURL = wifiBaseURL
        self.baseURL = tetherBaseURL
        self.token = normalizedToken(token)
        self.mockFallbackEnabled = mockFallbackEnabled
        self.activeRoute = mode == .mock ? .mock : .offline
        if mode == .live { startPolling() } else { stopPolling() }
    }

    // MARK: status

    func refresh() async {
        if mode == .mock {
            status = .mockTracking(killed: mockKilled)
            connected = true
            activeRoute = .mock
            lastError = nil
            return
        }
        do {
            let data = try await getWithFallback("status")
            status = try Self.decoder.decode(WCStatus.self, from: data)
            connected = true
            lastError = nil
            if status?.safety.killed == true { optimisticKilled = false }
        } catch {
            if mockFallbackEnabled {
                status = .mockTracking(killed: mockKilled)
                connected = false
                activeRoute = .mockFallback
                lastError = "Live API failed; showing mock data: \(error.localizedDescription)"
                return
            }
            connected = false
            activeRoute = .offline
            lastError = error.localizedDescription
        }
    }

    /// ~1Hz status polling so the HUD reflects live Orin state without a manual
    /// refresh (review #4). Driven by `configure`: active in `.live`, idle in `.mock`.
    private func startPolling(interval: Duration = .seconds(1)) {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refresh()
                try? await Task.sleep(for: interval)
            }
        }
    }

    private func stopPolling() {
        pollTask?.cancel()
        pollTask = nil
    }

    // MARK: safety (highest priority, always allowed)

    func kill(reason: String = "operator") async {
        optimisticKilled = true
        if mode == .mock { mockKilled = true; await refresh(); return }
        do {
            _ = try await post("safety/kill", body: ["reason": reason, "source": "ios_native"])
            lastCommandError = nil
        } catch {
            lastCommandError = "Safety stop not confirmed by Orin: \(error.localizedDescription)"
        }
        await refresh()
    }

    func resume() async {
        optimisticKilled = false
        if mode == .mock { mockKilled = false; await refresh(); return }
        do {
            _ = try await post("safety/resume", body: ["source": "ios_native"])
            lastCommandError = nil
        } catch {
            lastCommandError = "Resume not confirmed by Orin: \(error.localizedDescription)"
        }
        await refresh()
    }

    /// Clear a surfaced command failure once the operator has acknowledged it.
    func clearCommandError() {
        lastCommandError = nil
    }

    // MARK: recording

    func toggleRecording() async {
        if status?.media?.recording == true {
            await stopRecording()
        } else {
            await startRecording()
        }
    }

    func startRecording() async {
        guard mode == .live else { return }
        do {
            _ = try await post("media/record/start", body: ["source": "ios_native"])
            lastCommandError = nil
        } catch {
            lastCommandError = "Recording start not confirmed: \(error.localizedDescription)"
        }
        await refresh()
    }

    func stopRecording() async {
        guard mode == .live else { return }
        do {
            _ = try await post("media/record/stop", body: ["source": "ios_native"])
            lastCommandError = nil
        } catch {
            lastCommandError = "Recording stop not confirmed: \(error.localizedDescription)"
        }
        await refresh()
    }

    // MARK: ptz (owner-gated on the server)

    @discardableResult
    func ptzVelocity(pan: Double, tilt: Double, zoom: Double = 0) async -> Bool {
        guard mode == .live else { return false }
        return await sendControl("ptz/velocity", body: [
            "requested_owner": "manual", "takeover": true,
            "pan": pan, "tilt": tilt, "zoom": zoom,
            "deadman_ms": 800, "source": "ios_native"
        ])
    }

    @discardableResult
    func ptzStop(hold: Bool = true) async -> Bool {
        guard mode == .live else { return false }
        return await sendControl("ptz/stop", body: ["hold": hold, "source": "ios_native"])
    }

    @discardableResult
    func ptzStartAuto() async -> Bool {
        guard mode == .live else { return false }
        return await sendControl("ptz/auto", body: ["source": "ios_native"])
    }

    /// POST /api/v1/ptz/home — VISCA pan/tilt-to-home. Owner-gated; KILL-rejected by the server.
    /// Returns false if the backend refuses (killed, owner_busy, or not connected).
    /// iOS must feature-detect via WCConfig.supported.ptzHome before calling.
    @discardableResult
    func ptzHome() async -> Bool {
        guard mode == .live else { return false }
        return await sendControl("ptz/home", body: [
            "requested_owner": "manual", "takeover": true, "source": "ios_native"
        ])
    }

    @discardableResult
    func zoom(_ value: Double) async -> Bool {
        guard mode == .live else { return false }
        return await sendControl("ptz/zoom", body: [
            "requested_owner": "manual", "takeover": true,
            "mode": "velocity", "value": value, "source": "ios_native"
        ])
    }

    @discardableResult
    func configHot(_ patch: [String: Any]) async -> Bool {
        guard mode == .live else { return false }
        do {
            _ = try await post("config/hot", body: ["patch": patch])
            return true
        } catch {
            lastControlError = error.localizedDescription
            return false
        }
    }

    /// GET /api/v1/config -- current tuning values for the Tune panel. nil in mock/offline.
    func config() async -> WCConfig? {
        guard mode == .live else { return nil }
        do {
            let data = try await getWithFallback("config")
            return try Self.decoder.decode(WCConfig.self, from: data)
        } catch {
            return nil
        }
    }

    /// POST /api/v1/system/restart -- stops PTZ + restarts the vision service (confirm_moving).
    @discardableResult
    func systemRestart() async -> Bool {
        guard mode == .live else { return false }
        do {
            _ = try await post("system/restart", body: ["reason": "ios_native", "confirm_moving": true])
            lastCommandError = nil
            return true
        } catch {
            lastCommandError = "Restart not confirmed: \(error.localizedDescription)"
            return false
        }
    }

    /// MJPEG monitor feed URL (GET /api/v1/preview.mjpeg), nil in mock mode.
    var previewURL: URL? {
        mode == .live ? baseURL.appending(path: "preview.mjpeg") : nil
    }

    // MARK: transport

    private static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    private func authorize(_ req: inout URLRequest) {
        if let token { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
    }

    private func normalizedToken(_ token: String?) -> String? {
        guard let token else { return nil }
        let trimmed = token.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private func getWithFallback(_ path: String) async throws -> Data {
        var lastError: Error?
        for candidate in apiCandidates() {
            do {
                var req = URLRequest(url: candidate.appending(path: path))
                req.timeoutInterval = 3
                authorize(&req)
                let (data, response) = try await URLSession.shared.data(for: req)
                markConnected(to: candidate)
                if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                    throw WaveCamAPIError(statusCode: http.statusCode, data: data)
                }
                return data
            } catch let error as WaveCamAPIError {
                throw error
            } catch {
                if let urlError = error as? URLError, !urlError.isReadRouteFailoverAllowed {
                    throw error
                }
                lastError = error
            }
        }
        throw lastError ?? URLError(.cannotConnectToHost)
    }

    private func apiCandidates() -> [URL] {
        deduped(candidateOrder())
    }

    private func candidateOrder(now: Date = Date()) -> [URL] {
        if activeRoute == .wifi || activeRoute == .custom {
            if now < nextTetherProbeAt {
                return [baseURL, tetherBaseURL, wifiBaseURL]
            }
            nextTetherProbeAt = now.addingTimeInterval(tetherRecheckInterval)
        }
        return [tetherBaseURL, wifiBaseURL]
    }

    private func deduped(_ urls: [URL]) -> [URL] {
        var seen = Set<String>()
        return urls.filter { url in
            let key = url.absoluteString
            guard !seen.contains(key) else { return false }
            seen.insert(key)
            return true
        }
    }

    private func markConnected(to candidate: URL) {
        baseURL = candidate
        activeRoute = route(for: candidate)
        if activeRoute == .tether {
            nextTetherProbeAt = .distantPast
        }
    }

    private func route(for candidate: URL) -> ConnectionRoute {
        if candidate.absoluteString == tetherBaseURL.absoluteString { return .tether }
        if candidate.absoluteString == wifiBaseURL.absoluteString { return .wifi }
        return .custom
    }

    @discardableResult
    private func sendControl(_ path: String, body: [String: Any]) async -> Bool {
        do {
            if !connected {
                await refresh()
            }
            let data = try await post(path, body: body)
            if applyControlResponse(data) == false {
                return false
            }
            lastControlError = nil
            return true
        } catch let error as WaveCamAPIError {
            applyStatusIfPresent(error.data)
            lastControlError = error.localizedDescription
            return false
        } catch {
            lastControlError = error.localizedDescription
            return false
        }
    }

    private func applyControlResponse(_ data: Data) -> Bool {
        guard let response = try? Self.decoder.decode(WCControlResponse.self, from: data) else {
            refreshAfterLegacyResponse()
            lastControlError = "Control response was not parseable."
            return false
        }
        if let status = response.status {
            self.status = status
            connected = true
        } else {
            refreshAfterLegacyResponse()
        }
        guard let ok = response.ok else {
            lastControlError = [response.code, response.message]
                .compactMap(\.self)
                .joined(separator: ": ")
            if lastControlError?.isEmpty != false {
                lastControlError = "Control response did not confirm success."
            }
            return false
        }
        if ok == false {
            lastControlError = [response.code, response.message].compactMap(\.self).joined(separator: ": ")
            return false
        }
        return true
    }

    private func applyStatusIfPresent(_ data: Data) {
        guard let response = try? Self.decoder.decode(WCControlResponse.self, from: data),
              let status = response.status else { return }
        self.status = status
        connected = true
    }

    private func refreshAfterLegacyResponse() {
        Task { [weak self] in
            await self?.refresh()
        }
    }

    @discardableResult
    private func post(_ path: String, body: [String: Any]) async throws -> Data {
        let payload = try JSONSerialization.data(withJSONObject: body)
        var failoverError: Error?
        // Mutating POSTs fail over only on *connection* errors. A reached server that
        // returns an HTTP error propagates immediately -- never re-send to another host
        // (would risk double-applying a command). Same candidates as getWithFallback.
        for candidate in apiCandidates() {
            do {
                var req = URLRequest(url: candidate.appending(path: path))
                req.httpMethod = "POST"
                req.timeoutInterval = 5
                req.setValue("application/json", forHTTPHeaderField: "Content-Type")
                authorize(&req)
                req.httpBody = payload
                let (data, response) = try await URLSession.shared.data(for: req)
                markConnected(to: candidate)
                if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                    throw WaveCamAPIError(statusCode: http.statusCode, data: data)
                }
                return data
            } catch let error as WaveCamAPIError {
                throw error
            } catch let error as URLError {
                guard error.isWriteRouteFailoverAllowed else { throw error }
                failoverError = error
            }
        }
        throw failoverError ?? URLError(.cannotConnectToHost)
    }
}

private extension URLError {
    var isReadRouteFailoverAllowed: Bool {
        // Reads are idempotent, so failing over after a post-send drop is harmless.
        switch code {
        case .timedOut, .networkConnectionLost, .notConnectedToInternet:
            return true
        default:
            return isWriteRouteFailoverAllowed
        }
    }

    var isWriteRouteFailoverAllowed: Bool {
        // Mutating POSTs fail over ONLY on pre-connection errors, where the server
        // provably never received the request. .timedOut / .networkConnectionLost /
        // .notConnectedToInternet can fire AFTER the server already acted, so retrying
        // to the other host would double-apply non-idempotent commands like
        // media/record/* or config/hot. (review C2)
        switch code {
        case .cannotConnectToHost, .cannotFindHost, .dnsLookupFailed:
            return true
        default:
            return false
        }
    }
}

struct WaveCamAPIError: LocalizedError {
    let statusCode: Int
    let data: Data

    var errorDescription: String? {
        guard
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            return "HTTP \(statusCode)"
        }
        let code = object["code"] as? String
        let message = object["message"] as? String
        return [code, message].compactMap(\.self).joined(separator: ": ")
    }
}
