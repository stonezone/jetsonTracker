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
    /// True when the TargetEstimator is running in shadow mode (never commands).
    /// Absent on older backends — UI hides shadow indicator when nil.
    var shadowMode: Bool?

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
        // Reader-health fields (backend ≥ P1 review build); nil on older backends.
        var readerAlive: Bool?
        var lastPollAgeSec: Double?
        // Direct-LoRa tracker telemetry (backend exposes these when source=direct_lora); nil otherwise.
        var targetBatteryMv: Int?
        var targetSats: Int?
    }
    struct Media: Codable, Sendable {
        var recording: Bool
        var segmentName: String?
        var freeGb: Double?
    }
    struct Network: Codable, Sendable {
        var cameraLan: Bool?
        var uplink: Bool?
    }
}

// H4: tolerant decoding — a renamed/missing backend field must degrade one HUD
// value, not throw the whole /status decode and blank the app to OFFLINE.
// (Extensions preserve the synthesized memberwise inits.)
extension WCStatus {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        revision = try c.decodeIfPresent(Int.self, forKey: .revision) ?? 0
        timeUnixMs = try c.decodeIfPresent(Int.self, forKey: .timeUnixMs)
        session = try c.decodeIfPresent(Session.self, forKey: .session)
            ?? Session(state: "UNKNOWN", mode: nil, startedAtUnixMs: nil)
        safety = try c.decodeIfPresent(Safety.self, forKey: .safety)
            ?? Safety(killed: false, killReason: nil, lastKillAtUnixMs: nil)
        ptz = try c.decodeIfPresent(PTZ.self, forKey: .ptz)
            ?? PTZ(owner: "idle", enabled: nil, panTiltCmd: nil, zoomState: nil)
        tracking = try c.decodeIfPresent(Tracking.self, forKey: .tracking)
            ?? Tracking(locked: false, state: "UNKNOWN", confidence: 0, fps: 0,
                        hasColor: nil, hasPerson: nil, matched: nil)
        gps = try c.decodeIfPresent(GPS.self, forKey: .gps)
        media = try c.decodeIfPresent(Media.self, forKey: .media)
        services = try c.decodeIfPresent([String: String].self, forKey: .services)
        network = try c.decodeIfPresent(Network.self, forKey: .network)
        shadowMode = try c.decodeIfPresent(Bool.self, forKey: .shadowMode)
    }
}

extension WCStatus.Session {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        state = try c.decodeIfPresent(String.self, forKey: .state) ?? "UNKNOWN"
        mode = try c.decodeIfPresent(String.self, forKey: .mode)
        startedAtUnixMs = try c.decodeIfPresent(Int.self, forKey: .startedAtUnixMs)
    }
}

extension WCStatus.Safety {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // false on absence matches the backend's at-rest default; the optimistic
        // KILL latch covers operator-initiated stops while a field is missing.
        killed = try c.decodeIfPresent(Bool.self, forKey: .killed) ?? false
        killReason = try c.decodeIfPresent(String.self, forKey: .killReason)
        lastKillAtUnixMs = try c.decodeIfPresent(Int.self, forKey: .lastKillAtUnixMs)
    }
}

extension WCStatus.PTZ {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        owner = try c.decodeIfPresent(String.self, forKey: .owner) ?? "idle"
        enabled = try c.decodeIfPresent(Bool.self, forKey: .enabled)
        panTiltCmd = try c.decodeIfPresent(String.self, forKey: .panTiltCmd)
        zoomState = try c.decodeIfPresent(String.self, forKey: .zoomState)
    }
}

extension WCStatus.Tracking {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        locked = try c.decodeIfPresent(Bool.self, forKey: .locked) ?? false
        state = try c.decodeIfPresent(String.self, forKey: .state) ?? "UNKNOWN"
        confidence = try c.decodeIfPresent(Double.self, forKey: .confidence) ?? 0
        fps = try c.decodeIfPresent(Double.self, forKey: .fps) ?? 0
        hasColor = try c.decodeIfPresent(Bool.self, forKey: .hasColor)
        hasPerson = try c.decodeIfPresent(Bool.self, forKey: .hasPerson)
        matched = try c.decodeIfPresent(Bool.self, forKey: .matched)
    }
}

