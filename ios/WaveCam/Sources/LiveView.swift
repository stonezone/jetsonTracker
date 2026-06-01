import SwiftUI
import WebKit

/// Live/Monitor screen: operator preview, tracking HUD, and emergency stop.
struct LiveView: View {
    @Environment(WaveCamClient.self) private var client
    @Environment(\.verticalSizeClass) private var verticalSizeClass

    private var isLandscapeControl: Bool {
        verticalSizeClass == .compact
    }

    var body: some View {
        Group {
            if isLandscapeControl {
                HStack(alignment: .top, spacing: 12) {
                    LiveFeedCard(status: client.status, previewURL: client.previewURL, height: nil)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                    VStack(spacing: 10) {
                        LiveTelemetryGrid(status: client.status, axis: .vertical)
                        EmergencyStopButton(compact: true)
                    }
                    .frame(width: 190)
                }
                .padding(.horizontal, 14)
                .padding(.top, 8)
                .padding(.bottom, 12)
            } else {
                ScrollView {
                    VStack(spacing: 12) {
                        LiveFeedCard(status: client.status, previewURL: client.previewURL)
                        LiveTelemetryGrid(status: client.status)
                        EmergencyStopButton()
                    }
                    .padding(.horizontal, 16)
                    .padding(.top, 6)
                    .padding(.bottom, 22)
                }
                .scrollIndicators(.hidden)
            }
        }
        .background(WC.bg.ignoresSafeArea())
        .task { await client.refresh() }
    }
}

private struct LiveFeedCard: View {
    let status: WCStatus?
    let previewURL: URL?
    var height: CGFloat? = 430

    private var isLocked: Bool { status?.tracking.locked ?? true }
    private var isRecording: Bool { status?.media?.recording ?? true }

    var body: some View {
        content
            .clipShape(.rect(cornerRadius: 20))
            .overlay(RoundedRectangle(cornerRadius: 20).stroke(Color.white.opacity(0.14)))
            .shadow(color: .black.opacity(0.32), radius: 24, y: 14)
    }

    private var feed: some View {
        ZStack {
            FeedBackground(previewURL: previewURL)
            if previewURL == nil {
                FeedSubjectOverlay(isLocked: isLocked, confidence: status?.tracking.confidence)
            }
            FeedReticles()
            FeedAimReticle(status: status)
            FeedPTZOverlay(status: status)
            FeedTopTags(isLocked: isLocked, isRecording: isRecording)
            FeedBottomStrip(status: status)
        }
    }

    @ViewBuilder
    private var content: some View {
        if let height {
            feed.frame(height: height)
        } else {
            feed.aspectRatio(16 / 9, contentMode: .fit)
        }
    }
}

private struct FeedBackground: View {
    let previewURL: URL?

    var body: some View {
        ZStack {
            if let previewURL {
                MJPEGPreviewView(url: previewURL)
            } else {
                MockOceanScene(showOfflinePattern: false)
            }
        }
    }
}

private struct MJPEGPreviewView: UIViewRepresentable {
    let url: URL

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.allowsInlineMediaPlayback = true

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.isOpaque = false
        webView.backgroundColor = UIColor(Color(hex: 0x0B1218))
        webView.scrollView.backgroundColor = UIColor(Color(hex: 0x0B1218))
        webView.scrollView.isScrollEnabled = false
        webView.loadHTMLString(html(for: url), baseURL: nil)
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        guard context.coordinator.loadedURL != url else { return }
        context.coordinator.loadedURL = url
        webView.loadHTMLString(html(for: url), baseURL: nil)
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(loadedURL: url)
    }

    final class Coordinator {
        var loadedURL: URL

        init(loadedURL: URL) {
            self.loadedURL = loadedURL
        }
    }

    private func html(for url: URL) -> String {
        """
        <!doctype html>
        <html>
        <head>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
        html,body{margin:0;width:100%;height:100%;background:#0b1218;overflow:hidden}
        img{width:100vw;height:100vh;object-fit:cover;display:block}
        </style>
        </head>
        <body><img src="\(url.absoluteString)" alt=""></body>
        </html>
        """
    }
}

