import Foundation
import Observation

enum WaveCamDefaults {
    static let baseURLString = "http://172.20.10.8:8088/api/v1"
    static let legacyLANBaseURLString = "http://192.168.1.155:8088/api/v1"
    static let modeKey = "wavecam.mode"
    static let baseURLKey = "wavecam.baseURL"
    static let tokenKey = "wavecam.authToken"
    static let mockFallbackKey = "wavecam.mockFallbackEnabled"

    static var baseURL: URL {
        URL(string: baseURLString)!
    }

    static var fallbackBaseURLs: [URL] {
        [
            URL(string: legacyLANBaseURLString)
        ].compactMap(\.self)
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
                       "dashboard": "running", "cloudflared": "degraded"],
            network: .init(cameraLan: true, uplink: true, cloudflare: true)
        )
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

    var mode: Mode
    var baseURL: URL
    var token: String?
    var mockFallbackEnabled: Bool

    private(set) var status: WCStatus?
    private(set) var connected: Bool = false
    private(set) var lastError: String?

    private var mockKilled = false
    private var configuredBaseURL: URL

    init(mode: Mode = .live,
         baseURL: URL = WaveCamDefaults.baseURL,
         token: String? = nil,
         mockFallbackEnabled: Bool = false) {
        self.mode = mode
        self.baseURL = baseURL
        self.configuredBaseURL = baseURL
        self.token = token
        self.mockFallbackEnabled = mockFallbackEnabled
    }

    var killed: Bool { status?.safety.killed ?? false }
    var owner: String { status?.ptz.owner ?? "idle" }

    func configure(mode: Mode, baseURL: URL, token: String?, mockFallbackEnabled: Bool) {
        self.mode = mode
        self.configuredBaseURL = baseURL
        self.baseURL = baseURL
        self.token = normalizedToken(token)
        self.mockFallbackEnabled = mockFallbackEnabled
    }

    // MARK: status

    func refresh() async {
        if mode == .mock {
            status = .mockTracking(killed: mockKilled)
            connected = true
            lastError = nil
            return
        }
        do {
            let data = try await getWithFallback("status")
            status = try Self.decoder.decode(WCStatus.self, from: data)
            connected = true
            lastError = nil
        } catch {
            if mockFallbackEnabled {
                status = .mockTracking(killed: mockKilled)
                connected = false
                lastError = "Live API failed; showing mock data: \(error.localizedDescription)"
                return
            }
            connected = false
            lastError = error.localizedDescription
        }
    }

    // MARK: safety (highest priority, always allowed)

    func kill(reason: String = "operator") async {
        if mode == .mock { mockKilled = true; await refresh(); return }
        _ = try? await post("safety/kill", body: ["reason": reason, "source": "ios_native"])
        await refresh()
    }

    func resume() async {
        if mode == .mock { mockKilled = false; await refresh(); return }
        _ = try? await post("safety/resume", body: ["source": "ios_native"])
        await refresh()
    }

    // MARK: ptz (owner-gated on the server)

    func ptzVelocity(pan: Double, tilt: Double, zoom: Double = 0) async {
        guard mode == .live else { return }
        await sendControl("ptz/velocity", body: [
            "requested_owner": "manual", "takeover": true,
            "pan": pan, "tilt": tilt, "zoom": zoom,
            "deadman_ms": 800, "source": "ios_native"
        ])
    }

    func ptzStop(hold: Bool = true) async {
        guard mode == .live else { return }
        await sendControl("ptz/stop", body: ["hold": hold, "source": "ios_native"])
    }

    func ptzStartAuto() async {
        guard mode == .live else { return }
        await sendControl("ptz/auto", body: ["source": "ios_native"])
    }

    func zoom(_ value: Double) async {
        guard mode == .live else { return }
        await sendControl("ptz/zoom", body: [
            "requested_owner": "manual", "takeover": true,
            "mode": "velocity", "value": value, "source": "ios_native"
        ])
    }

    func configHot(_ patch: [String: Double]) async {
        guard mode == .live else { return }
        _ = try? await post("config/hot", body: ["patch": patch])
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
                let (data, _) = try await URLSession.shared.data(for: req)
                baseURL = candidate
                return data
            } catch {
                lastError = error
            }
        }
        throw lastError ?? URLError(.cannotConnectToHost)
    }

    private func apiCandidates() -> [URL] {
        var seen = Set<String>()
        return ([configuredBaseURL] + WaveCamDefaults.fallbackBaseURLs).filter { url in
            let key = url.absoluteString
            guard !seen.contains(key) else { return false }
            seen.insert(key)
            return true
        }
    }

    private func sendControl(_ path: String, body: [String: Any]) async {
        do {
            _ = try await post(path, body: body)
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
    }

    @discardableResult
    private func post(_ path: String, body: [String: Any]) async throws -> Data {
        var req = URLRequest(url: baseURL.appending(path: path))
        req.httpMethod = "POST"
        req.timeoutInterval = 5
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        authorize(&req)
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, response) = try await URLSession.shared.data(for: req)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw WaveCamAPIError(statusCode: http.statusCode, data: data)
        }
        return data
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
