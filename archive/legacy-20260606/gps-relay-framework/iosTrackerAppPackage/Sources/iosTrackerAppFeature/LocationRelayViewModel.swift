import SwiftUI
import LocationCore
import LocationRelayService
import WebSocketTransport

#if canImport(UIKit)
import UIKit
#endif

@MainActor
public final class LocationRelayViewModel: ObservableObject {

    // MARK: - Published Properties
    @Published public private(set) var isRelayActive: Bool = false
    @Published public private(set) var baseFix: LocationFix?
    @Published public private(set) var remoteFix: LocationFix?
    @Published public private(set) var relayHealth: RelayHealth = .idle
    @Published public private(set) var connectionState: ConnectionState = .disconnected
    @Published public private(set) var isWatchConnected: Bool = false
    @Published public private(set) var lastBaseTimestamp: Date?
    @Published public private(set) var lastRemoteTimestamp: Date?

    @Published public var webSocketURL: String {
        didSet {
            guard webSocketURL != oldValue else { return }
            UserDefaults.standard.set(webSocketURL, forKey: Defaults.webSocketURL)
            if URL(string: webSocketURL)?.scheme?.lowercased() == "wss", allowInsecureConnections {
                allowInsecureConnections = false
            }
        }
    }

    @Published public var trackingMode: TrackingMode {
        didSet {
            guard trackingMode != oldValue else { return }
            UserDefaults.standard.set(trackingMode.rawValue, forKey: Defaults.trackingMode)
            var updatedConfig = coordinator.configuration
            updatedConfig.trackingMode = trackingMode
            coordinator.configuration = updatedConfig
            updateStatusMessage()
        }
    }

    @Published public var allowInsecureConnections: Bool {
        didSet {
            guard allowInsecureConnections != oldValue else { return }
            UserDefaults.standard.set(allowInsecureConnections, forKey: Defaults.allowInsecureConnections)
        }
    }

    @Published public var webSocketEnabled: Bool {
        didSet {
            guard webSocketEnabled != oldValue else { return }
            UserDefaults.standard.set(webSocketEnabled, forKey: Defaults.webSocketEnabled)
        }
    }

    @Published public var isWatchRelayEnabled: Bool {
        didSet {
            guard isWatchRelayEnabled != oldValue else { return }
            UserDefaults.standard.set(isWatchRelayEnabled, forKey: Defaults.isWatchRelayEnabled)
            var updatedConfig = coordinator.configuration
            updatedConfig.isWatchRelayEnabled = isWatchRelayEnabled
            coordinator.configuration = updatedConfig
        }
    }

    @Published public var statusMessage: String = "Ready to start"
    @Published public var authorizationMessage: String?

    // MARK: - Private Properties
    private let coordinator: LocationRelayCoordinator
    private var shouldResumeRelay: Bool

    // MARK: - Initialization
    public init(coordinator: LocationRelayCoordinator? = nil) {
        let defaults = UserDefaults.standard
        let savedURL = defaults.string(forKey: Defaults.webSocketURL)
        let shouldMigrateWebSocketURL = savedURL == nil || savedURL == Defaults.legacyLocalWebSocketURL
        let resolvedURL = shouldMigrateWebSocketURL ? Defaults.productionWebSocketURL : (savedURL ?? Defaults.productionWebSocketURL)
        let isSecureWebSocketURL = URL(string: resolvedURL)?.scheme?.lowercased() == "wss"
        let savedMode = defaults.string(forKey: Defaults.trackingMode).flatMap(TrackingMode.init(rawValue:)) ?? .balanced
        let savedAllow = (shouldMigrateWebSocketURL || isSecureWebSocketURL) ? false : (defaults.object(forKey: Defaults.allowInsecureConnections) as? Bool ?? false)
        let savedEnabled = defaults.object(forKey: Defaults.webSocketEnabled) as? Bool ?? true
        let savedWatchRelay = defaults.object(forKey: Defaults.isWatchRelayEnabled) as? Bool ?? true
        let savedShouldResume = defaults.object(forKey: Defaults.shouldResumeRelay) as? Bool ?? savedEnabled

        if shouldMigrateWebSocketURL {
            defaults.set(resolvedURL, forKey: Defaults.webSocketURL)
        }
        if shouldMigrateWebSocketURL || isSecureWebSocketURL {
            defaults.set(false, forKey: Defaults.allowInsecureConnections)
        }

        defaults.set(savedEnabled, forKey: Defaults.webSocketEnabled)
        defaults.set(savedWatchRelay, forKey: Defaults.isWatchRelayEnabled)
        defaults.set(savedShouldResume, forKey: Defaults.shouldResumeRelay)

        self.webSocketURL = resolvedURL
        self.trackingMode = savedMode
        self.allowInsecureConnections = savedAllow
        self.webSocketEnabled = savedEnabled
        self.isWatchRelayEnabled = savedWatchRelay
        self.shouldResumeRelay = savedShouldResume

        let initialCoordinator = coordinator ?? LocationRelayCoordinator(configuration: .init(trackingMode: savedMode))
        self.coordinator = initialCoordinator

        self.coordinator.delegate = self
        self.coordinator.configuration.trackingMode = savedMode
        self.coordinator.configuration.isWatchRelayEnabled = savedWatchRelay
        updateStatusMessage()
    }

    // MARK: - Public API

    public func resumeRelayIfNeeded() {
        guard shouldResumeRelay, !isRelayActive else { return }
        startRelay()
    }

