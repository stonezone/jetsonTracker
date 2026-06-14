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

// MARK: - WCSession delegate (activates session; receives context from iPhone)

@MainActor
final class WatchSessionDelegate: NSObject, ObservableObject, WCSessionDelegate {
    func activate() {
        guard WCSession.isSupported() else { return }
        WCSession.default.delegate = self
        WCSession.default.activate()
    }

    nonisolated func session(_ session: WCSession, activationDidCompleteWith state: WCSessionActivationState, error: Error?) {
        // Apply any context that arrived before activation completed.
        let ctx = session.receivedApplicationContext
        if !ctx.isEmpty {
            Task { @MainActor in WatchConnectionStore.shared.apply(ctx) }
        }
    }

    /// Called when the iPhone pushes updated connection settings or token.
    nonisolated func session(_ session: WCSession, didReceiveApplicationContext applicationContext: [String: Any]) {
        Task { @MainActor in WatchConnectionStore.shared.apply(applicationContext) }
    }

    /// Called after the OS finishes delivering a transferred file to the iPhone.
    /// Delete the local JSONL source only on success — the file must persist until
    /// the OS completes the transfer (WCSession.transferFile is async by nature).
    nonisolated func session(_ session: WCSession,
                             didFinish fileTransfer: WCSessionFileTransfer,
                             error: Error?) {
        guard error == nil else { return }
        let url = fileTransfer.file.fileURL
        try? FileManager.default.removeItem(at: url)
    }
}
