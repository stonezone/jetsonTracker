import SwiftUI
import UIKit

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
                    LiveFeedCard(
                        status: client.status,
                        connected: client.connected,
                        previewURL: client.previewURL,
                        height: nil
                    )
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                    VStack(spacing: 10) {
                        ScrollView {
                            VStack(spacing: 10) {
                                LiveTelemetryGrid(status: client.status, connected: client.connected, axis: .vertical)
                                RecordButton(compact: true)
                            }
                        }
                        .scrollIndicators(.hidden)
                        EmergencyStopButton(style: .compact)
                    }
                    .frame(width: 190)
                }
                .padding(.horizontal, 14)
                .padding(.top, 8)
                .padding(.bottom, 12)
            } else {
                ScrollView {
                    VStack(spacing: 12) {
                        LiveFeedCard(
                            status: client.status,
                            connected: client.connected,
                            previewURL: client.previewURL
                        )
                        LiveTelemetryGrid(status: client.status, connected: client.connected)
                        RecordButton()
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
    let connected: Bool
    let previewURL: URL?
    var height: CGFloat? = 430

    private var isLocked: Bool { connected && status?.tracking.locked == true }
    private var isRecording: Bool { connected && status?.media?.recording == true }

    var body: some View {
        content
            .clipShape(.rect(cornerRadius: 20))
            .overlay(RoundedRectangle(cornerRadius: 20).stroke(Color.white.opacity(0.14)))
            .shadow(color: .black.opacity(0.32), radius: 24, y: 14)
    }

    private var feed: some View {
        ZStack {
            FeedBackground(previewURL: previewURL, connected: connected)
            if connected && previewURL == nil {
                FeedSubjectOverlay(isLocked: isLocked, confidence: status?.tracking.confidence)
            }
            FeedReticles()
            FeedAimReticle(status: status, connected: connected)
            FeedPTZOverlay(status: status, connected: connected)
            FeedTopTags(isLocked: isLocked, isRecording: isRecording, connected: connected)
            FeedLockReason(status: status, connected: connected)
            FeedBottomStrip(status: status, connected: connected)
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
    let connected: Bool

    var body: some View {
        ZStack {
            if let previewURL {
                MJPEGPreviewView(url: previewURL)
            } else {
                MockOceanScene(showOfflinePattern: !connected)
            }
        }
    }
}

struct MJPEGPreviewView: UIViewRepresentable {
    let url: URL

    func makeUIView(context: Context) -> UIImageView {
        let imageView = UIImageView()
        imageView.backgroundColor = UIColor(WC.deep)
        imageView.contentMode = .scaleAspectFill
        imageView.clipsToBounds = true
        context.coordinator.start(url: url, imageView: imageView)
        return imageView
    }

    func updateUIView(_ imageView: UIImageView, context: Context) {
        context.coordinator.start(url: url, imageView: imageView)
    }

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    static func dismantleUIView(_ uiView: UIImageView, coordinator: Coordinator) {
        coordinator.stop()
    }

    final class Coordinator: NSObject, URLSessionDataDelegate {
        // All mutable state is accessed exclusively on stateQueue.
        // No stateQueue.sync is used anywhere — all dispatches are async
        // to prevent deadlock when delegate callbacks re-enter from URLSession's
        // internal queue.
        private let stateQueue = DispatchQueue(label: "wavecam.mjpeg.coordinator")

        private var loadedURL: URL?
        private var buffer = Data()
        private var session: URLSession?
        private var task: URLSessionDataTask?
        // imageView is written on stateQueue; its image property is set on main.
        private weak var imageView: UIImageView?
        private var lastFrameAt = Date.distantPast
        private var watchdogWorkItem: DispatchWorkItem?
        private let stallTimeout: TimeInterval = 3.0

        func start(url: URL, imageView: UIImageView) {
            stateQueue.async { [weak self] in
                guard let self else { return }
                self.imageView = imageView
                guard self.loadedURL != url else { return }
                self.stopLocked()
                self.loadedURL = url
                self.connect(url: url)
            }
        }

        // Must be called from stateQueue.
        private func connect(url: URL) {
            let configuration = URLSessionConfiguration.default
            configuration.timeoutIntervalForRequest = 10
            // delegateQueue: nil → URLSession creates its own internal serial queue.
            // All delegate callbacks are dispatched to stateQueue before touching state.
            let session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
            self.session = session
            let task = session.dataTask(with: url)
            self.task = task
            lastFrameAt = Date()
            scheduleWatchdog()
            task.resume()
        }

        func stop() {
            stateQueue.async { [weak self] in
                self?.stopLocked()
            }
        }

        // Must be called from stateQueue.
        private func stopLocked() {
            watchdogWorkItem?.cancel()
            watchdogWorkItem = nil
            task?.cancel()
            session?.invalidateAndCancel()
            task = nil
            session = nil
            buffer.removeAll(keepingCapacity: false)
            loadedURL = nil
        }

        // MJPEG has no natural end; on a dropped/finished connection (Orin or
        // network blip) reconnect after a short backoff so the feed self-heals,
        // instead of relying on a view teardown driven by the 1Hz status poll.
        func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
            session.finishTasksAndInvalidate()
            stateQueue.async { [weak self] in
                guard let self, self.session === session, let url = self.loadedURL else { return }
                self.task = nil
                self.session = nil
                self.stateQueue.asyncAfter(deadline: .now() + 1.5) { [weak self] in
                    guard let self, self.loadedURL == url, self.session == nil else { return }
                    self.connect(url: url)
                }
            }
        }

        // Must be called from stateQueue.
        private func scheduleWatchdog() {
            watchdogWorkItem?.cancel()
            let item = DispatchWorkItem { [weak self] in
                self?.restartIfStalled()
            }
            watchdogWorkItem = item
            stateQueue.asyncAfter(deadline: .now() + stallTimeout, execute: item)
        }

        // Runs on stateQueue (dispatched from the watchdog DispatchWorkItem).
        private func restartIfStalled() {
            guard let url = loadedURL, session != nil else { return }
            if Date().timeIntervalSince(lastFrameAt) >= stallTimeout {
                reconnect(url: url)
                return
            }
            scheduleWatchdog()
        }

        // Must be called from stateQueue.
        private func reconnect(url: URL) {
            task?.cancel()
            session?.invalidateAndCancel()
            task = nil
            session = nil
            buffer.removeAll(keepingCapacity: true)
            connect(url: url)
        }

        func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
            stateQueue.async { [weak self] in
                guard let self else { return }
                self.buffer.append(data)
                self.drainFrames()
            }
        }

        // Runs on stateQueue. JPEG decode happens here (off main thread).
        // Only the image assignment hops to main.
        private func drainFrames() {
            let startMarker = Data([0xff, 0xd8])
            let endMarker = Data([0xff, 0xd9])

            while
                let start = buffer.range(of: startMarker),
                let end = buffer.range(of: endMarker, in: start.upperBound..<buffer.endIndex)
            {
                let frame = buffer[start.lowerBound..<end.upperBound]
                buffer.removeSubrange(buffer.startIndex..<end.upperBound)
                guard let image = UIImage(data: Data(frame)) else { continue }
                lastFrameAt = Date()
                scheduleWatchdog()
                DispatchQueue.main.async { [weak imageView] in
                    imageView?.image = image
                }
            }

            if buffer.count > 2_000_000 {
                buffer.removeAll(keepingCapacity: true)
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

    private var confidenceText: String {
        guard let confidence else { return "-" }
        return confidence.formatted(.number.precision(.fractionLength(2)))
    }

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
                Text(confidenceText)
            }
            .font(.system(size: 9, design: .monospaced))
            .foregroundStyle(WC.brand)
            .offset(x: -12, y: -57)
        }
    }
}

struct FeedReticles: View {
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

struct ReticleCorner: View {
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

struct FeedAimReticle: View {
    let status: WCStatus?
    let connected: Bool

    private var isMoving: Bool {
        guard connected else { return false }
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

struct FeedPTZOverlay: View {
    let status: WCStatus?
    let connected: Bool

    private var ptzEnabled: Bool {
        connected && status?.ptz.enabled != false
    }

    private var killed: Bool {
        connected && status?.safety.killed == true
    }

    private var owner: String {
        guard connected else { return "-" }
        return status?.ptz.owner.ptzOwnerLabel ?? "-"
    }

    private var command: String {
        guard connected else { return "-" }
        return status?.ptz.panTiltCmd?.uppercased() ?? "-"
    }

    private var zoom: String {
        guard connected else { return "-" }
        return status?.ptz.zoomState?.uppercased() ?? "-"
    }

    private var stateText: String {
        if !connected { return "OFFLINE" }
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
        case "OFF", "IDLE", "OFFLINE": WC.warn
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

struct PTZOverlayMetric: View {
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

struct FeedTopTags: View {
    let isLocked: Bool
    let isRecording: Bool
    let connected: Bool

    var body: some View {
        VStack {
            HStack(spacing: 8) {
                if connected {
                    LiveTag(text: isLocked ? "LOCKED" : "SEARCH", color: isLocked ? WC.brand : WC.warn, dot: isLocked)
                } else {
                    LiveTag(text: "OFFLINE", color: WC.warn, dot: false)
                }
                if connected && isRecording {
                    LiveTag(text: "REC", color: WC.kill, dot: true)
                }
            }
            .padding(.top, 12)
            Spacer()
        }
    }
}

struct LiveTag: View {
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

/// Plain-English reason the camera isn't locked, shown under the top tags.
/// Built only from real tracking fields; silent when locked, offline, or
/// the backend doesn't report the color/person components.
struct FeedLockReason: View {
    let status: WCStatus?
    let connected: Bool

    var body: some View {
        if let reason {
            VStack {
                Text(reason.text)
                    .font(.system(size: 10, weight: .medium))
                    .multilineTextAlignment(.center)
                    .foregroundStyle(reason.color)
                    .padding(.horizontal, 9)
                    .padding(.vertical, 5)
                    .background(Color.black.opacity(0.62), in: .rect(cornerRadius: 8))
                    .overlay(RoundedRectangle(cornerRadius: 8).stroke(reason.color.opacity(0.45)))
                    .frame(maxWidth: 280)
                    .padding(.top, 44)
                Spacer()
            }
        }
    }

    private var reason: (text: String, color: Color)? {
        guard connected, let t = status?.tracking else { return nil }
        if status?.safety.killed == true { return ("STOPPED · Resume to track", WC.kill) }
        if t.locked { return nil }
        if t.hasColor == nil && t.hasPerson == nil { return ("Searching…", WC.muted) }
        let hasColor = t.hasColor ?? false
        let hasPerson = t.hasPerson ?? false
        if !hasColor && !hasPerson { return ("No target — does Color preset match the subject?", WC.warn) }
        if hasColor && !hasPerson { return ("Color seen · no YOLO person", WC.muted) }
        if !hasColor && hasPerson { return ("Person seen · no color match", WC.muted) }
        return ("Acquiring…", WC.muted)
    }
}

private struct FeedBottomStrip: View {
    let status: WCStatus?
    let connected: Bool

    private var stateText: String {
        guard connected else { return "OFFLINE" }
        return status?.session.state ?? "-"
    }

    private var stateColor: Color {
        connected ? WC.ok : WC.warn
    }

    var body: some View {
        VStack {
            Spacer()
            HStack(spacing: 6) {
                FeedMetric(label: "STATE", value: stateText, color: stateColor)
                FeedMetric(label: "CONF", value: confidenceText, color: WC.brand)
                FeedMetric(label: "FPS", value: fpsText, color: WC.txt)
                FeedMetric(label: "GPS", value: distanceText, color: connected ? (gpsStale ? WC.warn : WC.ok) : WC.faint)
            }
            .padding(10)
        }
    }

    private var confidenceText: String {
        guard connected, let value = status?.tracking.confidence else { return "-" }
        return value.formatted(.number.precision(.fractionLength(2)))
    }

    private var fpsText: String {
        guard connected, let value = status?.tracking.fps else { return "-" }
        return value.formatted(.number.precision(.fractionLength(1)))
    }

    private var gpsStale: Bool { status?.gps?.stale == true }

    private var distanceText: String {
        guard connected, let meters = status?.gps?.distanceM else { return "-" }
        let distance = "\(Int(meters.rounded()))m"
        return gpsStale ? "\(distance) !" : distance
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
    let connected: Bool
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
        StatusPill(title: "OWNER", value: connected ? (status?.ptz.owner.ptzOwnerLabel ?? "-") : "-", color: connected ? WC.brand : WC.warn)
        StatusPill(title: "MODE", value: connected ? (status?.session.mode?.uppercased() ?? "-") : "OFFLINE", color: connected ? WC.ok : WC.warn)
        StatusPill(title: "PTZ", value: ptzState, color: connected && status?.ptz.enabled != false ? WC.ok : WC.warn)
        StatusPill(title: "FREE", value: freeText, color: freeColor)
    }

    private var ptzState: String {
        guard connected else { return "-" }
        if status?.ptz.enabled == false { return "OFF" }
        return status?.ptz.panTiltCmd?.uppercased() ?? "-"
    }

    private var freeText: String {
        guard connected, let gb = status?.media?.freeGb else { return "-" }
        return "\(Int(gb.rounded()))GB"
    }

    private var freeColor: Color {
        guard connected, let gb = status?.media?.freeGb else { return WC.faint }
        return gb < 20 ? WC.warn : WC.ok
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

/// Start/stop the Orin recorder (server: media/record/start|stop). The core
/// "film" verb, surfaced on the screen the operator watches while filming.
struct RecordButton: View {
    @Environment(WaveCamClient.self) private var client
    var compact = false

    private var isRecording: Bool { client.status?.media?.recording == true }
    private var segmentName: String? { client.status?.media?.segmentName }

    var body: some View {
        if compact {
            // Icon-only — the Live control dock. Uniform 44pt; red = the record verb,
            // a filled stop.fill while recording. No wrapping text, no filename clutter.
            Button {
                Task { await client.toggleRecording() }
            } label: {
                Image(systemName: isRecording ? "stop.fill" : "record.circle")
                    .font(.system(size: 18, weight: .bold))
                    .foregroundStyle(isRecording ? .white : WC.kill)
                    .frame(width: 44, height: 44)
                    .background(
                        isRecording ? WC.kill : WC.kill.opacity(0.15),
                        in: .rect(cornerRadius: WCRadius.xs)
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: WCRadius.xs)
                            .stroke(WC.kill.opacity(isRecording ? 0.85 : 0.4))
                    )
            }
            .buttonStyle(.plain)
            .disabled(!client.connected)
            .opacity(client.connected ? 1 : 0.5)
            .accessibilityLabel(isRecording ? "Stop recording" : "Start recording")
        } else {
            // Full labelled variant (used outside the dock).
            Button {
                Task { await client.toggleRecording() }
            } label: {
                HStack(spacing: 9) {
                    Image(systemName: isRecording ? "stop.fill" : "record.circle")
                        .font(.system(size: 15, weight: .bold))
                    Text(isRecording ? "Stop Recording" : "Record")
                        .font(.system(size: 15, weight: .bold))
                        .lineLimit(1)
                }
                .foregroundStyle(isRecording ? .white : WC.kill)
                .frame(maxWidth: .infinity, minHeight: 44)
                .padding(.vertical, 10)
                .background(isRecording ? WC.kill : WC.kill.opacity(0.14), in: .rect(cornerRadius: WCRadius.sm))
                .overlay(RoundedRectangle(cornerRadius: WCRadius.sm).stroke(WC.kill.opacity(isRecording ? 0 : 0.5)))
            }
            .buttonStyle(.plain)
            .disabled(!client.connected)
            .opacity(client.connected ? 1 : 0.5)
            .accessibilityLabel(isRecording ? "Stop recording" : "Start recording")
        }
    }
}

#Preview {
    LiveView()
        .environment(WaveCamClient(mode: .mock))
        .preferredColorScheme(.dark)
}