extension WCStatus.Media {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        recording = try c.decodeIfPresent(Bool.self, forKey: .recording) ?? false
        segmentName = try c.decodeIfPresent(String.self, forKey: .segmentName)
        freeGb = try c.decodeIfPresent(Double.self, forKey: .freeGb)
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
            services: ["wavecam": "running", "supervisor": "running"],
            network: .init(cameraLan: true, uplink: true)
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
        var mediaDelete: Bool?
        var trackingMode: Bool?
    }

    struct Current: Codable, Sendable {
        var ptz: PTZ
        var fusion: Fusion
        var color: ColorCfg
        var detector: Detector
        var web: Web
        var gps: GPSCfg?   // absent on backends before the P2 deploy — feature-detected
        var tracking: Tracking?   // absent before the GPS-only-mode backend — feature-detected

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
            var gpsBoost: Double?
            var gpsBoostRadiusFrac: Double?
        }
        struct GPSCfg: Codable, Sendable {
            var staleThresholdSec: Double?
            var graceSec: Double?
            var driveZoom: Bool?
        }
        struct Tracking: Codable, Sendable {
            var mode: String?   // "auto" | "gps_only" | "vision_only"
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
    case gpsUnavailable   // base Wio has no fix yet — base-lock refused
    case operatorAcceptRequired   // heading-lock preview step: must explicitly accept
    case uncertaintyTooHigh(Double)   // heading uncertainty exceeds budget
    case levelRequired   // level check must pass before heading
    case panAxisNotLevel(Double)   // tripod not level — tilt magnitude in degrees
    case httpError(Int, String?)
    case networkError(String)

    var errorDescription: String? {
        switch self {
        case .killed:
            return "KILL is latched — resume before capturing calibration."
        case .ownerBusy:
            return "Another PTZ owner holds the camera. Try again or take over."
        case .gpsUnavailable:
            return "Base GPS has no fix yet — give the base tracker open sky and wait for the Base fix line on the GPS chip."
        case .unavailable:
            return "On-device calibration requires the latest Orin build — checklist only for now."
        case .operatorAcceptRequired:
            return "Preview requires explicit tap-to-accept before the heading is locked."
        case let .uncertaintyTooHigh(deg):
            return String(format: "Heading uncertainty %.1f° exceeds the 2° budget — move closer or use a known landmark.", deg)
        case .levelRequired:
            return "A passing level check is required before heading capture."
        case let .panAxisNotLevel(deg):
            return String(format: "Tripod not level — tilt is %.1f° (max 0.5°). Level the tripod then re-check.", deg)
        case let .httpError(code, msg):
            return msg.map { "\($0) (HTTP \(code))" } ?? "HTTP \(code)"
        case let .networkError(desc):
            return "Network error: \(desc)"
        }
    }
}

// MARK: - Calibrate session models (PR #88 / codex/calibrate-backend)

/// Location entry from POST /api/v1/calibration/location response.
struct WCCalLocationEntry: Codable, Sendable {
    var lat: Double?
    var lon: Double?
    var altM: Double?
    var errorRadiusM: Double?
    var sampleCount: Int?
    var rejectedCount: Int?
    var model: String?
    var capturedAtUnixMs: Int?
}

extension WCCalLocationEntry {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        lat = try c.decodeIfPresent(Double.self, forKey: .lat)
        lon = try c.decodeIfPresent(Double.self, forKey: .lon)
        altM = try c.decodeIfPresent(Double.self, forKey: .altM)
        errorRadiusM = try c.decodeIfPresent(Double.self, forKey: .errorRadiusM)
        sampleCount = try c.decodeIfPresent(Int.self, forKey: .sampleCount)
        rejectedCount = try c.decodeIfPresent(Int.self, forKey: .rejectedCount)
        model = try c.decodeIfPresent(String.self, forKey: .model)
        capturedAtUnixMs = try c.decodeIfPresent(Int.self, forKey: .capturedAtUnixMs)
    }
}

/// Level result from POST /api/v1/calibration/level response.
struct WCCalLevelEntry: Codable, Sendable {
    var rollDeg: Double?
    var pitchDeg: Double?
    var tiltMagDeg: Double?
    var maxTiltDeg: Double?
    var passed: Bool?
}

