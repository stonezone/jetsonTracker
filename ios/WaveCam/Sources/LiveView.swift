import SwiftUI

/// Live/Monitor screen: operator preview, tracking HUD, and emergency stop.
struct LiveView: View {
    @Environment(WaveCamClient.self) private var client

    var body: some View {
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
        .background(WC.bg.ignoresSafeArea())
        .scrollIndicators(.hidden)
        .task { await client.refresh() }
    }
}

private struct LiveFeedCard: View {
    let status: WCStatus?
    let previewURL: URL?

    private var isLocked: Bool { status?.tracking.locked ?? true }
    private var isRecording: Bool { status?.media?.recording ?? true }

    var body: some View {
        ZStack {
            FeedBackground(previewURL: previewURL)
            FeedSubjectOverlay(isLocked: isLocked, confidence: status?.tracking.confidence)
            FeedReticles()
            FeedTopTags(isLocked: isLocked, isRecording: isRecording)
            FeedBottomStrip(status: status)
        }
        .frame(height: 430)
        .clipShape(.rect(cornerRadius: 20))
        .overlay(RoundedRectangle(cornerRadius: 20).stroke(Color.white.opacity(0.14)))
        .shadow(color: .black.opacity(0.32), radius: 24, y: 14)
    }
}

private struct FeedBackground: View {
    let previewURL: URL?

    var body: some View {
        ZStack {
            if let previewURL {
                AsyncImage(url: previewURL) { phase in
                    switch phase {
                    case .success(let image):
                        image.resizable().scaledToFill()
                    case .failure:
                        MockOceanScene(showOfflinePattern: true)
                    case .empty:
                        MockOceanScene(showOfflinePattern: false)
                            .overlay(ProgressView().tint(WC.ok))
                    @unknown default:
                        MockOceanScene(showOfflinePattern: false)
                    }
                }
            } else {
                MockOceanScene(showOfflinePattern: false)
            }
        }
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
    let status: WCStatus?

    var body: some View {
        HStack(spacing: 8) {
            StatusPill(title: "OWNER", value: status?.ptz.owner.uppercased() ?? "VISION", color: WC.brand)
            StatusPill(title: "MODE", value: status?.session.mode?.uppercased() ?? "VISION_GPS", color: WC.ok)
            StatusPill(title: "PTZ", value: ptzState, color: status?.ptz.enabled == false ? WC.warn : WC.ok)
        }
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

    var body: some View {
        Button {
            Task { await client.kill() }
        } label: {
            HStack(spacing: 12) {
                RoundedRectangle(cornerRadius: 3)
                    .fill(.white)
                    .frame(width: 13, height: 13)
                Text("Emergency Stop")
                    .font(.system(size: 16, weight: .bold))
                    .tracking(3)
                    .textCase(.uppercase)
            }
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 16)
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
