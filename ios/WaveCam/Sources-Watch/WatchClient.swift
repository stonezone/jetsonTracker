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
    // Primary = USB tether; fallback = LAN Wi-Fi.
    // Live values come from WatchConnectionStore (synced from the paired iPhone);
    // these computed vars fall back to the store defaults if no sync has occurred yet.
    private var tetherBase: URL { WatchConnectionStore.shared.tetherURL }
    private var wifiBase:   URL { WatchConnectionStore.shared.wifiURL }

    private(set) var snapshot = WatchSnapshot.offline
    private(set) var online   = false
    /// H13: true when the last STOP could not be confirmed by the rig (POST failed on
    /// every route). Cleared on a confirmed kill (poll sees killed) or a successful resume.
    private(set) var stopNotConfirmed = false

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
        // H13: kill is idempotent — allow timeout failover, and SURFACE a failed STOP
        // instead of discarding the Bool (the operator must know the rig may still move).
        let ok = await post("safety/kill", body: ["reason": "operator", "source": "watch"],
                            allowTimeoutFailover: true)
        stopNotConfirmed = !ok
        await poll()
    }

    func resume() async {
        let ok = await post("safety/resume", body: ["source": "watch"],
                            allowTimeoutFailover: true)
        if ok { stopNotConfirmed = false }
        await poll()
    }

    /// R19: let the operator clear a stale "STOP NOT CONFIRMED" banner once the situation
    /// is known-resolved elsewhere (killed+resumed from the phone, or handled physically).
    /// Fail-safe direction only — this never clears an ACTUAL kill latch (`snapshot.killed`
    /// still gates the resume button independently), it only dismisses the unconfirmed-POST
    /// warning banner itself.
    func dismissStopNotConfirmed() {
        stopNotConfirmed = false
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
            // H13: the rig confirms it is killed — the unconfirmed-STOP warning is stale.
            if snapshot.killed { stopNotConfirmed = false }
        } catch {
            online = false
            snapshot = .offline
        }
    }

    /// Try tether first, fall back to Wi-Fi. Idempotent failover for GET (reads are safe to retry).
    private func get(_ path: String) async throws -> Data {
        let candidates = routeCandidates(preferred: resolvedBase)
        var lastErr: Error = URLError(.cannotConnectToHost)
        for base in candidates {
            do {
                var req = URLRequest(url: base.appending(path: path))
                req.timeoutInterval = 3
                if let token = WatchConnectionStore.shared.token {
                    req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
                }
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
    /// `allowTimeoutFailover`: idempotent safety endpoints (kill/resume) opt in — an ABSENT
    /// tether subnet fails as .timedOut (same blackhole the GET path above documents), so
    /// without it the Wi-Fi retry never happens and a STOP dies silently (H13). Keep it
    /// false for non-idempotent POSTs (record toggle) so a command is never double-applied.
    @discardableResult
    private func post(_ path: String, body: [String: Any], allowTimeoutFailover: Bool = false) async -> Bool {
        guard let payload = try? JSONSerialization.data(withJSONObject: body) else { return false }
        var failoverCodes: [URLError.Code] = [.cannotConnectToHost, .cannotFindHost, .dnsLookupFailed]
        if allowTimeoutFailover {
            failoverCodes += [.timedOut, .networkConnectionLost]
        }
        let candidates = routeCandidates(preferred: resolvedBase)
        for base in candidates {
            do {
                var req = URLRequest(url: base.appending(path: path))
                req.httpMethod = "POST"
                req.timeoutInterval = 5
                req.setValue("application/json", forHTTPHeaderField: "Content-Type")
                if let token = WatchConnectionStore.shared.token {
                    req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
                }
                req.httpBody = payload
                let (_, resp) = try await URLSession.shared.data(for: req)
                if let http = resp as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                    return false
                }
                resolvedBase = base
                return true
            } catch let ue as URLError where failoverCodes.contains(ue.code) {
                resolvedBase = nil
                continue
            } catch {
                return false
            }
        }
        return false
    }
}

// MARK: - Route helpers

private extension WatchClient {
    /// Prefer the cached route but keep both hardcoded fallbacks so a single
    /// cached-route failure does not prevent failover in the same request.
    func routeCandidates(preferred: URL? = nil) -> [URL] {
        let fallbacks = [tetherBase, wifiBase]
        guard let preferred else { return fallbacks }
        var seen = Set<String>()
        var result: [URL] = []
        for url in [preferred] + fallbacks {
            guard seen.insert(url.absoluteString).inserted else { continue }
            result.append(url)
        }
        return result
    }
}