extension WCCalLevelEntry {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        rollDeg = try c.decodeIfPresent(Double.self, forKey: .rollDeg)
        pitchDeg = try c.decodeIfPresent(Double.self, forKey: .pitchDeg)
        tiltMagDeg = try c.decodeIfPresent(Double.self, forKey: .tiltMagDeg)
        maxTiltDeg = try c.decodeIfPresent(Double.self, forKey: .maxTiltDeg)
        passed = try c.decodeIfPresent(Bool.self, forKey: .passed)
    }
}

/// Heading lock entry from POST /api/v1/calibration/heading-lock response.
struct WCCalHeadingLockEntry: Codable, Sendable {
    var bearingDeg: Double?
    var panEnc: Double?
    var panEncPerDeg: Double?
    var distanceM: Double?
    var uncertaintyDeg: Double?
    var confidence: Double?
    var method: String?
}

extension WCCalHeadingLockEntry {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        bearingDeg = try c.decodeIfPresent(Double.self, forKey: .bearingDeg)
        panEnc = try c.decodeIfPresent(Double.self, forKey: .panEnc)
        panEncPerDeg = try c.decodeIfPresent(Double.self, forKey: .panEncPerDeg)
        distanceM = try c.decodeIfPresent(Double.self, forKey: .distanceM)
        uncertaintyDeg = try c.decodeIfPresent(Double.self, forKey: .uncertaintyDeg)
        confidence = try c.decodeIfPresent(Double.self, forKey: .confidence)
        method = try c.decodeIfPresent(String.self, forKey: .method)
    }
}

/// Validation result from POST /api/v1/calibration/validation response.
struct WCCalValidationEntry: Codable, Sendable {
    var bearingDeg: Double?
    var predictedBearingDeg: Double?
    var missDeg: Double?
    var maxMissDeg: Double?
    var distanceM: Double?
    var accepted: Bool?
}

extension WCCalValidationEntry {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        bearingDeg = try c.decodeIfPresent(Double.self, forKey: .bearingDeg)
        predictedBearingDeg = try c.decodeIfPresent(Double.self, forKey: .predictedBearingDeg)
        missDeg = try c.decodeIfPresent(Double.self, forKey: .missDeg)
        maxMissDeg = try c.decodeIfPresent(Double.self, forKey: .maxMissDeg)
        distanceM = try c.decodeIfPresent(Double.self, forKey: .distanceM)
        accepted = try c.decodeIfPresent(Bool.self, forKey: .accepted)
    }
}

/// Session sub-object nested inside the `calibration` key for wizard-step endpoints.
struct WCCalibrationSession: Codable, Sendable {
    var location: WCCalLocationEntry?
    var level: WCCalLevelEntry?
    var headingLock: WCCalHeadingLockEntry?
    var validation: WCCalValidationEntry?
}

extension WCCalibrationSession {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        location = try c.decodeIfPresent(WCCalLocationEntry.self, forKey: .location)
        level = try c.decodeIfPresent(WCCalLevelEntry.self, forKey: .level)
        headingLock = try c.decodeIfPresent(WCCalHeadingLockEntry.self, forKey: .headingLock)
        validation = try c.decodeIfPresent(WCCalValidationEntry.self, forKey: .validation)
    }
}

/// Top-level calibration state returned by all session-wizard endpoints (PR #88).
/// The existing `WCCalibrationState` is the legacy per-axis state; this carries the
/// PR-88 session fields. Both can coexist in the response — the session envelope is
/// parsed from `calibration` and session sub-fields from `calibration.session`.
struct WCCalibrationSessionState: Codable, Sendable {
    var active: Bool
    var valid: Bool
    var confirmed: Bool
    var banner: String
    var ageSec: Double?
    var ownerActive: Bool?
    var session: WCCalibrationSession?
    // Legacy axis fields forwarded for backwards compat.
    var referenceHeading: Double?
}

