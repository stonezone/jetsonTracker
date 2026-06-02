import SwiftUI
import WebKit

/// Dashboard screen: native chrome around the Orin dashboard WebView.
struct DashView: View {
    @Environment(WaveCamClient.self) private var client

    @State private var loadID = UUID()
    @State private var isLoading = true
    @State private var loadFailed = false

    private var dashboardURL: URL {
        client.baseURL.dashboardURL
    }

    var body: some View {
        VStack(spacing: 0) {
            DashboardChrome(
                url: dashboardURL,
                isLoading: isLoading,
                loadFailed: loadFailed,
                onReload: reload
            )
            ZStack {
                DashboardWebView(
                    url: dashboardURL,
                    reloadID: loadID,
                    isLoading: $isLoading,
                    loadFailed: $loadFailed
                )
                .background(WC.ink)

                if loadFailed {
                    DashboardFallback(url: dashboardURL, onReload: reload)
                } else if isLoading {
                    DashboardLoadingOverlay()
                }
            }
            .clipShape(.rect(cornerRadius: 0))
        }
        .background(WC.bg.ignoresSafeArea())
    }

    private func reload() {
        loadFailed = false
        isLoading = true
        loadID = UUID()
    }
}

private struct DashboardChrome: View {
    let url: URL
    let isLoading: Bool
    let loadFailed: Bool
    let onReload: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            HStack(spacing: 5) {
                Circle().fill(Color(hex: 0x344651)).frame(width: 8, height: 8)
                Circle().fill(Color(hex: 0x344651)).frame(width: 8, height: 8)
                Circle().fill(Color(hex: 0x344651)).frame(width: 8, height: 8)
            }

            Text(url.absoluteString)
                .font(.system(size: 11, weight: .medium, design: .monospaced))
                .foregroundStyle(loadFailed ? WC.warn : WC.muted)
                .lineLimit(1)
                .truncationMode(.middle)
                .padding(.horizontal, 10)
                .padding(.vertical, 7)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(WC.bg, in: .rect(cornerRadius: 9))
                .overlay(RoundedRectangle(cornerRadius: 9).stroke(WC.line))

            Button {
                onReload()
            } label: {
                Image(systemName: isLoading ? "hourglass" : "arrow.clockwise")
                    .font(.system(size: 13, weight: .bold))
                    .frame(width: 44, height: 44)
            }
            .buttonStyle(.plain)
            .foregroundStyle(loadFailed ? WC.warn : WC.ok)
            .background(WC.panel2, in: .rect(cornerRadius: 11))
            .overlay(RoundedRectangle(cornerRadius: 11).stroke(WC.line))
            .accessibilityLabel("Reload dashboard")
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(WC.ink)
        .overlay(Rectangle().fill(WC.line).frame(height: 1), alignment: .bottom)
    }
}

private struct DashboardWebView: UIViewRepresentable {
    let url: URL
    let reloadID: UUID

    @Binding var isLoading: Bool
    @Binding var loadFailed: Bool

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.allowsInlineMediaPlayback = true

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.scrollView.backgroundColor = UIColor(WC.deep)
        webView.backgroundColor = UIColor(WC.deep)
        webView.isOpaque = false
        webView.allowsBackForwardNavigationGestures = false
        webView.load(URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 8))
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        guard context.coordinator.reloadID != reloadID || context.coordinator.url != url else { return }
        context.coordinator.reloadID = reloadID
        context.coordinator.url = url
        webView.load(URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 8))
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(isLoading: $isLoading, loadFailed: $loadFailed, reloadID: reloadID, url: url)
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        @Binding private var isLoading: Bool
        @Binding private var loadFailed: Bool
        var reloadID: UUID
        var url: URL

        init(isLoading: Binding<Bool>, loadFailed: Binding<Bool>, reloadID: UUID, url: URL) {
            _isLoading = isLoading
            _loadFailed = loadFailed
            self.reloadID = reloadID
            self.url = url
        }

        func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
            isLoading = true
            loadFailed = false
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            isLoading = false
            loadFailed = false
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            isLoading = false
            loadFailed = true
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            isLoading = false
            loadFailed = true
        }
    }
}

private extension URL {
    var dashboardURL: URL {
        guard var components = URLComponents(url: self, resolvingAgainstBaseURL: false) else {
            return self
        }
        components.port = 8088
        components.path = ""
        components.query = nil
        components.fragment = nil
        return components.url ?? self
    }
}

private struct DashboardLoadingOverlay: View {
    var body: some View {
        VStack(spacing: 14) {
            ProgressView()
                .tint(WC.ok)
            Text("CONNECTING TO ORIN DASHBOARD")
                .font(.system(size: 11, weight: .semibold))
                .tracking(1.5)
                .foregroundStyle(WC.muted)
        }
        .padding(18)
        .background(Color.black.opacity(0.55), in: .rect(cornerRadius: 16))
    }
}

private struct DashboardFallback: View {
    let url: URL
    let onReload: () -> Void

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                VStack(spacing: 8) {
                    Image(systemName: "network.slash")
                        .font(.system(size: 28, weight: .bold))
                        .foregroundStyle(WC.warn)
                    Text("DASHBOARD UNREACHABLE")
                        .font(.system(size: 18, weight: .black))
                        .tracking(1.5)
                        .foregroundStyle(WC.warn)
                    Text(url.absoluteString)
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(WC.muted)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                .frame(maxWidth: .infinity)
                .padding(16)
                .background(WC.panel, in: .rect(cornerRadius: 18))
                .overlay(RoundedRectangle(cornerRadius: 18).stroke(WC.line))

                DashboardMiniGrid()

                Button {
                    onReload()
                } label: {
                    Label("Retry Dashboard", systemImage: "arrow.clockwise")
                        .font(.system(size: 14, weight: .bold))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                }
                .buttonStyle(.plain)
                .foregroundStyle(Color.black)
                .background(WC.ok, in: .rect(cornerRadius: 14))
            }
            .padding(16)
            .padding(.bottom, 96)
        }
        .background(WC.bg)
    }
}

private struct DashboardMiniGrid: View {
    var body: some View {
        VStack(spacing: 11) {
            HStack(spacing: 11) {
                DashboardMiniPanel(label: "APP SHELL", value: "READY", tint: WC.ok)
                DashboardMiniPanel(label: "WEB DASH", value: "OFFLINE", tint: WC.warn)
            }
            HStack(spacing: 11) {
                DashboardMiniPanel(label: "LOCAL API", value: "CHECK", tint: WC.txt)
                DashboardMiniPanel(label: "CONTROL TABS", value: "READY", tint: WC.ok)
            }
            DashboardRetryHint()
        }
    }
}

private struct DashboardMiniPanel: View {
    let label: String
    let value: String
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(.system(size: 9, weight: .semibold))
                .tracking(1.3)
                .foregroundStyle(WC.faint)
            Text(value)
                .font(.system(size: 17, weight: .semibold, design: .monospaced))
                .foregroundStyle(tint)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(WC.panel, in: .rect(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(WC.line))
    }
}

private struct DashboardRetryHint: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("CONNECTION CHECK")
                .font(.system(size: 9, weight: .semibold))
                .tracking(1.3)
                .foregroundStyle(WC.faint)
            Text("Dashboard loads when the phone can reach the Orin web UI on the local network.")
                .font(.system(size: 12))
                .foregroundStyle(WC.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(12)
        .background(WC.panel, in: .rect(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(WC.line))
    }
}
