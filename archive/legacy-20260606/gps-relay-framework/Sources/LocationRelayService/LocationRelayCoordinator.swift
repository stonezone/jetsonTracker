#if os(iOS)
import Foundation
import CoreLocation
import LocationCore
import WebSocketTransport

@available(iOS 13.0, *)
public protocol LocationRelayCoordinatorDelegate: AnyObject {
    func relayCoordinator(_ coordinator: LocationRelayCoordinator, didUpdate update: RelayUpdate)
    func relayCoordinator(_ coordinator: LocationRelayCoordinator, didChangeHealth health: RelayHealth)
    func relayCoordinator(_ coordinator: LocationRelayCoordinator, didUpdateConnection state: ConnectionState)
    func relayCoordinator(_ coordinator: LocationRelayCoordinator, didEncounterError error: Error)
    func relayCoordinator(_ coordinator: LocationRelayCoordinator, authorizationDidFail error: LocationRelayError)
    func relayCoordinator(_ coordinator: LocationRelayCoordinator, watchConnectionDidChange isConnected: Bool)
}

@available(iOS 13.0, *)
public extension LocationRelayCoordinatorDelegate {
    func relayCoordinator(_ coordinator: LocationRelayCoordinator, didUpdate update: RelayUpdate) {}
    func relayCoordinator(_ coordinator: LocationRelayCoordinator, didChangeHealth health: RelayHealth) {}
    func relayCoordinator(_ coordinator: LocationRelayCoordinator, didUpdateConnection state: ConnectionState) {}
    func relayCoordinator(_ coordinator: LocationRelayCoordinator, didEncounterError error: Error) {}
    func relayCoordinator(_ coordinator: LocationRelayCoordinator, authorizationDidFail error: LocationRelayError) {}
    func relayCoordinator(_ coordinator: LocationRelayCoordinator, watchConnectionDidChange isConnected: Bool) {}
}

@available(iOS 13.0, *)
public final class LocationRelayCoordinator: NSObject {

    public struct Configuration: @unchecked Sendable {
        public struct WebSocketEndpoint: Sendable {
            public var url: URL
            public var configuration: WebSocketTransportConfiguration

            public init(
                url: URL,
                configuration: WebSocketTransportConfiguration = WebSocketTransportConfiguration()
            ) {
                self.url = url
                self.configuration = configuration
            }
        }

        public var trackingMode: TrackingMode
        public var qualityOverride: QualityThresholds?
        public var webSocketEndpoint: WebSocketEndpoint?
        public var additionalTransports: [LocationTransport]
        public var fusionMode: LocationRelayService.FusionMode
        public var isWatchRelayEnabled: Bool

        public init(
            trackingMode: TrackingMode = .balanced,
            qualityOverride: QualityThresholds? = nil,
            webSocketEndpoint: WebSocketEndpoint? = nil,
            additionalTransports: [LocationTransport] = [],
            fusionMode: LocationRelayService.FusionMode = .disabled,
            isWatchRelayEnabled: Bool = false
        ) {
            self.trackingMode = trackingMode
            self.qualityOverride = qualityOverride
            self.webSocketEndpoint = webSocketEndpoint
            self.additionalTransports = additionalTransports
            self.fusionMode = fusionMode
            self.isWatchRelayEnabled = isWatchRelayEnabled
        }
    }

    public weak var delegate: LocationRelayCoordinatorDelegate?
    public private(set) var connectionState: ConnectionState = .disconnected
    public private(set) var latestHealth: RelayHealth = .idle

    public var configuration: Configuration {
        didSet {
            service.trackingMode = configuration.trackingMode
            service.qualityOverride = configuration.qualityOverride
            service.fusionMode = configuration.fusionMode
            service.isWatchRelayEnabled = configuration.isWatchRelayEnabled
            webSocketTransport = nil // Rebuild on next start if endpoint changed
            activeWebSocketEndpoint = nil
        }
    }

    private let service: LocationRelayService
    private var isRunning = false
    private var webSocketTransport: WebSocketTransport?
    private var activeWebSocketEndpoint: Configuration.WebSocketEndpoint?
    private(set) var isWatchConnected: Bool = false

    // MARK: - Initialisation

    public init(
        configuration: Configuration = Configuration(),
        locationManager: LocationManagerProtocol = CLLocationManager()
    ) {
        self.configuration = configuration
        self.service = LocationRelayService(
            locationManager: locationManager,
            trackingMode: configuration.trackingMode
        )
        super.init()
        self.service.qualityOverride = configuration.qualityOverride
        self.service.fusionMode = configuration.fusionMode
        self.service.isWatchRelayEnabled = configuration.isWatchRelayEnabled
        self.service.delegate = self
    }

    // MARK: - Lifecycle

    public func start() {
        guard !isRunning else { return }
        attachTransports()
        connectionState = webSocketTransport?.connectionState ?? .disconnected
        service.start()
        isRunning = true
    }

    public func stop() {
        guard isRunning else { return }
        service.stop()
        webSocketTransport?.close()
        connectionState = .disconnected
        isWatchConnected = false
        isRunning = false
    }

    public func restart(with newConfiguration: Configuration? = nil) {
        stop()
        if let newConfiguration {
            configuration = newConfiguration
        }
        start()
    }

    // MARK: - Accessors

    public var currentSnapshot: RelayUpdate? {
        service.currentSnapshot()
    }

    public var currentFix: LocationFix? {
        service.currentFix
    }

    // MARK: - Private Helpers

    private func attachTransports() {
        // Recreate WebSocket transport if configuration changed
        if let endpoint = configuration.webSocketEndpoint {
            if webSocketTransport == nil || activeWebSocketEndpoint?.url != endpoint.url {
                webSocketTransport = WebSocketTransport(
                    url: endpoint.url,
                    configuration: endpoint.configuration
                )
                webSocketTransport?.delegate = self
            }
            activeWebSocketEndpoint = endpoint
        } else {
            webSocketTransport = nil
            activeWebSocketEndpoint = nil
        }

        var transportsToAttach: [LocationTransport] = configuration.additionalTransports
        if let webSocketTransport {
            transportsToAttach.insert(webSocketTransport, at: 0)
        }

        transportsToAttach.forEach { service.addTransport($0) }
    }
}

// MARK: - LocationRelayDelegate

@available(iOS 13.0, *)
extension LocationRelayCoordinator: LocationRelayDelegate {
    public func didUpdate(_ update: RelayUpdate) {
        delegate?.relayCoordinator(self, didUpdate: update)
    }

    public func healthDidChange(_ health: RelayHealth) {
        latestHealth = health
        delegate?.relayCoordinator(self, didChangeHealth: health)
    }

    public func watchConnectionDidChange(_ isConnected: Bool) {
        isWatchConnected = isConnected
        delegate?.relayCoordinator(self, watchConnectionDidChange: isConnected)
    }

    public func authorizationDidFail(_ error: LocationRelayError) {
        delegate?.relayCoordinator(self, authorizationDidFail: error)
    }
}

// MARK: - WebSocketTransportDelegate

@available(iOS 13.0, *)
extension LocationRelayCoordinator: WebSocketTransportDelegate {
    public func webSocketTransport(_ transport: WebSocketTransport, didChangeState state: ConnectionState) {
        connectionState = state
        delegate?.relayCoordinator(self, didUpdateConnection: state)
    }

    public func webSocketTransport(_ transport: WebSocketTransport, didEncounterError error: Error) {
        delegate?.relayCoordinator(self, didEncounterError: error)
    }
}
#endif