extension WCCalibrationSessionState {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        active = try c.decodeIfPresent(Bool.self, forKey: .active) ?? false
        valid = try c.decodeIfPresent(Bool.self, forKey: .valid) ?? false
        confirmed = try c.decodeIfPresent(Bool.self, forKey: .confirmed) ?? false
        banner = try c.decodeIfPresent(String.self, forKey: .banner) ?? "INVALID"
        ageSec = try c.decodeIfPresent(Double.self, forKey: .ageSec)
        ownerActive = try c.decodeIfPresent(Bool.self, forKey: .ownerActive)
        session = try c.decodeIfPresent(WCCalibrationSession.self, forKey: .session)
        referenceHeading = try c.decodeIfPresent(Double.self, forKey: .referenceHeading)
    }
}

/// Envelope for all PR-88 session-wizard responses. `calibration` carries the
/// session-state; `status` is the full status snapshot; `code`/`message` carry
/// structured refusal reasons on 4xx responses.
private struct WCCalibrationSessionResponse: Codable, Sendable {
    var ok: Bool?
    var code: String?
    var message: String?
    var uncertaintyDeg: Double?
    var missDeg: Double?
    var calibration: WCCalibrationSessionState?
    var status: WCStatus?
}

extension WCCalibrationSessionResponse {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = try c.decodeIfPresent(Bool.self, forKey: .ok)
        code = try c.decodeIfPresent(String.self, forKey: .code)
        message = try c.decodeIfPresent(String.self, forKey: .message)
        uncertaintyDeg = try c.decodeIfPresent(Double.self, forKey: .uncertaintyDeg)
        missDeg = try c.decodeIfPresent(Double.self, forKey: .missDeg)
        calibration = try c.decodeIfPresent(WCCalibrationSessionState.self, forKey: .calibration)
        status = try c.decodeIfPresent(WCStatus.self, forKey: .status)
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

// H4: only `name` is essential — a record missing size/ctime (older backend)
// must not throw away the whole media list.
extension WCMediaFile {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decode(String.self, forKey: .name)
        sizeBytes = try c.decodeIfPresent(Int.self, forKey: .sizeBytes) ?? 0
        ctimeUnixMs = try c.decodeIfPresent(Int.self, forKey: .ctimeUnixMs) ?? 0
    }
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

// MARK: - Health models

/// Component health entry from GET /api/v1/health.
/// `detail` carries mixed scalars (fps, free_gb, etc.) decoded via JSONValue.
struct WCComponent: Codable, Sendable {
    var ok: Bool?
    var ageSec: Double?
    var detail: [String: JSONValue]?
}

/// Top-level response from GET /api/v1/health.
/// nil response → feature not yet deployed → UI hides the card entirely.
struct WCHealth: Codable, Sendable {
    var ok: Bool?
    var components: [String: WCComponent]?
}

// MARK: - Event models

/// Estimator shadow-tick detail, present when kind == "shadow".
/// All fields are optional — absent on older backends or non-shadow events.
struct ShadowDetail: Codable, Sendable {
    var bearingDeg: Double?
    var distM: Double?
    var panEncWould: Int?
    var bearingStdDeg: Double?
    var ownerActual: String?
    var gpsUpdated: Bool?
    var visionUpdated: Bool?

    enum CodingKeys: String, CodingKey {
        case bearingDeg = "bearing_deg"
        case distM = "dist_m"
        case panEncWould = "pan_enc_would"
        case bearingStdDeg = "bearing_std_deg"
        case ownerActual = "owner_actual"
        case gpsUpdated = "gps_updated"
        case visionUpdated = "vision_updated"
    }
}

/// One event entry from GET /api/v1/events.
struct WCEvent: Codable, Sendable, Identifiable {
    var t: Double?
    var kind: String?
    /// String detail for non-shadow events (lock, kill, owner, gps, etc.).
    var detail: String?
    /// Structured detail for kind == "shadow" events; nil for all other kinds.
    var shadowDetail: ShadowDetail?

    /// Stable identity: timestamp float → string; collisions within the same
    /// second are disambiguated by kind.
    var id: String {
        let ts = t.map { String($0) } ?? "0"
        return "\(ts)-\(kind ?? "")"
    }