private struct MockOceanScene: View {
    let showOfflinePattern: Bool

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [Color(hex: 0x16344A), Color(hex: 0x1B4A5A), Color(hex: 0x0F3A44), Color(hex: 0x0A2630)],
                startPoint: .top,
                endPoint: .bottom
            )
            RadialGradient(
                colors: [Color(hex: 0xFFAA5A, alpha: 0.28), .clear],
                center: .topTrailing,
                startRadius: 20,
                endRadius: 260
            )
            .blendMode(.screen)
            VStack(spacing: 0) {
                Spacer().frame(height: 142)
                Rectangle()
                    .fill(
                        LinearGradient(
                            colors: [.clear, Color(hex: 0xFFD2A0, alpha: 0.55), .clear],
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                    )
                    .frame(height: 1)
                Spacer()
            }
            WaveBands()
            if showOfflinePattern {
                Image(systemName: "wifi.slash")
                    .font(.system(size: 24, weight: .semibold))
                    .foregroundStyle(WC.muted)
                    .padding(14)
                    .background(Color.black.opacity(0.45), in: .rect(cornerRadius: 16))
            }
        }
    }
}

private struct WaveBands: View {
    var body: some View {
        VStack(spacing: 13) {
            Spacer()
            ForEach(0..<8, id: \.self) { i in
                Capsule()
                    .fill(Color.white.opacity(i.isMultiple(of: 2) ? 0.14 : 0.07))
                    .frame(width: CGFloat(170 + i * 34), height: 2)
                    .offset(x: CGFloat((i % 3) * 18 - 18))
            }
        }
        .padding(.bottom, 54)
        .opacity(0.58)
    }
}

private struct FeedSubjectOverlay: View {
    let isLocked: Bool
    let confidence: Double?

    var body: some View {
        ZStack {
            SurferGlyph()
                .position(x: 222, y: 246)
            if isLocked {
                LockBox(confidence: confidence)
                    .position(x: 222, y: 224)
            }
        }
    }
}

private struct SurferGlyph: View {
    var body: some View {
        ZStack {
            Capsule()
                .fill(Color.white.opacity(0.92))
                .frame(width: 34, height: 5)
                .offset(y: 29)
            Rectangle()
                .fill(Color.white.opacity(0.7))
                .frame(width: 2, height: 12)
                .offset(y: 35)
            Circle()
                .fill(Color(hex: 0x10222B))
                .frame(width: 8, height: 8)
                .offset(y: -20)
            RoundedRectangle(cornerRadius: 7)
                .fill(WC.brand)
                .frame(width: 14, height: 24)
                .shadow(color: WC.brand.opacity(0.7), radius: 9)
                .offset(y: -4)
            RoundedRectangle(cornerRadius: 4)
                .fill(Color(hex: 0x10222B))
                .frame(width: 6, height: 16)
                .offset(y: 17)
            Circle()
                .fill(Color.white.opacity(0.34))
                .frame(width: 20, height: 11)
                .blur(radius: 1)
                .offset(x: -15, y: 25)
        }
        .frame(width: 48, height: 70)
    }
}

private struct LockBox: View {
    let confidence: Double?

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 6)
                .stroke(WC.brand, lineWidth: 2)
                .frame(width: 76, height: 92)
                .shadow(color: WC.brand.opacity(0.45), radius: 14)
            Circle()
                .stroke(WC.brand, lineWidth: 1)
                .frame(width: 11, height: 11)
            HStack(spacing: 4) {
                Text("CONF")
                Text(confidence ?? 0.91, format: .number.precision(.fractionLength(2)))
            }
            .font(.system(size: 9, design: .monospaced))
            .foregroundStyle(WC.brand)
            .offset(x: -12, y: -57)
        }
    }
}