    public func startRelay() {
        guard !isRelayActive else { return }

        var updatedConfig = coordinator.configuration

        // Only configure WebSocket if enabled
        if webSocketEnabled {
            guard let url = URL(string: webSocketURL) else {
                statusMessage = "Invalid WebSocket URL"
                return
            }

            let scheme = url.scheme?.lowercased()
            if scheme != "ws" && scheme != "wss" {
                statusMessage = "URL must start with ws:// or wss://"
                return
            }

            if scheme == "ws" && !allowInsecureConnections {
                statusMessage = "Enable secure wss:// or allow insecure ws:// connections."
                return
            }

            var wsConfig: WebSocketTransportConfiguration
            if scheme == "wss" {
                // Smaller payloads reduce LTE airtime/queueing and improve tail latency.
                wsConfig = .cellular()
            } else {
                wsConfig = WebSocketTransportConfiguration(allowInsecureConnections: allowInsecureConnections)
            }
            wsConfig.customHeaders["X-Client-Type"] = "ios"
            #if canImport(UIKit)
            wsConfig.customHeaders["X-Device-Id"] = UIDevice.current.identifierForVendor?.uuidString ?? "ios"
            #else
            wsConfig.customHeaders["X-Device-Id"] = "ios"
            #endif
            wsConfig.sessionConfiguration.allowsCellularAccess = true
            wsConfig.sessionConfiguration.waitsForConnectivity = true
            updatedConfig.webSocketEndpoint = .init(url: url, configuration: wsConfig)
        } else {
            // Disable WebSocket endpoint
            updatedConfig.webSocketEndpoint = nil
        }

        coordinator.configuration = updatedConfig

        coordinator.start()
        connectionState = coordinator.connectionState
        isRelayActive = true
        shouldResumeRelay = true
        UserDefaults.standard.set(true, forKey: Defaults.shouldResumeRelay)
        authorizationMessage = nil
        statusMessage = webSocketEnabled ? "Relay started" : "Relay started (WebSocket disabled)"
    }

    public func stopRelay() {
        guard isRelayActive else { return }
        shouldResumeRelay = false
        UserDefaults.standard.set(false, forKey: Defaults.shouldResumeRelay)
        coordinator.stop()
        isRelayActive = false
        connectionState = .disconnected
        relayHealth = .idle
        isWatchConnected = false
        baseFix = nil
        remoteFix = nil
        lastBaseTimestamp = nil
        lastRemoteTimestamp = nil
        statusMessage = "Relay stopped"
    }

    public func dismissAuthorizationMessage() {
        authorizationMessage = nil
    }

    // MARK: - Helpers

    private func updateStatusMessage() {
        if !isRelayActive {
            statusMessage = "Ready to start"
            return
        }

        switch relayHealth {
        case .idle:
            statusMessage = "Starting relay..."
        case .streaming:
            statusMessage = "Streaming location data"
        case .degraded(let reason):
            statusMessage = "Degraded: \(reason)"
        }
    }
}

// MARK: - Defaults Keys

private enum Defaults {
    static let productionWebSocketURL = "wss://ws.stonezone.net"
    static let legacyLocalWebSocketURL = "ws://192.168.55.1:8765"
    static let webSocketURL = "relay.webSocketURL"
    static let trackingMode = "relay.trackingMode"
    static let allowInsecureConnections = "relay.allowInsecureConnections"
    static let webSocketEnabled = "relay.webSocketEnabled"
    static let isWatchRelayEnabled = "relay.isWatchRelayEnabled"
    static let shouldResumeRelay = "relay.shouldResume"
}

// MARK: - LocationRelayCoordinatorDelegate

@available(iOS 13.0, *)
extension LocationRelayViewModel: LocationRelayCoordinatorDelegate {
    nonisolated public func relayCoordinator(_ coordinator: LocationRelayCoordinator, didUpdate update: RelayUpdate) {
        Task { @MainActor [weak self] in
            self?.baseFix = update.base
            self?.remoteFix = update.remote
            let now = Date()
            if update.base != nil {
                self?.lastBaseTimestamp = now
            }
            if update.remote != nil {
                self?.lastRemoteTimestamp = now
            }
        }
    }

    nonisolated public func relayCoordinator(_ coordinator: LocationRelayCoordinator, didChangeHealth health: RelayHealth) {
        Task { @MainActor [weak self] in
            self?.relayHealth = health
            self?.updateStatusMessage()
        }
    }

    nonisolated public func relayCoordinator(_ coordinator: LocationRelayCoordinator, didUpdateConnection state: ConnectionState) {
        Task { @MainActor [weak self] in
            self?.connectionState = state
        }
    }

    nonisolated public func relayCoordinator(_ coordinator: LocationRelayCoordinator, didEncounterError error: Error) {
        Task { @MainActor [weak self] in
            self?.statusMessage = "Connection error: \(error.localizedDescription)"
        }
    }

    nonisolated public func relayCoordinator(_ coordinator: LocationRelayCoordinator, authorizationDidFail error: LocationRelayError) {
        Task { @MainActor [weak self] in
            self?.authorizationMessage = error.errorDescription ?? "Authorization issue."
            self?.statusMessage = error.errorDescription ?? "Authorization issue."
        }
    }

    nonisolated public func relayCoordinator(_ coordinator: LocationRelayCoordinator, watchConnectionDidChange isConnected: Bool) {
        Task { @MainActor [weak self] in
            self?.isWatchConnected = isConnected
        }
    }
}