    var timestamp: Date {
        Date(timeIntervalSince1970: t ?? 0)
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        t = try c.decodeIfPresent(Double.self, forKey: .t)
        kind = try c.decodeIfPresent(String.self, forKey: .kind)
        // `detail` is a string for most events but a dict for kind=shadow.
        // Try string first; fall back to nil so the view can use shadowDetail.
        detail = try? c.decodeIfPresent(String.self, forKey: .detail)
        // Attempt shadow dict decode regardless of kind — harmless no-op for string detail.
        shadowDetail = try? c.decodeIfPresent(ShadowDetail.self, forKey: .detail)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encodeIfPresent(t, forKey: .t)
        try c.encodeIfPresent(kind, forKey: .kind)
        try c.encodeIfPresent(detail, forKey: .detail)
    }

    enum CodingKeys: String, CodingKey {
        case t, kind, detail
    }
}

private struct WCEventsResponse: Codable, Sendable {
    var events: [WCEvent]
}

// H4: a missing `events` key decodes to empty rather than blanking the event ring.
extension WCEventsResponse {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        events = try c.decodeIfPresent([WCEvent].self, forKey: .events) ?? []
    }
}

// MARK: - Log models

/// One log line returned by GET /api/v1/logs.
/// Field names decoded via .convertFromSnakeCase.
struct WCAgentReport: Decodable {
    let status: String?          // idle | running | done | error
    let provider: String?
    let text: String?
    let error: String?
    let durationSec: Double?

    init(status: String?, provider: String?, text: String?, error: String?, durationSec: Double?) {
        self.status = status; self.provider = provider; self.text = text
        self.error = error; self.durationSec = durationSec
    }
}

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

// H4: one malformed line must not throw away the whole log response.
extension WCLogLine {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tsUnixMs = try c.decodeIfPresent(Int.self, forKey: .tsUnixMs) ?? 0
        level = try c.decodeIfPresent(String.self, forKey: .level) ?? ""
        source = try c.decodeIfPresent(String.self, forKey: .source) ?? ""
        message = try c.decodeIfPresent(String.self, forKey: .message) ?? ""
    }
}

private struct WCLogsResponse: Codable, Sendable {
    var lines: [WCLogLine]
}

// H4: a missing `lines` key decodes to empty rather than throwing.
extension WCLogsResponse {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        lines = try c.decodeIfPresent([WCLogLine].self, forKey: .lines) ?? []
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

    /// True while a KILL request is in flight and the backend has not yet confirmed it.
    /// Gates `refresh()` so a poll returning killed==false cannot prematurely clear the
    /// latch — the UI must never falsely report "not killed" while the camera may still move.
    private var killInFlight = false

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
            if status?.safety.killed == true {
                // Backend confirmed the kill — safe to drop the optimistic latch.
                optimisticKilled = false
                killInFlight = false
            } else if !killInFlight {
                // No pending kill request; fresh status is authoritative.
                optimisticKilled = false
            }
            // When killInFlight==true and killed==false the latch stays set:
            // the kill POST is still in flight and the UI must not falsely clear.
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

    /// Scene-phase hook: the 1Hz poll has no business running while the app is
    /// backgrounded on a beach battery. Live mode only — mock never polls.
    func setPollingActive(_ active: Bool) {
        guard mode == .live else { return }
        if active { startPolling() } else { stopPolling() }
    }

    // MARK: safety (highest priority, always allowed)

    func kill(reason: String = "operator") async {
        optimisticKilled = true
        killInFlight = true
        if mode == .mock { mockKilled = true; killInFlight = false; await refresh(); return }
        do {
            _ = try await post("safety/kill", body: ["reason": reason, "source": "ios_native"])
            lastCommandError = nil
        } catch {
            lastCommandError = "Safety stop not confirmed by Orin: \(error.localizedDescription)"
            // Request never reached the server — do not leave a false latch.
            optimisticKilled = false
            killInFlight = false
        }
        await refresh()
        if killed {
            // Backend confirmed; refresh() already cleared the latch, but be explicit.
            optimisticKilled = false
            killInFlight = false
        }
    }

