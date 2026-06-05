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
        var presets: Bool?
        var logs: Bool?
        var cinematicZoom: Bool?
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
            var ffDeadzoneMult: Double?
            var minSpeed: Int?
            var commandMinInterval: Double?
            var invertTilt: Bool?
            var invertPan: Bool?
        }
        struct Fusion: Codable, Sendable {
            var requirePerson: Bool
            var personAimY: Double
            var lockThreshold: Double?
            var unlockThreshold: Double?
            var matchDist: Double?
        }
        struct ColorCfg: Codable, Sendable {
            var preset: String
            var minArea: Int?
            var maxArea: Int?
            var morphKernel: Int?
        }
        struct Detector: Codable, Sendable {
            var conf: Double
            var personClass: Int
            var model: String?
            var everyN: Int?
        }
        struct Web: Codable, Sendable {
            var showMask: Bool
            var jpegQuality: Int?
            var showHud: Bool?
        }
    }
}

// MARK: - Calibration models

/// Persisted calibration entry for a single axis returned inside GET /api/v1/calibration.
struct WCCalibrationEntry: Codable, Sendable {
    var capturedAtUnixMs: Int?
    var source: String?
    var note: String?
    // Axis-specific fields – only one will be present per entry.
    var headingDeg: Double?
    var tiltDeg: Double?
    var zoomFovDeg: Double?
}

/// The `calibration` sub-object from GET /api/v1/calibration (and POST responses).
/// Field names match snake_case JSON decoded via .convertFromSnakeCase.
struct WCCalibrationState: Codable, Sendable {
    var referenceHeading: Double?
    var heading: WCCalibrationEntry?
    var tilt: WCCalibrationEntry?
    var zoom: WCCalibrationEntry?
    var updatedAtUnixMs: Int?
}

/// Envelope returned by GET /api/v1/calibration and each POST /calibration/* endpoint.
private struct WCCalibrationResponse: Codable, Sendable {
    var ok: Bool?
    var code: String?
    var message: String?
    var calibration: WCCalibrationState?
    var status: WCStatus?
}

/// Structured refusal reason surfaced to the UI on a failed calibration capture.
enum WaveCamCalibrationError: LocalizedError, Sendable {
    case killed
    case ownerBusy
    case unavailable   // endpoint not present (backend not yet deployed)
    case httpError(Int, String?)
    case networkError(String)

    var errorDescription: String? {
        switch self {
        case .killed:
            return "KILL is latched — resume before capturing calibration."
        case .ownerBusy:
            return "Another PTZ owner holds the camera. Try again or take over."
        case .unavailable:
            return "On-device calibration requires the latest Orin build — checklist only for now."
        case let .httpError(code, msg):
            return msg.map { "\($0) (HTTP \(code))" } ?? "HTTP \(code)"
        case let .networkError(desc):
            return "Network error: \(desc)"
        }
    }
}

// MARK: - Preset models

/// A heterogeneous JSON value that appears in preset `values` dicts.
/// The backend can store any scalar type (String, Int, Double, Bool) per config key.
/// Using a typed enum avoids `Any` casts and keeps Codable conformance clean.
enum JSONValue: Codable, Sendable, Equatable {
    case string(String)
    case int(Int)
    case double(Double)
    case bool(Bool)

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let v = try? c.decode(Bool.self)   { self = .bool(v);   return }
        if let v = try? c.decode(Int.self)    { self = .int(v);    return }
        if let v = try? c.decode(Double.self) { self = .double(v); return }
        if let v = try? c.decode(String.self) { self = .string(v); return }
        throw DecodingError.dataCorruptedError(in: c, debugDescription: "Unsupported JSON value type")
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let v): try c.encode(v)
        case .int(let v):    try c.encode(v)
        case .double(let v): try c.encode(v)
        case .bool(let v):   try c.encode(v)
        }
    }

    /// Convenience: produce a JSONValue from a Bool @State that a Tune slider wrote.
    static func from(_ any: Any) -> JSONValue? {
        switch any {
        case let v as Bool:   return .bool(v)
        case let v as Int:    return .int(v)
        case let v as Double: return .double(v)
        case let v as String: return .string(v)
        default: return nil
        }
    }

    /// The underlying JSON-native value, for JSONSerialization request bodies.
    var rawValue: Any {
        switch self {
        case .string(let v): return v
        case .int(let v):    return v
        case .double(let v): return v
        case .bool(let v):   return v
        }
    }
}

