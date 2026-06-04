import SwiftUI
import UniformTypeIdentifiers

/// Browse recordings stored on the Orin, download one to the phone, and share it.
/// Read-only — no camera control, no recording start/stop.
struct MediaView: View {
    @Environment(WaveCamClient.self) private var client

    @State private var files: [WCMediaFile] = []
    @State private var loadState: MediaLoadState = .idle
    @State private var downloadProgress: [String: DownloadState] = [:]
    @State private var shareItem: ShareableFile?

    var body: some View {
        VStack(spacing: 0) {
            mediaHeader
            Divider().background(WC.line)
            contentBody
        }
        .background(WC.bg.ignoresSafeArea())
        .task { await load() }
        .sheet(item: $shareItem) { item in
            ShareSheet(url: item.url)
                .presentationDetents([.medium, .large])
        }
    }

    // MARK: - Header

    private var mediaHeader: some View {
        GlassCard(cornerRadius: 0, padding: 0) {
            HStack(spacing: WCSpace.md) {
                VStack(alignment: .leading, spacing: WCSpace.xs) {
                    Text("RECORDINGS")
                        .font(WCFont.bodyBold)
                        .tracking(1.5)
                        .foregroundStyle(WC.muted)
                    if let freeGb = client.status?.media?.freeGb {
                        Text(String(format: "%.1f GB free", freeGb))
                            .font(WCFont.captionMono)
                            .foregroundStyle(WC.faint)
                    }
                }
                Spacer()
                if case .loading = loadState {
                    ProgressView().tint(WC.accent).scaleEffect(0.8)
                        .frame(width: 44, height: 44)
                } else {
                    GlassIconButton(
                        systemImage: "arrow.clockwise",
                        state: .normal,
                        action: { Task { await load() } }
                    )
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
                fileList
            }
        }
    }

    private var loadingPlaceholder: some View {
        VStack(spacing: 16) {
            ProgressView().tint(WC.ok)
            Text("LOADING RECORDINGS")
                .font(.system(size: 11, weight: .semibold))
                .tracking(1.5)
                .foregroundStyle(WC.muted)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - File list

    private var fileList: some View {
        ScrollView {
            GlassSurface(cornerRadius: WCRadius.md) {
                LazyVStack(spacing: 0) {
                    ForEach(files) { file in
                        MediaFileRow(
                            file: file,
                            downloadState: downloadProgress[file.name] ?? .idle,
                            onDownload: { Task { await download(file) } },
                            onShare: { localURL in shareItem = ShareableFile(url: localURL) }
                        )
                        Divider()
                            .background(WC.line)
                            .padding(.leading, WCSpace.lg)
                    }
                }
            }
            .padding(.horizontal, WCSpace.lg)
            .padding(.vertical, WCSpace.md)
            .padding(.bottom, WCSpace.xl)
        }
        .scrollIndicators(.hidden)
        .background(WC.bg)
    }

    // MARK: - Actions

    private func load() async {
        guard loadState != .loading else { return }
        loadState = .loading
        guard client.mode == .live else {
            loadState = .mockMode
            return
        }
        do {
            let fetched = try await client.mediaList()
            files = fetched
            loadState = .loaded
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
}

// MARK: - File row

private struct MediaFileRow: View {
    let file: WCMediaFile
    let downloadState: DownloadState
    let onDownload: () -> Void
    let onShare: (URL) -> Void

    private var sizeLabel: String {
        let mb = Double(file.sizeBytes) / 1_000_000
        return mb >= 1000
            ? String(format: "%.1f GB", mb / 1000)
            : String(format: "%.0f MB", mb)
    }

    private var dateLabel: String {
        file.createdAt.formatted(date: .abbreviated, time: .shortened)
    }

    var body: some View {
        HStack(spacing: WCSpace.md) {
            Image(systemName: "film")
                .font(.system(size: 16, weight: .medium))
                .foregroundStyle(WC.accent)
                .frame(width: 28)

            VStack(alignment: .leading, spacing: WCSpace.xs) {
                Text(file.name)
                    .font(WCFont.mono)
                    .foregroundStyle(WC.txt)
                    .lineLimit(1)
                    .truncationMode(.middle)
                HStack(spacing: WCSpace.sm) {
                    Text(sizeLabel)
                        .font(WCFont.captionMono)
                        .foregroundStyle(WC.faint)
                    Text(dateLabel)
                        .font(WCFont.caption)
                        .foregroundStyle(WC.faint)
                }
            }

            Spacer(minLength: WCSpace.xs)

            rowActions
        }
        .padding(.horizontal, WCSpace.lg)
        .padding(.vertical, WCSpace.md)
        .contentShape(Rectangle())
    }

    @ViewBuilder
    private var rowActions: some View {
        switch downloadState {
        case .idle, .failed:
            Button {
                onDownload()
            } label: {
                Label("Download", systemImage: "arrow.down.circle")
                    .labelStyle(.iconOnly)
                    .font(.system(size: 20))
                    .frame(width: 44, height: 44)
            }
            .buttonStyle(.plain)
            .foregroundStyle(downloadState == .idle ? WC.ok : WC.warn)
            .accessibilityLabel("Download \(file.name)")
            .accessibilityHint(downloadState == .idle ? "" : "Previous download failed — tap to retry")

        case .downloading:
            ProgressView()
                .tint(WC.ok)
                .frame(width: 44, height: 44)

        case .done(let localURL):
            Button {
                onShare(localURL)
            } label: {
                Label("Share", systemImage: "square.and.arrow.up")
                    .labelStyle(.iconOnly)
                    .font(.system(size: 20))
                    .frame(width: 44, height: 44)
            }
            .buttonStyle(.plain)
            .foregroundStyle(WC.accent)
            .accessibilityLabel("Share \(file.name)")
        }
    }
}

// MARK: - Empty / offline / unavailable states

private struct MediaEmptyView: View {
    let onRefresh: () -> Void

    var body: some View {
        MediaStateShell(
            icon: "tray",
            iconColor: WC.muted,
            title: "NO RECORDINGS",
            detail: "Start a recording session from the Live tab. Files appear here once saved.",
            actionLabel: "Refresh",
            onAction: onRefresh
        )
    }
}

private struct MediaOfflineView: View {
    let onRetry: () -> Void

    var body: some View {
        MediaStateShell(
            icon: "network.slash",
            iconColor: WC.warn,
            title: "OFFLINE",
            detail: "Cannot reach the Orin. Connect via USB tether or Wi-Fi, then retry.",
            actionLabel: "Retry",
            onAction: onRetry
        )
    }
}

private struct MediaUnavailableView: View {
    var body: some View {
        MediaStateShell(
            icon: "arrow.up.square",
            iconColor: WC.warn,
            title: "UPDATE REQUIRED",
            detail: "Media listing is not available on this Orin firmware. Ask Codex to deploy a newer build.",
            actionLabel: nil,
            onAction: nil
        )
    }
}

private struct MediaMockView: View {
    var body: some View {
        MediaStateShell(
            icon: "film",
            iconColor: WC.faint,
            title: "MOCK MODE",
            detail: "Media browsing requires a live connection. Switch to Live mode in the Connect tab.",
            actionLabel: nil,
            onAction: nil
        )
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
            Image(systemName: icon)
                .font(.system(size: 32, weight: .bold))
                .foregroundStyle(iconColor)
            Text(title)
                .font(WCFont.heading)
                .tracking(1.5)
                .foregroundStyle(WC.txt)
            Text(detail)
                .font(WCFont.body)
                .foregroundStyle(WC.muted)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 280)
                .fixedSize(horizontal: false, vertical: true)
            if let label = actionLabel, let action = onAction {
                GlassButton(label: label, role: .normal, action: action)
                    .frame(maxWidth: 200)
            }
        }
        .padding(WCSpace.xl)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

// MARK: - Share sheet (UIActivityViewController bridge)

/// Wraps UIActivityViewController so we can present it from SwiftUI.
private struct ShareSheet: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [url], applicationActivities: nil)
    }

    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}

// MARK: - Supporting types

private enum MediaLoadState: Equatable {
    case idle
    case loading
    case loaded
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

/// Stable identity wrapper so `.sheet(item:)` can present the share sheet.
private struct ShareableFile: Identifiable {
    let id = UUID()
    let url: URL
}

#Preview {
    MediaView()
        .environment(WaveCamClient(mode: .mock))
        .preferredColorScheme(.dark)
}