    func resume() async {
        optimisticKilled = false
        killInFlight = false
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

    /// POST /api/v1/calibration/base-lock — latch the averaged base GPS position as the
    /// camera reference. Backend refuses with `gps_unavailable` until the base has a fix.
    /// Fields: requested_owner, takeover, source, note (optional)
    func captureCalibrationBaseLock(
        source: String = "ios_native",
        note: String? = nil
    ) async -> Result<WCCalibrationState, WaveCamCalibrationError> {
        guard mode == .live else { return .failure(.unavailable) }
        var body: [String: Any] = [
            "requested_owner": "manual",
            "takeover": true,
            "source": source
        ]
        if let note { body["note"] = note }
        return await sendCalibrationCapture("calibration/base-lock", body: body)
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
                case "gps_unavailable": return .failure(.gpsUnavailable)
                default: return .failure(.httpError(apiError.statusCode, message ?? code))
                }
            }
            return .failure(.httpError(apiError.statusCode, apiError.localizedDescription))
        } catch {
            return .failure(.networkError(error.localizedDescription))
        }
    }

    // MARK: calibrate wizard session (PR #88 endpoints)

    /// POST /api/v1/calibration/session/start
    /// Sets PTZ owner to "calibrate" (tracker locked out); returns banner "CALIBRATE ACTIVE".
    func calibrateSessionStart(source: String = "ios_native") async -> Result<WCCalibrationSessionState, WaveCamCalibrationError> {
        guard mode == .live else { return .failure(.unavailable) }
        return await sendCalibrationSession("calibration/session/start", body: [
            "requested_owner": "manual",
            "takeover": true,
            "source": source
        ])
    }

    /// POST /api/v1/calibration/session/exit
    /// Releases the "calibrate" PTZ owner and restores prior mode.
    func calibrateSessionExit(source: String = "ios_native") async -> Result<WCCalibrationSessionState, WaveCamCalibrationError> {
        guard mode == .live else { return .failure(.unavailable) }
        return await sendCalibrationSession("calibration/session/exit", body: [
            "confirm": true,
            "restore_prior": true,
            "source": source
        ])
    }

    /// POST /api/v1/calibration/location
    /// Locks the base GPS position. `useLive` = use the backend's live base-Wio fix directly
    /// (no samples from iOS); the backend averages and applies HDOP×UERE radius model.
    func calibrateLocation(useLive: Bool = true, source: String = "ios_native") async -> Result<WCCalibrationSessionState, WaveCamCalibrationError> {
        guard mode == .live else { return .failure(.unavailable) }
        return await sendCalibrationSession("calibration/location", body: [
            "method": "base_wio_average",
            "use_live_base": useLive,
            "source": source
        ])
    }

    /// POST /api/v1/calibration/level
    /// Checks pan-axis level. `rollDeg` / `pitchDeg` come from the iPhone's motion sensor
    /// when the phone is rigidly mounted; backend refuses above 0.5° tilt magnitude.
    func calibrateLevel(rollDeg: Double, pitchDeg: Double, source: String = "ios_native") async -> Result<WCCalibrationSessionState, WaveCamCalibrationError> {
        guard mode == .live else { return .failure(.unavailable) }
        return await sendCalibrationSession("calibration/level", body: [
            "roll_deg": rollDeg,
            "pitch_deg": pitchDeg,
            "source": source
        ])
    }

    /// POST /api/v1/calibration/heading-lock (preview probe — operator_accepted: false).
    /// Backend returns 409 `operator_accept_required`; caller shows the preview and then
    /// calls calibrateHeadingLockAccept once the operator taps to confirm.
    func calibrateHeadingLockPreview(
        bearingDeg: Double,
        distanceM: Double?,
        source: String = "ios_native"
    ) async -> Result<WCCalibrationSessionState, WaveCamCalibrationError> {
        guard mode == .live else { return .failure(.unavailable) }
        var body: [String: Any] = [
            "method": "landmark",
            "operator_accepted": false,
            "bearing_deg": bearingDeg,
            "source": source
        ]
        if let d = distanceM { body["distance_m"] = d }
        return await sendCalibrationSession("calibration/heading-lock", body: body)
    }

    /// POST /api/v1/calibration/heading-lock (operator_accepted: true).
    /// Locks the heading. Must be called only after the operator has reviewed the preview.
    func calibrateHeadingLockAccept(
        bearingDeg: Double,
        distanceM: Double?,
        source: String = "ios_native"
    ) async -> Result<WCCalibrationSessionState, WaveCamCalibrationError> {
        guard mode == .live else { return .failure(.unavailable) }
        var body: [String: Any] = [
            "method": "landmark",
            "operator_accepted": true,
            "bearing_deg": bearingDeg,
            "source": source
        ]
        if let d = distanceM { body["distance_m"] = d }
        return await sendCalibrationSession("calibration/heading-lock", body: body)
    }

    /// POST /api/v1/calibration/validation
    /// Sight an independent check-point; backend returns predicted vs actual miss.
    func calibrateValidation(
        bearingDeg: Double,
        distanceM: Double?,
        source: String = "ios_native"
    ) async -> Result<WCCalibrationSessionState, WaveCamCalibrationError> {
        guard mode == .live else { return .failure(.unavailable) }
        var body: [String: Any] = ["bearing_deg": bearingDeg, "source": source]
        if let d = distanceM { body["distance_m"] = d }
        return await sendCalibrationSession("calibration/validation", body: body)
    }

    /// POST /api/v1/calibration/validation/confirm
    /// Operator confirms the validation; marks calibration valid and exits CALIBRATE mode.
    func calibrateValidationConfirm(accepted: Bool = true, source: String = "ios_native") async -> Result<WCCalibrationSessionState, WaveCamCalibrationError> {
        guard mode == .live else { return .failure(.unavailable) }
        return await sendCalibrationSession("calibration/validation/confirm", body: [
            "accepted": accepted,
            "source": source
        ])
    }

    private func sendCalibrationSession(
        _ path: String,
        body: [String: Any]
    ) async -> Result<WCCalibrationSessionState, WaveCamCalibrationError> {
        do {
            let data = try await post(path, body: body)
            let response = try Self.decoder.decode(WCCalibrationSessionResponse.self, from: data)
            if let s = response.status { self.status = s; connected = true }
            guard let cal = response.calibration else {
                return .failure(.httpError(200, "Backend returned no calibration session state."))
            }
            return .success(cal)
        } catch let apiError as WaveCamAPIError {
            if let obj = try? JSONSerialization.jsonObject(with: apiError.data) as? [String: Any] {
                let code = obj["code"] as? String ?? ""
                let message = obj["message"] as? String
                let uncertaintyDeg = obj["uncertainty_deg"] as? Double ?? 0
                switch code {
                case "killed": return .failure(.killed)
                case "owner_busy": return .failure(.ownerBusy)
                case "operator_accept_required": return .failure(.operatorAcceptRequired)
                case "uncertainty_too_high": return .failure(.uncertaintyTooHigh(uncertaintyDeg))
                case "level_required": return .failure(.levelRequired)
                case "pan_axis_not_level":
                    let tiltMag = obj["uncertainty_deg"] as? Double ?? 0
                    return .failure(.panAxisNotLevel(tiltMag))
                default: return .failure(.httpError(apiError.statusCode, message ?? code))
                }
            }
            return .failure(.httpError(apiError.statusCode, apiError.localizedDescription))
        } catch {
            return .failure(.networkError(error.localizedDescription))
        }
    }

    /// True = calibration session wizard is available (PR #88 backend); false = 404; nil = network error.
    func calibrateSessionAvailable() async -> Bool? {
        guard mode == .live else { return nil }
        do {
            let data = try await getWithFallback("calibration")
            let response = try Self.decoder.decode(WCCalibrationSessionResponse.self, from: data)
            // PR #88 backend adds `active`/`banner` fields; older backends omit them.
            return response.calibration?.banner != nil
        } catch let error as WaveCamAPIError where error.statusCode == 404 {
            return false
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

    // MARK: health + events (observability; feature-detected — nil = not deployed)

    /// GET /api/v1/health — returns nil when the endpoint is absent or on any network error.
    /// Callers hide their UI if this returns nil.
    func health() async -> WCHealth? {
        guard mode == .live else { return nil }
        do {
            let data = try await getWithFallback("health")
            return try Self.decoder.decode(WCHealth.self, from: data)
        } catch {
            return nil
        }
    }

    /// GET /api/v1/events?since=<unix_ts> — returns nil on network/server error.
    /// `since` is a Unix timestamp; pass 0 to fetch the full ring.
    func events(since: Double) async -> [WCEvent]? {
        guard mode == .live else { return nil }
        let queryItems = [URLQueryItem(name: "since", value: String(since))]
        do {
            let data = try await getWithFallback("events", queryItems: queryItems)
            return try Self.decoder.decode(WCEventsResponse.self, from: data).events
        } catch {
            return nil
        }
    }

    /// POST /api/v1/sensors/phone — phone-on-tripod telemetry (Phase-3 T3.1).
    /// Fire-and-forget: swallows all errors — sensor data is best-effort.
    /// Only posts in live mode; no-ops in mock/offline so the publisher never needs to know.
    func postPhoneSensor(_ body: [String: Any]) async {
        guard mode == .live else { return }
        _ = try? await post("sensors/phone", body: body)
    }

    /// POST /api/v1/agent/summon — requests an on-demand diagnostic pass from the supervisor.
    /// Returns true when the server accepts the request (2xx). In mock mode always returns true.
    func summonAgent(provider: String = "claude") async -> Bool {
        if mode == .mock { return true }
        do {
            _ = try await post("agent/summon", body: [
                "source": "ios_native",
                "reason": "operator_diagnostics",
                "provider": provider
            ])
            return true
        } catch {
            lastCommandError = "Summon not accepted: \(error.localizedDescription)"
            return false
        }
    }

    func agentReport() async -> WCAgentReport? {
        if mode == .mock {
            return WCAgentReport(status: "done", provider: "claude",
                                 text: "HEALTHY — mock report.",
                                 error: nil, durationSec: 3.2)
        }
        struct Envelope: Decodable { let report: WCAgentReport? }
        guard let data = try? await getWithFallback("agent/report") else { return nil }
        return (try? Self.decoder.decode(Envelope.self, from: data))?.report
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

    /// GET /api/v1/media/list — returns [] in mock mode.
    /// Propagates any HTTP or network error (including 503 MediaUnavailable) as a thrown error;
    /// the caller is responsible for surfacing the failure to the operator.
    func mediaList() async throws -> [WCMediaFile] {
        guard mode == .live else { return [] }
        let data = try await getWithFallback("media/list")
        let response = try Self.decoder.decode(WCMediaListResponse.self, from: data)
        return response.files
    }

    /// GET /api/v1/media/download/{name} — streams bytes to a temp file and returns
    /// its local URL. Probes /status through getWithFallback first so the tether→Wi-Fi
    /// route is settled before the streaming download (which can't fail over itself).
    func downloadMedia(name: String) async throws -> URL {
        guard mode == .live else { throw URLError(.resourceUnavailable) }
        // Settle the active route (tether→Wi-Fi); URLSession.download uses a single
        // resolved baseURL and has no failover of its own.
        _ = try await getWithFallback("status")
        // Raw name — baseURL.appending(path:) encodes it once (pre-encoding double-encodes).
        let url = baseURL.appending(path: "media/download/\(name)")
        var req = URLRequest(url: url, timeoutInterval: 120)
        authorize(&req)
        let (tempURL, response) = try await URLSession.shared.download(for: req)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw URLError(.badServerResponse)
        }
        // Move from the ephemeral download temp location to the app Documents directory
        // (UIFileSharingEnabled) so the file persists through iOS temp-dir purges.
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let dest = docs.appendingPathComponent("WaveCam-\(name)", conformingTo: .mpeg4Movie)
        try? FileManager.default.removeItem(at: dest)
        try FileManager.default.moveItem(at: tempURL, to: dest)
        return dest
    }

    /// DELETE /api/v1/media/{name} — deletes one recording. Feature-detected via
    /// supported.mediaDelete (the UI hides delete until the backend exposes it).
    func deleteMedia(name: String) async -> Bool {
        guard mode == .live else { return false }
        do {
            _ = try await delete("media/\(name)")   // raw name — delete() encodes once
            return true
        } catch {
            lastControlError = error.localizedDescription
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
                // Within the recheck window: stay on the known-good route and do NOT
                // probe the (usually-absent) tether subnet — otherwise every status
                // poll AND control POST blackholes on the tether read timeout. Tether
                // is retried once per tetherRecheckInterval when the window elapses.
                return [baseURL, wifiBaseURL]
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
            if (lastControlError ?? "").isEmpty {
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