struct WCPreset: Codable, Sendable, Identifiable {
    var name: String
    var builtin: Bool
    var restartRequired: Bool
    var values: [String: JSONValue]

    var id: String { name }
}

// H4: decode in an extension so the synthesized memberwise init survives. Tolerate
// a partial response — a missing flag must not throw the whole decode (which would
// otherwise surface as a false "operation failed").
extension WCPreset {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decode(String.self, forKey: .name)
        builtin = try c.decodeIfPresent(Bool.self, forKey: .builtin) ?? false
        restartRequired = try c.decodeIfPresent(Bool.self, forKey: .restartRequired) ?? false
        values = try c.decodeIfPresent([String: JSONValue].self, forKey: .values) ?? [:]
    }
}

private struct WCPresetsResponse: Codable, Sendable {
    var presets: [WCPreset]
}

struct WCPresetApplyResult: Codable, Sendable {
    var ok: Bool
    var applied: [String: JSONValue]
    var restartRequired: Bool
    var restartKeys: [String]
}

// H4: only `ok` is essential; default the rest so a partial response decodes
// instead of throwing → false "apply failed". (Extension preserves memberwise init.)
extension WCPresetApplyResult {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = try c.decode(Bool.self, forKey: .ok)
        applied = try c.decodeIfPresent([String: JSONValue].self, forKey: .applied) ?? [:]
        restartRequired = try c.decodeIfPresent(Bool.self, forKey: .restartRequired) ?? false
        restartKeys = try c.decodeIfPresent([String].self, forKey: .restartKeys) ?? []
    }
}

// MARK: - Media models

/// One recording returned by GET /api/v1/media/list.
/// Field names match the snake_case JSON after .convertFromSnakeCase.
struct WCMediaFile: Codable, Sendable, Identifiable {
    var name: String
    var sizeBytes: Int
    var ctimeUnixMs: Int

    /// Stable identity: the filename is unique within a recording directory.
    var id: String { name }

    /// Creation date derived from the Unix-ms timestamp.
    var createdAt: Date { Date(timeIntervalSince1970: Double(ctimeUnixMs) / 1000) }
}

private struct WCMediaListResponse: Codable, Sendable {
    var ok: Bool?
    var files: [WCMediaFile]
}

// H4: a missing `files` decodes to empty rather than throwing.
extension WCMediaListResponse {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = try c.decodeIfPresent(Bool.self, forKey: .ok)
        files = try c.decodeIfPresent([WCMediaFile].self, forKey: .files) ?? []
    }
}

// MARK: - Log models

/// One log line returned by GET /api/v1/logs.
/// Field names decoded via .convertFromSnakeCase.
struct WCLogLine: Codable, Sendable, Identifiable {
    var tsUnixMs: Int
    var level: String
    var source: String
    var message: String

    /// Stable row identity: timestamp-ms + source prevents collisions between two lines
    /// at the same millisecond from different sources.
    var id: String { "\(tsUnixMs)-\(source)" }

    var timestamp: Date { Date(timeIntervalSince1970: Double(tsUnixMs) / 1000) }
}

