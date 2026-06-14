import SwiftUI
import WatchConnectivity

@main
struct WaveCamApp: App {
    @AppStorage(WaveCamDefaults.modeKey) private var modeRaw = WaveCamClient.Mode.live.rawValue
    @AppStorage(WaveCamDefaults.baseURLKey) private var legacyBaseURLString = WaveCamDefaults.baseURLString
    @AppStorage(WaveCamDefaults.tetherBaseURLKey) private var tetherBaseURLString = WaveCamDefaults.tetherBaseURLString
    @AppStorage(WaveCamDefaults.wifiBaseURLKey) private var wifiBaseURLString = WaveCamDefaults.wifiBaseURLString
    @AppStorage(WaveCamDefaults.mockFallbackKey) private var mockFallbackEnabled = false

    @State private var client = WaveCamClient(mode: .live)
    @Environment(\.scenePhase) private var scenePhase

    // Phase-3 T3.1: phone-on-tripod sensor publisher. Lifecycle follows the app
    // foreground state; the publisher posts unconditionally while foregrounded
    // (server ignores when sensors.enabled=false on the backend).
    @State private var sensorPublisher: PhoneSensorPublisher? = nil

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(client)
                .preferredColorScheme(.dark)
                .task {
                    // Apply persisted settings once at launch. Runtime changes go through
                    // ConnectionView.applySettings (the single configure path); we deliberately
                    // do NOT observe @AppStorage here, because writing those keys on Apply would
                    // re-fire client.configure redundantly (iOS review #8).
                    KeychainStore.migrateLegacyToken(legacyDefaultsKey: WaveCamDefaults.tokenKey)
                    applyStoredSettings()
                    // Activate WatchConnectivity receiver for incoming session JSONL files.
                    // Set the activation callback first so the broadcast fires once the
                    // session is .activated — on first launch broadcastWatchContext() is a
                    // no-op if called before activation completes (activationState=.notActivated).
                    WatchSessionReceiver.shared.onActivated = { [self] in
                        broadcastWatchContext()
                    }
                    WatchSessionReceiver.shared.activate()
                    await client.refresh()
                    // Start the sensor publisher after settings are applied so it inherits
                    // the configured client URL and mode.
                    let publisher = PhoneSensorPublisher(client: client)
                    sensorPublisher = publisher
                    publisher.start()
                }
                .onChange(of: scenePhase) { _, phase in
                    // Pause the 1Hz status poll in the background (beach battery);
                    // .inactive is transient (app switcher, Control Center) — ignore.
                    switch phase {
                    case .background:
                        client.setPollingActive(false)
                        sensorPublisher?.stop()
                    case .active:
                        client.setPollingActive(true)
                        sensorPublisher?.start()
                    default: break
                    }
                }
        }
    }

    private func applyStoredSettings() {
        let mode = WaveCamClient.Mode(rawValue: modeRaw) ?? .live
        let routeURLs = storedRouteURLs()
        let token = KeychainStore.load(account: KeychainStore.tokenAccount) ?? ""
        client.configure(
            mode: mode,
            tetherBaseURL: routeURLs.tether,
            wifiBaseURL: routeURLs.wifi,
            token: token,
            mockFallbackEnabled: mockFallbackEnabled
        )
    }

    /// Pushes the stored token + URLs to the paired watch so WatchClient has
    /// current credentials without requiring a manual Connection Settings Apply.
    private func broadcastWatchContext() {
        guard WCSession.isSupported(), WCSession.default.activationState == .activated else { return }
        let token = KeychainStore.load(account: KeychainStore.tokenAccount) ?? ""
        let ctx: [String: Any] = [
            "wavecam_auth_token": token,
            "wavecam_tether_url": tetherBaseURLString,
            "wavecam_wifi_url": wifiBaseURLString,
        ]
        try? WCSession.default.updateApplicationContext(ctx)
    }

    private func storedRouteURLs() -> (tether: URL, wifi: URL) {
        var tether = URL(string: tetherBaseURLString) ?? WaveCamDefaults.tetherBaseURL
        var wifi = URL(string: wifiBaseURLString) ?? WaveCamDefaults.wifiBaseURL

        if tetherBaseURLString == WaveCamDefaults.tetherBaseURLString,
           wifiBaseURLString == WaveCamDefaults.wifiBaseURLString,
           legacyBaseURLString != WaveCamDefaults.baseURLString,
           let legacyURL = URL(string: legacyBaseURLString) {
            if legacyBaseURLString == WaveCamDefaults.legacyLANBaseURLString ||
                legacyBaseURLString.contains("192.168.") {
                wifi = legacyURL
            } else {
                tether = legacyURL
            }
        }

        return (tether: tether, wifi: wifi)
    }
}
