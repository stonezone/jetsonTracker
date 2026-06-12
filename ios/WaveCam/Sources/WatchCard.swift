import SwiftUI
import WatchConnectivity

/// Connect-tab card surfacing the watch link: pairing/reachability state and
/// the session files the watch has delivered (saved to Documents by
/// WatchSessionReceiver; also visible in the Files app). Until this existed
/// the watch was invisible from the phone — files arrived silently.
struct WatchCard: View {
    @State private var paired = false
    @State private var appInstalled = false
    @State private var reachable = false
    @State private var files: [(name: String, size: String, date: String)] = []

    var body: some View {
        OperatorCard(title: "WATCH") {
            HStack(spacing: WCSpace.sm) {
                GlassChip(text: paired ? "PAIRED" : "NOT PAIRED",
                          color: paired ? WC.ok : WC.warn, dot: paired)
                GlassChip(text: appInstalled ? "APP ON WATCH" : "APP MISSING",
                          color: appInstalled ? WC.ok : WC.warn, dot: appInstalled)
                GlassChip(text: reachable ? "REACHABLE" : "ASLEEP",
                          color: reachable ? WC.ok : WC.warn, dot: reachable)
                Spacer()
            }
            OperatorDivider()
            if files.isEmpty {
                Text("No recorded sessions received yet. Record on the watch (Record tab) — files land here and in the Files app.")
                    .font(.system(size: 12))
                    .foregroundStyle(WC.muted)
            } else {
                OperatorSectionLabel("RECEIVED SESSIONS (\(files.count))")
                ForEach(files.prefix(5), id: \.name) { f in
                    HStack {
                        Text(f.name).font(.system(size: 11, design: .monospaced))
                            .lineLimit(1).truncationMode(.middle)
                        Spacer()
                        Text(f.size).font(.system(size: 11)).foregroundStyle(WC.muted)
                        Text(f.date).font(.system(size: 11)).foregroundStyle(WC.muted)
                    }
                }
            }
        }
        .onAppear(perform: refresh)
        .onReceive(NotificationCenter.default.publisher(
            for: WatchSessionReceiver.fileReceivedNotification)) { _ in refresh() }
    }

    private func refresh() {
        let s = WCSession.default
        paired = s.isPaired
        appInstalled = s.isWatchAppInstalled
        reachable = s.isReachable
        files = Self.sessionFiles()
    }

    private static func sessionFiles() -> [(String, String, String)] {
        guard let docs = FileManager.default.urls(
            for: .documentDirectory, in: .userDomainMask).first else { return [] }
        let fm = FileManager.default
        let urls = (try? fm.contentsOfDirectory(
            at: docs, includingPropertiesForKeys: [.fileSizeKey, .contentModificationDateKey]))
            ?? []
        let df = DateFormatter(); df.dateFormat = "MM/dd HH:mm"
        return urls
            .filter { $0.lastPathComponent.hasPrefix("watch_session_") }
            .sorted { ($0.lastPathComponent) > ($1.lastPathComponent) }
            .map { u in
                let vals = try? u.resourceValues(forKeys: [.fileSizeKey, .contentModificationDateKey])
                let kb = (vals?.fileSize).map { String(format: "%.0f KB", Double($0) / 1024) } ?? "—"
                let dt = (vals?.contentModificationDate).map { df.string(from: $0) } ?? "—"
                return (u.lastPathComponent, kb, dt)
            }
    }
}
