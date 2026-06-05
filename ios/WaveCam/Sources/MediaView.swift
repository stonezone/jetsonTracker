import SwiftUI
import UniformTypeIdentifiers

/// Browse recordings stored on the Orin, grouped by day (collapsible). Download or
/// share one, or enter Select mode to bulk-download / bulk-delete. Read-only of the
/// camera — no recording control. Delete is feature-detected (hidden until the Orin
/// exposes DELETE /api/v1/media/{name}).
struct MediaView: View {
    @Environment(WaveCamClient.self) private var client

    @State private var files: [WCMediaFile] = []
    @State private var loadState: MediaLoadState = .idle
    @State private var downloadProgress: [String: DownloadState] = [:]
    @State private var shareItem: ShareableFile?

    @State private var collapsedDays: Set<String> = []
    @State private var didInitCollapse = false

    @State private var isSelecting = false
    @State private var selected: Set<String> = []

    @State private var deleteSupported = false
    @State private var confirmingDelete = false
    @State private var bulkBusy = false

    var body: some View {
        VStack(spacing: 0) {
            mediaHeader
            Divider().background(WC.line)
            contentBody
        }
        .background(WC.bg.ignoresSafeArea())
        .safeAreaInset(edge: .bottom) {
            if isSelecting && !selected.isEmpty { bulkBar }
        }
        .task { await load() }
        .sheet(item: $shareItem) { item in
            ShareSheet(url: item.url).presentationDetents([.medium, .large])
        }
        .alert("Delete \(selected.count) recording\(selected.count == 1 ? "" : "s")?",
               isPresented: $confirmingDelete) {
            Button("Delete", role: .destructive) { Task { await deleteSelected() } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This permanently removes \(byteLabel(selectedBytes)) from the Orin. Can't be undone.")
        }
    }

    // MARK: - Header

    private var mediaHeader: some View {
        GlassCard(cornerRadius: 0, padding: 0) {
            HStack(spacing: WCSpace.md) {
                VStack(alignment: .leading, spacing: WCSpace.xs) {
                    Text("RECORDINGS")
                        .font(WCFont.bodyBold).tracking(1.5).foregroundStyle(WC.muted)
                    if let freeGb = client.status?.media?.freeGb {
                        Text(String(format: "%.1f GB free", freeGb))
                            .font(WCFont.captionMono).foregroundStyle(WC.faint)
                    }
                }
                Spacer()
                if case .loaded = loadState, !files.isEmpty {
                    Button {
                        withAnimation(.easeInOut(duration: 0.15)) {
                            isSelecting.toggle()
                            if !isSelecting { selected.removeAll() }
                        }
                    } label: {
                        Text(isSelecting ? "Done" : "Select")
                            .font(WCFont.bodyBold).foregroundStyle(WC.accent)
                            .frame(minHeight: 44).padding(.horizontal, WCSpace.sm)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(isSelecting ? "Exit selection" : "Select recordings")
                }
                if case .loading = loadState {
                    ProgressView().tint(WC.accent).scaleEffect(0.8).frame(width: 44, height: 44)
                } else {
                    GlassIconButton(systemImage: "arrow.clockwise", state: .normal,
                                    action: { Task { await load() } })
                        .accessibilityLabel("Refresh recordings")
                }
            }
            .padding(.horizontal, WCSpace.lg)
            .padding(.vertical, WCSpace.sm + WCSpace.xs)
        }
    }

    // MARK: - Content states

    @ViewBuilder
    private var contentBody: some View {
        switch loadState {
        case .idle, .loading:
            loadingPlaceholder
        case .unavailable:
            MediaUnavailableView()
        case .offline:
            MediaOfflineView { Task { await load() } }
        case .mockMode:
            MediaMockView()
        case .loaded:
            if files.isEmpty {
                MediaEmptyView { Task { await load() } }
            } else {
                dayGroupedList
            }
        }
    }

    private var loadingPlaceholder: some View {
        VStack(spacing: 16) {
            ProgressView().tint(WC.ok)
            Text("LOADING RECORDINGS")
                .font(.system(size: 11, weight: .semibold)).tracking(1.5).foregroundStyle(WC.muted)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - Day-grouped list

    private var dayGroupedList: some View {
        ScrollView {
            LazyVStack(spacing: WCSpace.md) {
                ForEach(groupedDays) { day in
                    daySection(day)
                }
            }
            .padding(.horizontal, WCSpace.lg)
            .padding(.vertical, WCSpace.md)
            .padding(.bottom, WCSpace.xl)
        }
        .scrollIndicators(.hidden)
        .background(WC.bg)
    }

    private func daySection(_ day: MediaDay) -> some View {
        let collapsed = collapsedDays.contains(day.id)
        return VStack(spacing: 0) {
            Button {
                withAnimation(.easeInOut(duration: 0.15)) { toggleDay(day.id) }
            } label: {
                HStack(spacing: WCSpace.sm) {
                    Image(systemName: collapsed ? "chevron.right" : "chevron.down")
                        .font(.system(size: 11, weight: .bold)).foregroundStyle(WC.faint).frame(width: 14)
                    Text(day.label).font(WCFont.bodyBold).foregroundStyle(WC.txt)
                    Text("\(day.files.count)")
                        .font(WCFont.captionMono).foregroundStyle(WC.faint)
                        .padding(.horizontal, 6).padding(.vertical, 1)
                        .background(WC.line, in: Capsule())
                    Spacer()
                    if isSelecting {
                        Button {
                            withAnimation(.easeInOut(duration: 0.12)) { toggleDaySelection(day) }
                        } label: {
                            Text(allSelected(in: day) ? "Clear" : "All")
                                .font(WCFont.caption).foregroundStyle(WC.accent)
                        }
                        .buttonStyle(.plain)
                    } else {
                        Text(byteLabel(day.totalBytes)).font(WCFont.captionMono).foregroundStyle(WC.faint)
                    }
                }
                .padding(.vertical, WCSpace.sm)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if !collapsed {
                GlassSurface(cornerRadius: WCRadius.md) {
                    LazyVStack(spacing: 0) {
                        ForEach(day.files) { file in
                            MediaFileRow(
                                file: file,
                                downloadState: downloadProgress[file.name] ?? .idle,
                                isSelecting: isSelecting,
                                isSelected: selected.contains(file.name),
                                onDownload: { Task { await download(file) } },
                                onShare: { localURL in shareItem = ShareableFile(url: localURL) },
                                onToggleSelect: { toggleSelect(file.name) }
                            )
                            if file.id != day.files.last?.id {
                                Divider().background(WC.line).padding(.leading, WCSpace.lg)
                            }
                        }
                    }
                }
            }
        }
    }

    // MARK: - Bulk action bar

    private var bulkBar: some View {
        HStack(spacing: WCSpace.md) {
            VStack(alignment: .leading, spacing: 1) {
                Text("\(selected.count) selected").font(WCFont.bodyBold).foregroundStyle(WC.txt)
                Text(byteLabel(selectedBytes)).font(WCFont.captionMono).foregroundStyle(WC.faint)
            }
            Spacer()
            Button { Task { await downloadSelected() } } label: {
                Label("Download", systemImage: "arrow.down.circle")
                    .font(.system(size: 14, weight: .semibold)).foregroundStyle(WC.accent)
            }
            .buttonStyle(.plain).disabled(bulkBusy)
            if deleteSupported {
                Button { confirmingDelete = true } label: {
                    Label("Delete", systemImage: "trash")
                        .font(.system(size: 14, weight: .semibold)).foregroundStyle(WC.kill)
                }
                .buttonStyle(.plain).disabled(bulkBusy)
            }
        }
        .padding(.horizontal, WCSpace.lg)
        .padding(.vertical, WCSpace.md)
        .background(WC.ink)
        .overlay(Divider().background(WC.line), alignment: .top)
    }

    // MARK: - Grouping

    private var groupedDays: [MediaDay] {
        let cal = Calendar.current
        let groups = Dictionary(grouping: files) { cal.startOfDay(for: $0.createdAt) }
        return groups.keys.sorted(by: >).map { day in
            MediaDay(
                id: "\(Int(day.timeIntervalSince1970))",
                label: dayLabel(day, cal),
                files: (groups[day] ?? []).sorted { $0.createdAt > $1.createdAt }
            )
        }
    }

    private func dayLabel(_ day: Date, _ cal: Calendar) -> String {
        if cal.isDateInToday(day) { return "Today" }
        if cal.isDateInYesterday(day) { return "Yesterday" }
        return day.formatted(.dateTime.weekday(.abbreviated).month(.abbreviated).day())
    }

    private func toggleDay(_ id: String) {
        if collapsedDays.contains(id) { collapsedDays.remove(id) } else { collapsedDays.insert(id) }
    }

    // MARK: - Selection

    private var selectedBytes: Int {
        files.filter { selected.contains($0.name) }.reduce(0) { $0 + $1.sizeBytes }
    }

    private func toggleSelect(_ name: String) {
        if selected.contains(name) { selected.remove(name) } else { selected.insert(name) }
    }

    private func allSelected(in day: MediaDay) -> Bool {
        !day.files.isEmpty && day.files.allSatisfy { selected.contains($0.name) }
    }

    private func toggleDaySelection(_ day: MediaDay) {
        if allSelected(in: day) {
            day.files.forEach { selected.remove($0.name) }
        } else {
            day.files.forEach { selected.insert($0.name) }
        }
    }

    // MARK: - Actions

    private func load() async {
        guard loadState != .loading else { return }
        loadState = .loading
        guard client.mode == .live else { loadState = .mockMode; return }
        do {
            let fetched = try await client.mediaList()
            files = fetched
            loadState = .loaded
            if !didInitCollapse {
                collapsedDays = Set(groupedDays.dropFirst().map(\.id)) // today open, older collapsed
                didInitCollapse = true
            }
            // Drop selections for files that no longer exist.
            let names = Set(fetched.map(\.name))
            selected = selected.intersection(names)
            deleteSupported = await client.mediaDeleteSupported()
        } catch let err as WaveCamAPIError where err.statusCode == 503 {
            loadState = .unavailable
        } catch {
            loadState = .offline
        }
    }

    private func download(_ file: WCMediaFile) async {
        guard downloadProgress[file.name] != .downloading else { return }
        downloadProgress[file.name] = .downloading
        do {
            let localURL = try await client.downloadMedia(name: file.name)
            downloadProgress[file.name] = .done(localURL)
        } catch {
            downloadProgress[file.name] = .failed(error.localizedDescription)
        }
    }

    private func downloadSelected() async {
        guard !bulkBusy else { return }
        bulkBusy = true
        defer { bulkBusy = false }
        for file in files where selected.contains(file.name) {
            await download(file)
        }
    }

    private func deleteSelected() async {
        guard !bulkBusy, deleteSupported else { return }
        bulkBusy = true
        let targets = Array(selected)
        var anyOK = false
        for name in targets where await client.deleteMedia(name: name) {
            anyOK = true
        }
        bulkBusy = false
        selected.removeAll()
        isSelecting = false
        if anyOK { await load() }
    }
}

// MARK: - File row

private struct MediaFileRow: View {
    let file: WCMediaFile
    let downloadState: DownloadState
    let isSelecting: Bool
    let isSelected: Bool
    let onDownload: () -> Void
    let onShare: (URL) -> Void
    let onToggleSelect: () -> Void

    var body: some View {
        HStack(spacing: WCSpace.md) {
            if isSelecting {
                Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                    .font(.system(size: 21))
                    .foregroundStyle(isSelected ? WC.accent : WC.faint)
                    .frame(width: 28)
            } else {
                Image(systemName: "film")
                    .font(.system(size: 16, weight: .medium)).foregroundStyle(WC.accent).frame(width: 28)
            }

            VStack(alignment: .leading, spacing: WCSpace.xs) {
                Text(file.name)
                    .font(WCFont.mono).foregroundStyle(WC.txt).lineLimit(1).truncationMode(.middle)
                HStack(spacing: WCSpace.sm) {
                    Text(byteLabel(file.sizeBytes)).font(WCFont.captionMono).foregroundStyle(WC.faint)
                    Text(file.createdAt.formatted(date: .omitted, time: .shortened))
                        .font(WCFont.caption).foregroundStyle(WC.faint)
                }
            }

            Spacer(minLength: WCSpace.xs)

            if !isSelecting { rowActions }
        }
        .padding(.horizontal, WCSpace.lg)
        .padding(.vertical, WCSpace.md)
        .contentShape(Rectangle())
        .onTapGesture { if isSelecting { onToggleSelect() } }
    }

    @ViewBuilder
    private var rowActions: some View {
        switch downloadState {
        case .idle, .failed:
            Button(action: onDownload) {
                Label("Download", systemImage: "arrow.down.circle")
                    .labelStyle(.iconOnly).font(.system(size: 20)).frame(width: 44, height: 44)
            }
            .buttonStyle(.plain)
            .foregroundStyle(downloadState == .idle ? WC.ok : WC.warn)
            .accessibilityLabel("Download \(file.name)")
            .accessibilityHint(downloadState == .idle ? "" : "Previous download failed — tap to retry")
        case .downloading:
            ProgressView().tint(WC.ok).frame(width: 44, height: 44)
        case .done(let localURL):
            Button { onShare(localURL) } label: {
                Label("Share", systemImage: "square.and.arrow.up")
                    .labelStyle(.iconOnly).font(.system(size: 20)).frame(width: 44, height: 44)
            }
            .buttonStyle(.plain).foregroundStyle(WC.accent)
            .accessibilityLabel("Share \(file.name)")
        }
    }
}

/// One day's recordings, newest-first.
private struct MediaDay: Identifiable {
    let id: String
    let label: String
    let files: [WCMediaFile]
    var totalBytes: Int { files.reduce(0) { $0 + $1.sizeBytes } }
}

/// Shared size formatter (MB, or GB ≥ 1000 MB).
private func byteLabel(_ bytes: Int) -> String {
    let mb = Double(bytes) / 1_000_000
    return mb >= 1000 ? String(format: "%.1f GB", mb / 1000) : String(format: "%.0f MB", mb)
}

// MARK: - Empty / offline / unavailable states

private struct MediaEmptyView: View {
    let onRefresh: () -> Void
    var body: some View {
        MediaStateShell(icon: "tray", iconColor: WC.muted, title: "NO RECORDINGS",
                        detail: "Start a recording session from the Live tab. Files appear here once saved.",
                        actionLabel: "Refresh", onAction: onRefresh)
    }
}

private struct MediaOfflineView: View {
    let onRetry: () -> Void
    var body: some View {
        MediaStateShell(icon: "network.slash", iconColor: WC.warn, title: "OFFLINE",
                        detail: "Cannot reach the Orin. Connect via USB tether or Wi-Fi, then retry.",
                        actionLabel: "Retry", onAction: onRetry)
    }
}

private struct MediaUnavailableView: View {
    var body: some View {
        MediaStateShell(icon: "arrow.up.square", iconColor: WC.warn, title: "UPDATE REQUIRED",
                        detail: "Media listing is not available on this Orin firmware. Ask Codex to deploy a newer build.",
                        actionLabel: nil, onAction: nil)
    }
}

private struct MediaMockView: View {
    var body: some View {
        MediaStateShell(icon: "film", iconColor: WC.faint, title: "MOCK MODE",
                        detail: "Media browsing requires a live connection. Switch to Live mode in the Connect tab.",
                        actionLabel: nil, onAction: nil)
    }
}

private struct MediaStateShell: View {
    let icon: String
    let iconColor: Color
    let title: String
    let detail: String
    let actionLabel: String?
    let onAction: (() -> Void)?

    var body: some View {
        VStack(spacing: WCSpace.lg) {
            Image(systemName: icon).font(.system(size: 32, weight: .bold)).foregroundStyle(iconColor)
            Text(title).font(WCFont.heading).tracking(1.5).foregroundStyle(WC.txt)
            Text(detail).font(WCFont.body).foregroundStyle(WC.muted)
                .multilineTextAlignment(.center).frame(maxWidth: 280).fixedSize(horizontal: false, vertical: true)
            if let label = actionLabel, let action = onAction {
                GlassButton(label: label, role: .normal, action: action).frame(maxWidth: 200)
            }
        }
        .padding(WCSpace.xl).frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

// MARK: - Share sheet (UIActivityViewController bridge)

private struct ShareSheet: UIViewControllerRepresentable {
    let url: URL
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [url], applicationActivities: nil)
    }
    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}

// MARK: - Supporting types

private enum MediaLoadState: Equatable {
    case idle, loading, loaded
    case unavailable   // 503 from the backend — older Orin firmware
    case offline       // network / transport error
    case mockMode      // client.mode == .mock
}

enum DownloadState: Equatable {
    case idle
    case downloading
    case done(URL)
    case failed(String)

    static func == (lhs: DownloadState, rhs: DownloadState) -> Bool {
        switch (lhs, rhs) {
        case (.idle, .idle), (.downloading, .downloading): return true
        case (.done(let a), .done(let b)): return a == b
        case (.failed(let a), .failed(let b)): return a == b
        default: return false
        }
    }
}

private struct ShareableFile: Identifiable {
    let id = UUID()
    let url: URL
}

#Preview {
    MediaView()
        .environment(WaveCamClient(mode: .mock))
        .preferredColorScheme(.dark)
}