private struct FeedReticles: View {
    var body: some View {
        ZStack {
            ReticleCorner(horizontal: .leading, vertical: .top)
            ReticleCorner(horizontal: .trailing, vertical: .top)
            ReticleCorner(horizontal: .leading, vertical: .bottom)
            ReticleCorner(horizontal: .trailing, vertical: .bottom)
        }
        .padding(12)
    }
}

private struct ReticleCorner: View {
    enum Horizontal { case leading, trailing }
    enum Vertical { case top, bottom }

    let horizontal: Horizontal
    let vertical: Vertical

    var body: some View {
        VStack {
            if vertical == .bottom { Spacer() }
            HStack {
                if horizontal == .trailing { Spacer() }
                Path { path in
                    if horizontal == .leading {
                        path.move(to: CGPoint(x: 22, y: 0))
                        path.addLine(to: CGPoint(x: 0, y: 0))
                        path.addLine(to: CGPoint(x: 0, y: 22))
                    } else {
                        path.move(to: CGPoint(x: 0, y: 0))
                        path.addLine(to: CGPoint(x: 22, y: 0))
                        path.addLine(to: CGPoint(x: 22, y: 22))
                    }
                }
                .stroke(Color.white.opacity(0.34), lineWidth: 2)
                .frame(width: 22, height: 22)
                if horizontal == .leading { Spacer() }
            }
            if vertical == .top { Spacer() }
        }
    }
}

private struct FeedAimReticle: View {
    let status: WCStatus?

    private var isMoving: Bool {
        guard status?.ptz.enabled != false else { return false }
        guard status?.safety.killed != true else { return false }
        guard status?.ptz.owner != "idle" else { return false }
        return status?.ptz.panTiltCmd?.lowercased() != "stop"
    }

    private var color: Color {
        isMoving ? WC.brand : Color.white.opacity(0.55)
    }

    var body: some View {
        ZStack {
            Circle()
                .stroke(color.opacity(0.72), lineWidth: 1.5)
                .frame(width: 38, height: 38)
            Rectangle()
                .fill(color.opacity(0.76))
                .frame(width: 1.5, height: 54)
            Rectangle()
                .fill(color.opacity(0.76))
                .frame(width: 54, height: 1.5)
            Circle()
                .fill(color)
                .frame(width: 5, height: 5)
            Text("AIM")
                .font(.system(size: 8, weight: .semibold, design: .monospaced))
                .tracking(0.8)
                .foregroundStyle(color)
                .offset(y: 32)
        }
        .shadow(color: .black.opacity(0.35), radius: 4)
    }
}

private struct FeedPTZOverlay: View {
    let status: WCStatus?

    private var ptzEnabled: Bool {
        status?.ptz.enabled != false
    }

    private var killed: Bool {
        status?.safety.killed == true
    }

    private var owner: String {
        status?.ptz.owner.uppercased() ?? "IDLE"
    }

    private var command: String {
        status?.ptz.panTiltCmd?.uppercased() ?? "STOP"
    }

    private var zoom: String {
        status?.ptz.zoomState?.uppercased() ?? "HOLD"
    }

    private var stateText: String {
        if killed { return "KILLED" }
        if !ptzEnabled { return "OFF" }
        if owner == "IDLE" { return "IDLE" }
        if command == "STOP" { return "HELD" }
        return "MOVING"
    }

    private var stateColor: Color {
        switch stateText {
        case "MOVING": WC.brand
        case "KILLED": WC.kill
        case "OFF", "IDLE": WC.warn
        default: WC.ok
        }
    }

    private var motionLevel: Double {
        guard stateText == "MOVING" else { return 0 }
        return min(1, Double(commandSpeed("P") + commandSpeed("T")) / 22.0)
    }

