import SwiftUI

@main
struct WaveCamApp: App {
    @State private var client = WaveCamClient(mode: .mock)

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(client)
                .preferredColorScheme(.dark)
        }
    }
}
