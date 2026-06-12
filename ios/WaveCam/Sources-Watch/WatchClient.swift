import Foundation
import Observation

// MARK: - DTOs (tolerant; all optional except what's load-bearing for display)

private struct WatchStatus: Decodable {
    struct Session: Decodable {
        var state: String?
    }
    struct Safety: Decodable {
        var killed: Bool?
    }
    struct Tracking: Decodable {
        var locked: Bool?
    }
    struct GPS: Decodable {
        var targetAgeSec: Double?
        var stale: Bool?
        var readerAlive: Bool?
    }
    struct Media: Decodable {
        var recording: Bool?
    }

    var session: Session?
    var safety: Safety?
    var tracking: Tracking?
    var gps: GPS?
    var media: Media?
}

// MARK: - Published state

struct WatchSnapshot {
    var sessionState: String   // "TRACKING", "SEARCHING", "KILLED", …
    var killed: Bool
    var locked: Bool
    var recording: Bool
    /// nil = backend didn't report GPS at all
    var targetAgeSec: Double?
    var gpsStale: Bool?
    var readerAlive: Bool?

    static let offline = WatchSnapshot(
        sessionState: "OFFLINE",
        killed: false,
        locked: false,
        recording: false,
        targetAgeSec: nil,
        gpsStale: nil,
        readerAlive: nil
    )
}

// MARK: - Client

@MainActor
@Observable
final class WatchClient {
    // Primary = USB tether; fallback = LAN Wi-Fi (matches iOS defaults)
    private let tetherBase = URL(string: "http://172.20.10.8:8088/api/v1")!
    private let wifiBase   = URL(string: "http://192.168.1.155:8088/api/v1")!

    private(set) var snapshot = WatchSnapshot.offline
    private(set) var online   = false

    private var pollTask: Task<Void, Never>?
    private var resolvedBase: URL?

    private static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    // MARK: lifecycle

    func startPolling() {
        guard pollTask == nil else { return }
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.poll()
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }

    func stopPolling() {
        pollTask?.cancel()
        pollTask = nil
    }

    // MARK: commands

    func kill() async {
        await post("safety/kill", body: ["reason": "operator", "source": "watch"])
        await poll()
    }

    func resume() async {
        await post("safety/resume", body: ["source": "watch"])
        await poll()
    }

    func toggleRecording() async {
        let path = snapshot.recording ? "media/record/stop" : "media/record/start"
        await post(path, body: ["source": "watch"])
        await poll()
    }

    // MARK: private

    private func poll() async {
        do {
            let data = try await get("status")
            let s = try Self.decoder.decode(WatchStatus.self, from: data)
            online = true
            snapshot = WatchSnapshot(
                sessionState: s.session?.state ?? "UNKNOWN",
                killed: s.safety?.killed ?? false,
                locked: s.tracking?.locked ?? false,
                recording: s.media?.recording ?? false,
                targetAgeSec: s.gps?.targetAgeSec,
                gpsStale: s.gps?.stale,
                readerAlive: s.gps?.readerAlive
            )
        } catch {
            online = false
            snapshot = .offline
        }
    }

    /// Try tether first, fall back to Wi-Fi. Idempotent failover for GET (reads are safe to retry).
    private func get(_ path: String) async throws -> Data {
        let candidates = resolvedBase.map { [$0] } ?? [tetherBase, wifiBase]
        var lastErr: Error = URLError(.cannotConnectToHost)
        for base in candidates {
            do {
                var req = URLRequest(url: base.appending(path: path))
                req.timeoutInterval = 3
                let (data, resp) = try await URLSession.shared.data(for: req)
                if let http = resp as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                    throw URLError(.badServerResponse)
                }
                resolvedBase = base   // cache winning route
                return data
            } catch {
                // Only allow failover when the server was unreachable
                if let ue = error as? URLError,
                   // .timedOut is how an ABSENT subnet fails (tether IP on home
                   // Wi-Fi blackholes; nothing sends a refusal) — excluding it
                   // meant the Wi-Fi fallback was never reached and the watch
                   // showed OFFLINE forever (field report 2026-06-12).
                   [.cannotConnectToHost, .cannotFindHost, .dnsLookupFailed,
                    .timedOut, .networkConnectionLost].contains(ue.code) {
                    lastErr = error
                    resolvedBase = nil  // next poll re-probes both
                } else {
                    throw error
                }
            }
        }
        throw lastErr
    }

    /// Fire-and-forget POST. Uses the already-resolved base if known; otherwise tries tether first.
    @discardableResult
    private func post(_ path: String, body: [String: Any]) async -> Bool {
        guard let payload = try? JSONSerialization.data(withJSONObject: body) else { return false }
        let candidates = resolvedBase.map { [$0] } ?? [tetherBase, wifiBase]
        for base in candidates {
            do {
                var req = URLRequest(url: base.appending(path: path))
                req.httpMethod = "POST"
                req.timeoutInterval = 5
                req.setValue("application/json", forHTTPHeaderField: "Content-Type")
                req.httpBody = payload
                let (_, resp) = try await URLSession.shared.data(for: req)
                if let http = resp as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                    return false
                }
                resolvedBase = base
                return true
            } catch let ue as URLError
                where [.cannotConnectToHost, .cannotFindHost, .dnsLookupFailed].contains(ue.code) {
                resolvedBase = nil
                continue
            } catch {
                return false
            }
        }
        return false
    }
}