    var body: some View {
        VStack {
            HStack {
                Spacer()
                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 6) {
                        Circle().fill(stateColor).frame(width: 7, height: 7)
                        Text("PTZ \(stateText)")
                            .font(.system(size: 10, weight: .semibold, design: .monospaced))
                            .tracking(0.7)
                            .foregroundStyle(stateColor)
                    }
                    HStack(spacing: 6) {
                        PTZOverlayMetric(label: "OWNER", value: owner, color: WC.txt)
                        PTZOverlayMetric(label: "CMD", value: command, color: stateColor)
                        PTZOverlayMetric(label: "ZOOM", value: zoom, color: zoom == "HOLD" ? WC.muted : WC.brand)
                    }
                }
                .padding(9)
                .background(Color.black.opacity(0.64), in: .rect(cornerRadius: 11))
                .overlay(RoundedRectangle(cornerRadius: 11).stroke(stateColor.opacity(0.4)))
            }
            .padding(.top, 48)
            .padding(.horizontal, 12)

            Spacer()

            HStack {
                Spacer()
                PTZMotionScope(level: motionLevel, color: stateColor)
            }
            .padding(.trailing, 12)
            .padding(.bottom, 74)
        }
    }

    private func commandSpeed(_ prefix: Character) -> Int {
        let segments = command.split(separator: "/")
        guard let segment = segments.first(where: { $0.first == prefix }) else { return 0 }
        return Int(segment.dropFirst()) ?? 0
    }
}

private struct PTZOverlayMetric: View {
    let label: String
    let value: String
    let color: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.system(size: 7, weight: .semibold))
                .tracking(0.9)
                .foregroundStyle(WC.faint)
            Text(value)
                .font(.system(size: 10.5, weight: .semibold, design: .monospaced))
                .lineLimit(1)
                .minimumScaleFactor(0.58)
                .foregroundStyle(color)
        }
        .frame(width: 43, alignment: .leading)
    }
}

private struct PTZMotionScope: View {
    let level: Double
    let color: Color

    var body: some View {
        ZStack {
            Circle()
                .fill(Color.black.opacity(0.54))
            Circle()
                .stroke(Color.white.opacity(0.14), lineWidth: 1)
                .padding(7)
            Circle()
                .trim(from: 0, to: max(0.08, level))
                .stroke(color, style: StrokeStyle(lineWidth: 3, lineCap: .round))
                .rotationEffect(.degrees(-90))
                .padding(7)
                .opacity(level == 0 ? 0.35 : 1)
            Image(systemName: "viewfinder")
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(color)
            Text("LOOK")
                .font(.system(size: 7, weight: .semibold, design: .monospaced))
                .tracking(0.8)
                .foregroundStyle(WC.muted)
                .offset(y: 20)
        }
        .frame(width: 62, height: 62)
        .shadow(color: .black.opacity(0.34), radius: 8, y: 4)
    }
}

private struct FeedTopTags: View {
    let isLocked: Bool
    let isRecording: Bool

    var body: some View {
        VStack {
            HStack(spacing: 8) {
                LiveTag(text: isLocked ? "LOCKED" : "SEARCH", color: isLocked ? WC.brand : WC.warn, dot: isLocked)
                if isRecording {
                    LiveTag(text: "REC", color: WC.kill, dot: true)
                }
            }
            .padding(.top, 12)
            Spacer()
        }
    }
}

private struct LiveTag: View {
    let text: String
    let color: Color
    let dot: Bool

    var body: some View {
        HStack(spacing: 5) {
            if dot {
                Circle().fill(color).frame(width: 7, height: 7)
            }
            Text(text)
                .font(.system(size: 10, design: .monospaced))
                .tracking(0.6)
        }
        .foregroundStyle(color)
        .padding(.horizontal, 8)
        .padding(.vertical, 5)
        .background(Color.black.opacity(0.58), in: .rect(cornerRadius: 7))
        .overlay(RoundedRectangle(cornerRadius: 7).stroke(color.opacity(0.48)))
    }
}

private struct FeedBottomStrip: View {
    let status: WCStatus?