private struct WCLogsResponse: Codable, Sendable {
    var lines: [WCLogLine]
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
            // Fresh status is authoritative — drop the optimistic latch and let
            // `killed` drive `effectiveKilled` (:450), so a FAILED kill can't leave a
            // false "STOP LATCHED" while the camera still moves (error alert covers it).
            optimisticKilled = false
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
    func configHot(_ patch: [String: JSONValue]) async -> Bool {
        guard mode == .live else { return false }
        do {
            _ = try await post("config/hot", body: ["patch": patch.mapValues(\.rawValue)])
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

    // MARK: calibration (owner-gated, PTZ auth, KILL-rejected on the backend)

    /// GET /api/v1/calibration — returns nil when the endpoint is absent (not yet deployed)
    /// or on any network error. The `.unavailable` case is detected by a 404 status code.
    func calibrationState() async -> WCCalibrationState? {
        guard mode == .live else { return nil }
        do {
            let data = try await getWithFallback("calibration")
            let response = try Self.decoder.decode(WCCalibrationResponse.self, from: data)
            return response.calibration
        } catch let error as WaveCamAPIError where error.statusCode == 404 {
            return nil
        } catch {
            return nil
        }
    }

    /// True = endpoint present; false = 404 (backend not deployed); nil = network failure.
    func calibrationAvailable() async -> Bool? {
        guard mode == .live else { return nil }
        do {
            let data = try await getWithFallback("calibration")
            _ = try Self.decoder.decode(WCCalibrationResponse.self, from: data)
            return true
        } catch let error as WaveCamAPIError where error.statusCode == 404 {
            return false
        } catch {
            return nil
        }
    }

    /// POST /api/v1/calibration/heading
    /// Fields: requested_owner, takeover, heading_deg (0…360), source, note (optional)
    func captureCalibrationHeading(
        headingDeg: Double,
        source: String = "ios_native",
        note: String? = nil
    ) async -> Result<WCCalibrationState, WaveCamCalibrationError> {
        guard mode == .live else { return .failure(.unavailable) }
        var body: [String: Any] = [
            "requested_owner": "manual",
            "takeover": true,
            "heading_deg": headingDeg,
            "source": source
        ]
        if let note { body["note"] = note }
        return await sendCalibrationCapture("calibration/heading", body: body)
    }

    /// POST /api/v1/calibration/tilt
    /// Fields: requested_owner, takeover, tilt_deg (-90…90), source, note (optional)
    func captureCalibrationTilt(
        tiltDeg: Double,
        source: String = "ios_native",
        note: String? = nil
    ) async -> Result<WCCalibrationState, WaveCamCalibrationError> {
        guard mode == .live else { return .failure(.unavailable) }
        var body: [String: Any] = [
            "requested_owner": "manual",
            "takeover": true,
            "tilt_deg": tiltDeg,
            "source": source
        ]
        if let note { body["note"] = note }
        return await sendCalibrationCapture("calibration/tilt", body: body)
    }

    /// POST /api/v1/calibration/zoom
    /// Fields: requested_owner, takeover, zoom_fov_deg (1…180), source, note (optional)
    func captureCalibrationZoom(
        zoomFovDeg: Double,
        source: String = "ios_native",
        note: String? = nil
    ) async -> Result<WCCalibrationState, WaveCamCalibrationError> {
        guard mode == .live else { return .failure(.unavailable) }
        var body: [String: Any] = [
            "requested_owner": "manual",
            "takeover": true,
            "zoom_fov_deg": zoomFovDeg,
            "source": source
        ]
        if let note { body["note"] = note }
        return await sendCalibrationCapture("calibration/zoom", body: body)
    }

    private func sendCalibrationCapture(
        _ path: String,
        body: [String: Any]
    ) async -> Result<WCCalibrationState, WaveCamCalibrationError> {
        do {
            let data = try await post(path, body: body)
            let response = try Self.decoder.decode(WCCalibrationResponse.self, from: data)
            if let status = response.status { self.status = status; connected = true }
            guard let calibration = response.calibration else {
                return .failure(.httpError(200, "Backend returned no calibration state."))
            }
            return .success(calibration)
        } catch let apiError as WaveCamAPIError {
            // Surface structured refusals from the backend (killed / owner_busy).
            if let obj = try? JSONSerialization.jsonObject(with: apiError.data) as? [String: Any] {
                let code = obj["code"] as? String ?? ""
                let message = obj["message"] as? String
                switch code {
                case "killed":  return .failure(.killed)
                case "owner_busy": return .failure(.ownerBusy)
                default: return .failure(.httpError(apiError.statusCode, message ?? code))
                }
            }
            return .failure(.httpError(apiError.statusCode, apiError.localizedDescription))
        } catch {
            return .failure(.networkError(error.localizedDescription))
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

    // MARK: presets

    /// GET /api/v1/presets — returns canned presets in mock mode for offline demos.
    func presets() async -> [WCPreset]? {
        if mode == .mock { return Self.mockPresets }
        do {
            let data = try await getWithFallback("presets")
            let response = try Self.decoder.decode(WCPresetsResponse.self, from: data)
            return response.presets
        } catch {
            return nil
        }
    }

    /// POST /api/v1/presets — saves a new (or updates an existing custom) preset.
    /// Returns false in mock mode (no mutation) or on network/server error.
    func savePreset(name: String, values: [String: JSONValue]) async -> Bool {
        guard mode == .live else { return false }
        do {
            let valuesData = try JSONEncoder().encode(values)
            guard let valuesObj = try JSONSerialization.jsonObject(with: valuesData) as? [String: Any] else {
                return false
            }
            _ = try await post("presets", body: ["name": name, "values": valuesObj])
            return true
        } catch {
            lastControlError = error.localizedDescription
            return false
        }
    }

    /// POST /api/v1/presets/{name}/apply — applies preset values to live config.
    func applyPreset(name: String) async -> WCPresetApplyResult? {
        guard mode == .live else { return nil }
        do {
            // Pass the RAW name — post()'s URL.appending(path:) percent-encodes it once.
            // Pre-encoding here double-encodes (a space → %2520), which 404s any preset
            // whose name contains a space (Tow Foil / Wing Foil / Land Chase).
            let data = try await post("presets/\(name)/apply", body: ["source": "ios_native"])
            return try Self.decoder.decode(WCPresetApplyResult.self, from: data)
        } catch {
            lastControlError = error.localizedDescription
            return nil
        }
    }

    /// DELETE /api/v1/presets/{name} — deletes a custom preset. Rejects builtins server-side.
    func deletePreset(name: String) async -> Bool {
        guard mode == .live else { return false }
        do {
            // Raw name — delete()'s URL.appending(path:) encodes it once (see applyPreset).
            _ = try await delete("presets/\(name)")
            return true
        } catch {
            lastControlError = error.localizedDescription
            return false
        }
    }

    // Canned presets for mock / offline demo — Default + Tow Foil + Cloudy.
    private static let mockPresets: [WCPreset] = [
        WCPreset(
            name: "Default",
            builtin: true,
            restartRequired: false,
            values: [
                "ptz.max_pan_speed": .int(10),
                "ptz.max_tilt_speed": .int(8),
                "ptz.deadzone": .double(0.08),
                "ptz.ff_gain": .double(0.0),
                "ptz.zoom_target_frac": .double(0.5),
                "fusion.require_person": .bool(false),
                "fusion.person_aim_y": .double(0.5),
                "detector.conf": .double(0.35)
            ]
        ),
        WCPreset(
            name: "Tow Foil",
            builtin: true,
            restartRequired: false,
            values: [
                "ptz.max_pan_speed": .int(18),
                "ptz.max_tilt_speed": .int(12),
                "ptz.deadzone": .double(0.10),
                "ptz.ff_gain": .double(0.30),
                "ptz.zoom_target_frac": .double(0.35),
                "fusion.require_person": .bool(false),
                "fusion.person_aim_y": .double(0.45),
                "detector.conf": .double(0.35)
            ]
        ),
        WCPreset(
            name: "Cloudy",
            builtin: true,
            restartRequired: false,
            values: [
                "ptz.max_pan_speed": .int(10),
                "ptz.max_tilt_speed": .int(8),
                "ptz.deadzone": .double(0.08),
                "ptz.ff_gain": .double(0.0),
                "ptz.zoom_target_frac": .double(0.5),
                "fusion.require_person": .bool(false),
                "fusion.person_aim_y": .double(0.5),
                "detector.conf": .double(0.30)
            ]
        ),
    ]

    // MARK: logs (read-only; feature-detected on supported.logs)

    /// GET /api/v1/logs — returns canned lines in mock mode for offline demos;
    /// nil on network/server error in live mode.
    /// `level` is passed as a query param (nil = no filter); `limit` caps the result count.
    func logs(level: String? = nil, limit: Int = 200) async -> [WCLogLine]? {
        if mode == .mock { return Self.mockLogLines }
        var queryItems: [URLQueryItem] = [URLQueryItem(name: "limit", value: "\(limit)")]
        if let level { queryItems.append(URLQueryItem(name: "level", value: level)) }
        do {
            // H2: route through getWithFallback so a tether-down cold start fails over
            // to Wi-Fi instead of returning nil (same class as the "false unreachable" bug).
            let data = try await getWithFallback("logs", queryItems: queryItems)
            return try Self.decoder.decode(WCLogsResponse.self, from: data).lines
        } catch {
            return nil
        }
    }

    /// POST /api/v1/agent/summon — requests an on-demand diagnostic pass from the supervisor.
    /// Returns true when the server accepts the request (2xx). In mock mode always returns true.
    func summonAgent() async -> Bool {
        if mode == .mock { return true }
        do {
            _ = try await post("agent/summon", body: [
                "source": "ios_native",
                "reason": "operator_diagnostics"
            ])
            return true
        } catch {
            lastCommandError = "Summon not accepted: \(error.localizedDescription)"
            return false
        }
    }

    // 12 canned log lines across all levels for mock/offline demos.
    private static let mockLogLines: [WCLogLine] = {
        let now = Int(Date().timeIntervalSince1970 * 1000)
        let lines: [(offset: Int, level: String, source: String, message: String)] = [
            (0,    "INFO",  "supervisor",  "Supervisor started, PID 3812"),
            (800,  "DEBUG", "tracker",     "YOLO engine loaded: yolov8n.engine"),
            (1600, "INFO",  "tracker",     "Vision pipeline started at 30 FPS"),
            (2400, "DEBUG", "ptz",         "VISCA handshake OK — cam 192.168.100.88:1259"),
            (3200, "INFO",  "gps",         "LoRa GPS locked: 4 sats, dist 148m"),
            (4000, "DEBUG", "tracker",     "Color match: orange 0.87, person 0.93"),
            (4800, "WARN",  "ptz",         "Pan speed clamped to max (18) — high tracking error"),
            (5600, "INFO",  "tracker",     "Subject LOCKED — confidence 0.91"),
            (6400, "DEBUG", "media",       "Segment rolled: 20260603-141500.mp4"),
            (7200, "INFO",  "api",         "POST /api/v1/config/hot applied 2 keys"),
            (8000, "WARN",  "cloudflared", "Tunnel reconnect — attempt 1/3"),
            (8800, "ERROR", "cloudflared", "Tunnel failed after 3 attempts — uplink degraded"),
        ]
        return lines.map { line in
            WCLogLine(
                tsUnixMs: now - (12000 - line.offset),
                level: line.level,
                source: line.source,
                message: line.message
            )
        }
    }()

    // MARK: media (read-only; guard mode == .live like config())

    /// GET /api/v1/media/list — returns [] in mock mode or when the endpoint is unavailable.
    /// Throws a `WaveCamMediaListUnavailable` sentinel when the backend responds with 503
    /// (MediaUnavailable) so the caller can surface a distinct "update the Orin" message.
    func mediaList() async throws -> [WCMediaFile] {
        guard mode == .live else { return [] }
        let data = try await getWithFallback("media/list")
        let response = try Self.decoder.decode(WCMediaListResponse.self, from: data)
        return response.files
    }

    /// GET /api/v1/media/download/{name} — streams bytes to a temp file and returns
    /// its local URL. Uses the active `baseURL` (already resolved by the last status
    /// or getWithFallback call) so USB-tether vs. Wi-Fi failover is already settled.
    func downloadMedia(name: String) async throws -> URL {
        guard mode == .live else { throw URLError(.resourceUnavailable) }
        guard let escapedName = name.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) else {
            throw URLError(.badURL)
        }
        let url = baseURL.appending(path: "media/download/\(escapedName)")
        var req = URLRequest(url: url, timeoutInterval: 120)
        authorize(&req)
        let (tempURL, response) = try await URLSession.shared.download(for: req)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw URLError(.badServerResponse)
        }
        // Move from the ephemeral temp location to a durable Documents file.
        let dest = FileManager.default.temporaryDirectory
            .appendingPathComponent("WaveCam-\(name)", conformingTo: .mpeg4Movie)
        try? FileManager.default.removeItem(at: dest)
        try FileManager.default.moveItem(at: tempURL, to: dest)
        return dest
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

    private func getWithFallback(_ path: String, queryItems: [URLQueryItem] = []) async throws -> Data {
        var lastError: Error?
        for candidate in apiCandidates() {
            do {
                var url = candidate.appending(path: path)
                if !queryItems.isEmpty { url.append(queryItems: queryItems) }
                var req = URLRequest(url: url)
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
        // A cleanly-parsed response that omits `ok` is treated as success — only an
        // explicit ok=false is a failure. Stops a 2xx response without an `ok` field
        // from surfacing as a false "command not confirmed" (the status poll is the
        // real source of truth for the resulting state).
        if response.ok == false {
            lastControlError = [response.code, response.message].compactMap(\.self).joined(separator: ": ")
            if lastControlError?.isEmpty != false {
                lastControlError = "Control response reported failure."
            }
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
    private func delete(_ path: String) async throws -> Data {
        var failoverError: Error?
        for candidate in apiCandidates() {
            do {
                var req = URLRequest(url: candidate.appending(path: path))
                req.httpMethod = "DELETE"
                req.timeoutInterval = 5
                authorize(&req)
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
