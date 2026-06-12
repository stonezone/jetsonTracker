import SwiftUI
import WatchConnectivity

@main
struct WaveCamWatchApp: App {
    @State private var client = WatchClient()
    @StateObject private var wcDelegate = WatchSessionDelegate()

    var body: some Scene {
        WindowGroup {
            TabView {
                WatchStatusView()
                    .environment(client)
                    .tabItem { Label("Status", systemImage: "camera") }

                NavigationStack {
                    RecordSessionView()
                }
                .tabItem { Label("Record", systemImage: "waveform") }
            }
            .onAppear { wcDelegate.activate() }
        }
    }
}

// MARK: - WCSession delegate (activates the session; receives are handled on iPhone)

@MainActor
final class WatchSessionDelegate: NSObject, ObservableObject, WCSessionDelegate {
    func activate() {
        guard WCSession.isSupported() else { return }
        WCSession.default.delegate = self
        WCSession.default.activate()
    }

    nonisolated func session(_ session: WCSession, activationDidCompleteWith state: WCSessionActivationState, error: Error?) {}
}
