import SwiftUI

@main
struct WaveCamWatchApp: App {
    @State private var client = WatchClient()

    var body: some Scene {
        WindowGroup {
            WatchStatusView()
                .environment(client)
        }
    }
}
