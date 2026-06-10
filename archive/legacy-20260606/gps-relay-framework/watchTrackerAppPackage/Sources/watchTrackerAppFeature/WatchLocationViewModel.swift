import SwiftUI
import HealthKit
import LocationCore
import WatchLocationProvider

@MainActor
public class WatchLocationViewModel: ObservableObject {
    // MARK: - Published Properties
    @Published public var isTracking: Bool = false
    @Published public var currentFix: LocationFix?
    @Published public var lastFixTimestamp: Date?
    @Published public var statusMessage: String = "Ready to start"
    @Published public var workoutState: String = "Not started"
    @Published public var fixCount: Int = 0
    @Published public var transportMetrics: WatchDirectTransport.Metrics?

    // MARK: - Private Properties
    private let locationProvider: WatchLocationProvider
    // Configure WatchDirectTransport in WatchLocationProvider to enable low-latency LTE bypass.
    // Set this to your Cloudflare Tunnel WebSocket URL (wss://...).
    private let jetsonTunnelURL = URL(string: "wss://ws.stonezone.net")!
    private let useDirectJetsonTransport = false

    // MARK: - Initialization
    public init() {
        self.locationProvider = WatchLocationProvider()
        self.locationProvider.delegate = self
    }

    // MARK: - Public Methods
    public func startTracking() {
        guard !isTracking else { return }

        if useDirectJetsonTransport {
            locationProvider.configureDirectTransport(serverURL: jetsonTunnelURL)
        } else {
            locationProvider.setDirectTransportEnabled(false)
        }
        locationProvider.startWorkoutAndStreaming()

        isTracking = true
        statusMessage = useDirectJetsonTransport ? "Tracking started" : "Tracking started - phone relay"
        workoutState = "Active"
    }

    public func stopTracking() {
        guard isTracking else { return }

        locationProvider.stop()

        isTracking = false
        statusMessage = "Tracking stopped"
        workoutState = "Stopped"
    }

    // MARK: - Private Methods
    private func updateStatusMessage() {
        if isTracking {
            statusMessage = "Fixes sent: \(fixCount)"
        } else {
            statusMessage = "Ready to start"
        }
    }
}

// MARK: - WatchLocationProviderDelegate
extension WatchLocationViewModel: WatchLocationProviderDelegate {
    nonisolated public func didProduce(_ fix: LocationFix) {
        Task { @MainActor [fix] in
            self.currentFix = fix
            self.lastFixTimestamp = Date()
            self.fixCount += 1
            self.updateStatusMessage()
        }
    }

    nonisolated public func didFail(_ error: Error) {
        Task { @MainActor in
            self.statusMessage = "Error: \(error.localizedDescription)"
        }
    }

    nonisolated public func didUpdateMetrics(_ metrics: WatchDirectTransport.Metrics) {
        Task { @MainActor [metrics] in
            self.transportMetrics = metrics
        }
    }
}