    var body: some View {
        VStack {
            Spacer()
            HStack(spacing: 6) {
                FeedMetric(label: "STATE", value: status?.session.state ?? "TRACKING", color: WC.ok)
                FeedMetric(label: "CONF", value: confidenceText, color: WC.brand)
                FeedMetric(label: "FPS", value: fpsText, color: WC.txt)
                FeedMetric(label: "GPS", value: distanceText, color: WC.ok)
            }
            .padding(10)
        }
    }

    private var confidenceText: String {
        guard let value = status?.tracking.confidence else { return "0.91" }
        return value.formatted(.number.precision(.fractionLength(2)))
    }

    private var fpsText: String {
        guard let value = status?.tracking.fps else { return "26.0" }
        return value.formatted(.number.precision(.fractionLength(1)))
    }

    private var distanceText: String {
        guard let meters = status?.gps?.distanceM else { return "148m" }
        return "\(Int(meters.rounded()))m"
    }
}

private struct FeedMetric: View {
    let label: String
    let value: String
    let color: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.system(size: 8.5, weight: .semibold))
                .tracking(1.2)
                .foregroundStyle(WC.faint)
            Text(value)
                .font(.system(size: 13, design: .monospaced))
                .lineLimit(1)
                .minimumScaleFactor(0.7)
                .foregroundStyle(color)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 7)
        .padding(.vertical, 7)
        .background(Color.black.opacity(0.62), in: .rect(cornerRadius: 9))
        .overlay(RoundedRectangle(cornerRadius: 9).stroke(WC.line))
    }
}

private struct LiveTelemetryGrid: View {
    enum Axis {
        case horizontal
        case vertical
    }

    let status: WCStatus?
    var axis: Axis = .horizontal

    var body: some View {
        Group {
            switch axis {
            case .horizontal:
                HStack(spacing: 8) { telemetryPills }
            case .vertical:
                VStack(spacing: 8) { telemetryPills }
            }
        }
    }

    @ViewBuilder
    private var telemetryPills: some View {
        StatusPill(title: "OWNER", value: status?.ptz.owner.uppercased() ?? "VISION", color: WC.brand)
        StatusPill(title: "MODE", value: status?.session.mode?.uppercased() ?? "VISION_GPS", color: WC.ok)
        StatusPill(title: "PTZ", value: ptzState, color: status?.ptz.enabled == false ? WC.warn : WC.ok)
    }

    private var ptzState: String {
        if status?.ptz.enabled == false { return "OFF" }
        return status?.ptz.panTiltCmd?.uppercased() ?? "P4/T0"
    }
}

private struct StatusPill: View {
    let title: String
    let value: String
    let color: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.system(size: 9, weight: .semibold))
                .tracking(1.4)
                .foregroundStyle(WC.faint)
            Text(value)
                .font(.system(size: 12, weight: .semibold, design: .monospaced))
                .lineLimit(1)
                .minimumScaleFactor(0.58)
                .foregroundStyle(color)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(WC.panel, in: .rect(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(WC.line))
    }
}

private struct EmergencyStopButton: View {
    @Environment(WaveCamClient.self) private var client
    var compact = false

    var body: some View {
        Button {
            Task { await client.kill() }
        } label: {
            HStack(spacing: 12) {
                RoundedRectangle(cornerRadius: 3)
                    .fill(.white)
                    .frame(width: 13, height: 13)
                Text("Emergency Stop")
                    .font(.system(size: compact ? 13 : 16, weight: .bold))
                    .tracking(compact ? 2 : 3)
                    .textCase(.uppercase)
            }
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity)
            .padding(.vertical, compact ? 13 : 16)
            .background(
                LinearGradient(colors: [Color(hex: 0xFF5247), Color(hex: 0xE22B20)],
                               startPoint: .top,
                               endPoint: .bottom),
                in: .rect(cornerRadius: 16)
            )
            .shadow(color: WC.kill.opacity(0.48), radius: 24, y: 10)
        }
        .buttonStyle(.plain)
    }
}

#Preview {
    LiveView()
        .environment(WaveCamClient(mode: .mock))
        .preferredColorScheme(.dark)
}
