import SwiftUI

@main
struct WaveCamApp: App {
    @AppStorage(WaveCamDefaults.modeKey) private var modeRaw = WaveCamClient.Mode.live.rawValue
    @AppStorage(WaveCamDefaults.baseURLKey) private var baseURLString = WaveCamDefaults.baseURLString
    @AppStorage(WaveCamDefaults.mockFallbackKey) private var mockFallbackEnabled = false

    @State private var client = WaveCamClient(mode: .live)

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
                    await client.refresh()
                }
        }
    }

    private func applyStoredSettings() {
        let mode = WaveCamClient.Mode(rawValue: modeRaw) ?? .live
        let baseURL = URL(string: baseURLString) ?? WaveCamDefaults.baseURL
        let token = KeychainStore.load(account: KeychainStore.tokenAccount) ?? ""
        client.configure(
            mode: mode,
            baseURL: baseURL,
            token: token,
            mockFallbackEnabled: mockFallbackEnabled
        )
    }
}
