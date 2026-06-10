import SwiftUI

/// Root shell: persistent top bar (brand + connection + KILL), a 5-tab TabView,
/// and the sticky KILL-latch overlay that covers everything when latched.
struct ContentView: View {
    @Environment(WaveCamClient.self) private var client
    @State private var tab = 0

    var body: some View {
        ZStack {
            WC.bg.ignoresSafeArea()
            VStack(spacing: 0) {
                TopBar(tab: tab)
                if client.isShowingMockData { MockDataBanner() }
                TabView(selection: $tab) {
                    MergedLiveView().tag(0).tabItem { Label("Live", systemImage: "viewfinder") }
                    CalibrateView().tag(1).tabItem { Label("Calibrate", systemImage: "scope") }
                    ToolsView().tag(2).tabItem { Label("Tools", systemImage: "wrench.and.screwdriver") }
                    ConnectionView().tag(3).tabItem { Label("Connect", systemImage: "network") }
                    MediaView().tag(4).tabItem { Label("Media", systemImage: "play.rectangle.on.rectangle") }
                }
                .tint(WC.brand)
            }
            if client.effectiveKilled {
                KillLatchOverlay()
            }
        }
        .task { await client.refresh() }
        .alert("Command not confirmed", isPresented: Binding(
            get: { client.lastCommandError != nil },
            set: { if !$0 { client.clearCommandError() } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(client.lastCommandError ?? "")
        }
    }
}

/// Always-visible across every tab: brand, connection state, and the KILL chip.
private struct TopBar: View {
    @Environment(WaveCamClient.self) private var client
    var tab: Int

    /// Maps the current tab to its anchor in the Orin-hosted operator guide.
    private var guideAnchor: String {
        switch tab {
        case 0: return "live"
        case 1: return "calibrate"
        case 2: return "tune"
        case 3: return "connect"
        case 4: return "media"
        default: return "overview"
        }
    }

    private var connectionText: String {
        if client.mode == .mock { return "MOCK" }
        return client.connected ? client.activeRoute.shortLabel : "OFFLINE"
    }

    private var connectionColor: Color {
        if client.mode == .mock { return WC.warn }
        return client.connected ? WC.ok : WC.faint
    }

    var body: some View {
        HStack {
            HStack(spacing: 8) {
                Circle().fill(WC.brand).frame(width: 9, height: 9)
                HStack(spacing: 0) {
                    Text("WAVE").foregroundStyle(WC.txt)
                    Text("CAM").foregroundStyle(WC.brand)
                }
                .font(.system(size: 16, weight: .bold))
                .tracking(1)
            }
            Spacer()
            HStack(spacing: 6) {
                Circle().fill(connectionColor).frame(width: 7, height: 7)
                Text(connectionText)
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(WC.muted)
            }
            .padding(.trailing, 4)
            GuideButton(anchor: guideAnchor)
            EmergencyStopButton(style: .chip)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(WC.ink)
    }
}

/// Opens the Orin-hosted operator guide at the section matching the current tab.
/// Derives the guide URL from the live API base (e.g. http://host:8088/api/v1 → /guide#anchor),
/// so it follows whichever route — tether or Wi-Fi — is currently active.
private struct GuideButton: View {
    @Environment(WaveCamClient.self) private var client
    @Environment(\.openURL) private var openURL
    var anchor: String

    private var guideURL: URL? {
        guard var comps = URLComponents(url: client.baseURL, resolvingAgainstBaseURL: false) else { return nil }
        comps.path = "/guide"
        comps.query = nil
        comps.fragment = anchor
        return comps.url
    }

    var body: some View {
        Button {
            if let url = guideURL { openURL(url) }
        } label: {
            Image(systemName: "book")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(WC.accent)
                .frame(width: 30, height: 30)
                .background(WC.accent.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(WC.accent.opacity(0.32)))
                .frame(width: 44, height: 44)   // 44pt hit target around the 30pt badge
                .contentShape(Rectangle())
                .accessibilityLabel("Operator guide")
        }
        .buttonStyle(.plain)
        .padding(.trailing, 6)
        .accessibilityLabel("Open guide for this screen")
    }
}

/// Loud, unmissable warning shown whenever the live API is down and the HUD is
/// substituting mock telemetry — so the operator can never mistake fake data for the
/// real rig (which could mean the camera is NOT recording/tracking). (review H2)
private struct MockDataBanner: View {
    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill")
            Text("OFFLINE — SHOWING MOCK DATA · real camera state unknown")
                .font(.system(size: 11, weight: .bold)).tracking(0.5)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .foregroundStyle(.black)
        .padding(.horizontal, 14).padding(.vertical, 7)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(WC.warn)
    }
}

/// Full-screen sticky latch shown whenever KILL is active. Bypasses everything.
struct KillLatchOverlay: View {
    @Environment(WaveCamClient.self) private var client
    var body: some View {
        ZStack {
            Color.black.opacity(0.88).ignoresSafeArea()
            VStack(spacing: 18) {
                ZStack {
                    RoundedRectangle(cornerRadius: 16).fill(WC.kill.opacity(0.16)).frame(width: 64, height: 64)
                    RoundedRectangle(cornerRadius: 16).stroke(WC.kill, lineWidth: 2).frame(width: 64, height: 64)
                    RoundedRectangle(cornerRadius: 5).fill(WC.kill).frame(width: 24, height: 24)
                }
                Text("STOP LATCHED")
                    .font(.system(size: 28, weight: .bold)).tracking(3).foregroundStyle(WC.kill)
                Text("Pan, tilt & zoom halted. Sticky — stays stopped until you resume.")
                    .font(.system(size: 13)).foregroundStyle(WC.txt.opacity(0.85))
                    .multilineTextAlignment(.center).frame(maxWidth: 250)
                HoldToResumeButton { Task { await client.resume() } }
            }
        }
        .overlay(Rectangle().stroke(WC.kill, lineWidth: 3).ignoresSafeArea())
    }
}

/// Resume from a KILL latch requires a deliberate ~1.2s hold, not a single tap,
/// so an accidental touch can't clear a safety stop. The bar fills as you hold.
private struct HoldToResumeButton: View {
    var action: () -> Void
    @State private var pressing = false
    private let holdDuration: Double = 1.2

    var body: some View {
        Text(pressing ? "KEEP HOLDING…" : "HOLD TO RESUME")
            .font(.system(size: 14, weight: .semibold)).tracking(2).foregroundStyle(WC.ok)
            .padding(.horizontal, 26).padding(.vertical, 13)
            .frame(minHeight: 44)
            .background(alignment: .leading) {
                GeometryReader { geo in
                    RoundedRectangle(cornerRadius: 13)
                        .fill(WC.ok.opacity(0.18))
                        .frame(width: pressing ? geo.size.width : 0)
                        .animation(.linear(duration: pressing ? holdDuration : 0.18), value: pressing)
                }
            }
            .overlay(RoundedRectangle(cornerRadius: 13).stroke(WC.ok))
            .clipShape(RoundedRectangle(cornerRadius: 13))
            .contentShape(Rectangle())
            .onLongPressGesture(minimumDuration: holdDuration) {
                action()
            } onPressingChanged: { isPressing in
                pressing = isPressing
            }
            .accessibilityAddTraits(.isButton)
            .accessibilityLabel("Hold to resume")
            .accessibilityHint("Press and hold to clear the emergency stop")
            // VoiceOver's activate (double-tap) can't fire onLongPressGesture, so expose
            // resume as an explicit action — otherwise a VoiceOver user cannot clear KILL.
            .accessibilityAction { action() }
    }
}

#Preview {
    ContentView()
        .environment(WaveCamClient(mode: .mock))
        .preferredColorScheme(.dark)
}
