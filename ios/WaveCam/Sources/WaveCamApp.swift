import SwiftUI

@main
struct WaveCamApp: App {
    @AppStorage(WaveCamDefaults.modeKey) private var modeRaw = WaveCamClient.Mode.live.rawValue
    @AppStorage(WaveCamDefaults.baseURLKey) private var baseURLString = WaveCamDefaults.baseURLString
    @AppStorage(WaveCamDefaults.tokenKey) private var token = ""
    @AppStorage(WaveCamDefaults.mockFallbackKey) private var mockFallbackEnabled = false

    @State private var client = WaveCamClient(mode: .live)

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(client)
                .preferredColorScheme(.dark)
                .task {
                    applyStoredSettings()
                    await client.refresh()
                }
                .onChange(of: modeRaw) { _, _ in applyStoredSettings() }
                .onChange(of: baseURLString) { _, _ in applyStoredSettings() }
                .onChange(of: token) { _, _ in applyStoredSettings() }
                .onChange(of: mockFallbackEnabled) { _, _ in applyStoredSettings() }
        }
    }

    private func applyStoredSettings() {
        let mode = WaveCamClient.Mode(rawValue: modeRaw) ?? .live
        let baseURL = URL(string: baseURLString) ?? WaveCamDefaults.baseURL
        client.configure(
            mode: mode,
            baseURL: baseURL,
            token: token,
            mockFallbackEnabled: mockFallbackEnabled
        )
    }
}
