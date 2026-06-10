import SwiftUI

/// Live operator screen — Glass Rail redesign (2026-06-03).
///
/// Full-bleed MJPEG feed with a single Liquid Glass control rail:
/// - Landscape: rail pinned to the right edge (~62pt), full height.
/// - Portrait:  rail becomes a horizontal glass dock at the bottom.
/// - Fullscreen: rail/dock hidden; floating STOP chip stays reachable; root TopBar KILL
///   chip is always present regardless.
///
/// All PTZ command logic lives in PTZManualController (untouched).
struct MergedLiveView: View {
    @Environment(WaveCamClient.self) private var client
    @Environment(\.verticalSizeClass) private var verticalSizeClass

    @State private var controller = PTZManualController()
    @State private var isFullscreen = false
    @State private var config: WCConfig?

    // Toast state — bound to GlassToast; set from lastControlError / refusalText
    @State private var toastMessage: String?

    private var isLandscape: Bool { verticalSizeClass == .compact }
    private var homeSupported: Bool { config?.supported?.ptzHome == true }
    private var isAutoActive: Bool {
        controller.commandState.isAutoActive || client.owner.isAutonomousPTZOwner
    }

    var body: some View {
        Group {
            if isFullscreen {
                fullscreenLayout
            } else if isLandscape {
                landscapeLayout
            } else {
                portraitLayout
            }
        }
        .background(WC.bg.ignoresSafeArea())
        .task { await client.refresh() }
        .task { config = await client.config() }
        .onDisappear { controller.cleanup(client: client) }
        .onChange(of: client.status?.revision) { _, _ in
            controller.syncCommandState(with: client)
        }
        // Surface errors as toasts; prefer refusal text over generic error
        .onChange(of: controller.refusalText) { _, new in
            if let text = new { showToast(text) }
        }
        .onChange(of: client.lastControlError) { _, new in
            if let text = new { showToast(text) }
        }
    }

    // MARK: - Landscape

    private var landscapeLayout: some View {
        HStack(alignment: .top, spacing: 0) {
            feedLayer
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            LiveControlRail(
                isLandscape: true,
                isAutoActive: isAutoActive,
                homeSupported: homeSupported,
                controller: controller,
                onAutoToggle: toggleAuto,
                onHome: handleHome
            )
            .frame(width: 62)
            .padding(.vertical, WCSpace.sm)
            .padding(.trailing, WCSpace.sm)
        }
    }

    // MARK: - Portrait

    private var portraitLayout: some View {
        VStack(spacing: 0) {
            feedLayer
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            LiveControlRail(
                isLandscape: false,
                isAutoActive: isAutoActive,
                homeSupported: homeSupported,
                controller: controller,
                onAutoToggle: toggleAuto,
                onHome: handleHome
            )
            .padding(.horizontal, WCSpace.sm)
            .padding(.bottom, WCSpace.sm)
            .padding(.top, WCSpace.xs)
        }
    }

    // MARK: - Fullscreen

    private var fullscreenLayout: some View {
        ZStack(alignment: .topTrailing) {
            feedCard(fullscreen: true)
                .ignoresSafeArea()

            // Telemetry HUD — fullscreen is the tripod-monitoring mode, so lock/GPS
            // state must stay visible (it was dropped here pre-2026-06-10, leaving
            // the operator blind to LOCKED/GPS while in fullscreen).
            VStack {
                HStack(alignment: .top) {
                    GlassLockChip(status: client.status, connected: client.connected)
                    GlassGPSChip(status: client.status, connected: client.connected)
                    Spacer()
                    fullscreenToggleButton
                }
                .padding(WCSpace.md)
                Spacer()
            }

            // Floating STOP — always reachable in fullscreen (safety invariant)
            VStack {
                Spacer()
                HStack {
                    Spacer()
                    EmergencyStopButton(style: .compact)
                        .frame(width: 200)
                        .padding(WCSpace.md)
                }
            }
        }
    }

    // MARK: - Feed layer (feed + floating joystick + HUD chips + toast)

    private var feedLayer: some View {
        ZStack(alignment: .bottomLeading) {
            feedCard(fullscreen: false)

            // Joystick — bottom-left over feed, semi-transparent
            overlaidJoystick(size: isLandscape ? 156 : 172)
                .padding(.leading, WCSpace.md)
                .padding(.bottom, WCSpace.md)

            // Top HUD row — lock chip (left) + fullscreen toggle (right)
            VStack {
                HStack(alignment: .top) {
                    GlassLockChip(status: client.status, connected: client.connected)
                    GlassGPSChip(status: client.status, connected: client.connected)
                    Spacer()
                    fullscreenToggleButton
                }
                .padding(WCSpace.md)
                Spacer()
            }

            // Error/refusal toast — bottom, above joystick row
            GlassToast(message: $toastMessage)
        }
    }

    // MARK: - Feed card

    private func feedCard(fullscreen: Bool) -> some View {
        ZStack {
            if let url = client.previewURL {
                MJPEGPreviewView(url: url)
            } else {
                mockFeed
            }
            FeedReticles()
            FeedAimReticle(status: client.status, connected: client.connected)
            FeedLockReason(status: client.status, connected: client.connected)
        }
        .clipShape(.rect(cornerRadius: fullscreen ? 0 : WCRadius.lg))
        .overlay(
            fullscreen ? nil :
            RoundedRectangle(cornerRadius: WCRadius.lg)
                .stroke(Color.white.opacity(0.14))
        )
        .shadow(color: .black.opacity(0.32), radius: 24, y: 14)
    }

    private var mockFeed: some View {
        ZStack {
            LinearGradient(
                colors: [Color(hex: 0x16344A), Color(hex: 0x1B4A5A), Color(hex: 0x0F3A44), Color(hex: 0x0A2630)],
                startPoint: .top,
                endPoint: .bottom
            )
            if !client.connected {
                Image(systemName: "wifi.slash")
                    .font(.system(size: 24, weight: .semibold))
                    .foregroundStyle(WC.muted)
                    .padding(WCSpace.md)
                    .background(Color.black.opacity(0.45), in: .rect(cornerRadius: WCRadius.md))
            }
        }
    }

    // MARK: - Joystick overlay

    private func overlaidJoystick(size: CGFloat) -> some View {
        JoystickPad(
            knobOffset: Binding(
                get: { controller.knobOffset },
                set: { controller.knobOffset = $0 }
            ),
            diameter: size,
            onCommand: { p, t in controller.sendVelocity(pan: p, tilt: t, client: client) },
            onStop: { controller.releaseManualPTZ(client: client) },
            onHome: handleHome,
            semiTransparent: true
        )
        .opacity(0.82)
    }

    // MARK: - Actions

    private func toggleAuto() {
        if isAutoActive {
            controller.holdPTZ(client: client)
        } else {
            controller.startAutoPTZ(client: client)
        }
    }

    private func handleHome() {
        guard homeSupported else {
            showToast("Home unavailable — /ptz/home not yet supported by the backend")
            return
        }
        controller.ptzHome(client: client)
    }

    /// Fullscreen enter/exit toggle — lives on the feed's top-right corner so it's always
    /// in one consistent, obvious spot (and frees the rail for a usable zoom slider).
    private var fullscreenToggleButton: some View {
        Button {
            withAnimation(.easeInOut(duration: 0.2)) { isFullscreen.toggle() }
        } label: {
            Image(systemName: isFullscreen ? "arrow.down.right.and.arrow.up.left" : "arrow.up.left.and.arrow.down.right")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(WC.txt)
                .frame(width: 44, height: 44)
                .background(Color.black.opacity(0.55), in: .rect(cornerRadius: WCRadius.sm))
                .overlay(RoundedRectangle(cornerRadius: WCRadius.sm).stroke(WC.line))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(isFullscreen ? "Exit fullscreen" : "Fullscreen")
    }

    private func showToast(_ text: String) {
        withAnimation(.spring(duration: 0.3)) {
            toastMessage = text
        }
    }
}

// MARK: - LiveControlRail

/// The unified Liquid Glass control panel. Adapts between a vertical rail (landscape)
/// and a horizontal dock (portrait).
private struct LiveControlRail: View {
    let isLandscape: Bool
    let isAutoActive: Bool
    let homeSupported: Bool
    let controller: PTZManualController
    let onAutoToggle: () -> Void
    let onHome: () -> Void

    @Environment(WaveCamClient.self) private var client

    var body: some View {
        if #available(iOS 26, *) {
            GlassEffectContainer {
                railContent
            }
        } else {
            railContent
        }
    }

    @ViewBuilder
    private var railContent: some View {
        if isLandscape {
            verticalRail
        } else {
            horizontalDock
        }
    }

    // MARK: Landscape — vertical rail (right edge, ~62pt wide)

    private var verticalRail: some View {
        GlassSurface(cornerRadius: WCRadius.md, tinted: true) {
            VStack(spacing: WCSpace.sm) {
                // Zoom — vertical spring-to-center slider; fills available space but keeps a
                // usable minimum so it can't collapse in the height-constrained landscape rail.
                GlassZoomSlider(
                    zoomCommand: Binding(
                        get: { controller.zoomCommand },
                        set: { controller.updateZoom($0, client: client) }
                    ),
                    onRelease: { controller.stopZoomCommand(client: client) },
                    axis: .vertical
                )
                .frame(minHeight: 96, maxHeight: .infinity)

                Divider().background(Color.white.opacity(0.12))

                // Mode toggles + record (uniform 44pt icons)
                GlassIconButton(
                    systemImage: isAutoActive ? "viewfinder.circle.fill" : "viewfinder.circle",
                    state: isAutoActive ? .active : .normal,
                    action: onAutoToggle
                )
                .accessibilityLabel(isAutoActive ? "Stop auto tracking" : "Start auto tracking")

                GlassIconButton(
                    systemImage: "house",
                    state: .normal,
                    disabled: !homeSupported,
                    action: onHome
                )
                .accessibilityLabel(homeSupported ? "Camera home" : "Home unavailable")

                RecordButton(compact: true)

                Spacer(minLength: WCSpace.xs)

                // STOP — the one safety control, pinned at the bottom, solid red
                EmergencyStopButton(style: .icon)
            }
            .padding(WCSpace.sm)
        }
    }

    // MARK: Portrait — horizontal dock (bottom)

    private var horizontalDock: some View {
        GlassSurface(cornerRadius: WCRadius.md, tinted: true) {
            HStack(spacing: WCSpace.sm) {
                // Zoom — horizontal spring-to-center slider
                GlassZoomSlider(
                    zoomCommand: Binding(
                        get: { controller.zoomCommand },
                        set: { controller.updateZoom($0, client: client) }
                    ),
                    onRelease: { controller.stopZoomCommand(client: client) },
                    axis: .horizontal
                )
                .frame(maxWidth: .infinity)

                Divider().frame(width: 1, height: 30).overlay(Color.white.opacity(0.12))

                // Mode toggles + record (uniform 44pt icons)
                GlassIconButton(
                    systemImage: isAutoActive ? "viewfinder.circle.fill" : "viewfinder.circle",
                    state: isAutoActive ? .active : .normal,
                    action: onAutoToggle
                )
                .accessibilityLabel(isAutoActive ? "Stop auto tracking" : "Start auto tracking")

                GlassIconButton(
                    systemImage: "house",
                    state: .normal,
                    disabled: !homeSupported,
                    action: onHome
                )
                .accessibilityLabel(homeSupported ? "Camera home" : "Home unavailable")

                RecordButton(compact: true)

                Divider().frame(width: 1, height: 30).overlay(Color.white.opacity(0.12))

                // STOP — the one safety control, solid red, set apart
                EmergencyStopButton(style: .icon)
            }
            .padding(WCSpace.sm)
        }
    }
}

// MARK: - GlassZoomSlider

/// Velocity-based spring-to-center zoom slider.
///
/// Center = stop (zoom command = 0). Displacement toward tele/wide sets proportional
/// speed. Releasing snaps back to center and fires `onRelease` (which sends stop).
/// Reuses `PTZManualController.updateZoom` / `stopZoomCommand` — no new command logic.
private struct GlassZoomSlider: View {
    enum Axis { case vertical, horizontal }

    @Binding var zoomCommand: Double
    let onRelease: () -> Void
    let axis: Axis

    @State private var isDragging = false

    var body: some View {
        GeometryReader { geo in
            let trackLength = axis == .vertical ? geo.size.height : geo.size.width

            ZStack {
                // Track background
                Capsule()
                    .fill(Color.white.opacity(0.18))
                    .frame(
                        width:  axis == .vertical ? 6 : nil,
                        height: axis == .vertical ? nil : 6
                    )

                // Active fill from center toward displacement
                activeFill(trackLength: trackLength)

                // Knob — circle, springs back to center on release
                knob(trackLength: trackLength)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 2)
                    .onChanged { value in
                        isDragging = true
                        let raw = axis == .vertical
                            ? -value.translation.height / (trackLength / 2)
                            : value.translation.width / (trackLength / 2)
                        // Clamp to -1…1, map to zoom velocity
                        let clamped = max(-1.0, min(1.0, raw))
                        zoomCommand = clamped
                    }
                    .onEnded { _ in
                        isDragging = false
                        withAnimation(.spring(response: 0.25, dampingFraction: 0.7)) {
                            zoomCommand = 0
                        }
                        onRelease()
                    }
            )
        }
        .frame(
            width:  axis == .vertical ? 32 : nil,
            height: axis == .vertical ? nil : 32
        )
        // Tele (+) / wide (−) end markers so it reads clearly as a zoom control.
        .overlay(alignment: axis == .vertical ? .top : .trailing) {
            Image(systemName: "plus").font(.system(size: 8, weight: .heavy)).foregroundStyle(WC.muted)
        }
        .overlay(alignment: axis == .vertical ? .bottom : .leading) {
            Image(systemName: "minus").font(.system(size: 8, weight: .heavy)).foregroundStyle(WC.muted)
        }
    }

    @ViewBuilder
    private func activeFill(trackLength: CGFloat) -> some View {
        let displacement = CGFloat(zoomCommand) * (trackLength / 2)
        if axis == .vertical {
            Capsule()
                .fill(WC.accent.opacity(0.55))
                .frame(width: 6, height: max(0, abs(displacement)))
                .offset(y: -displacement / 2)
        } else {
            Capsule()
                .fill(WC.accent.opacity(0.55))
                .frame(width: max(0, abs(displacement)), height: 6)
                .offset(x: displacement / 2)
        }
    }

    @ViewBuilder
    private func knob(trackLength: CGFloat) -> some View {
        let displacement = CGFloat(zoomCommand) * (trackLength / 2)
        let knobSize: CGFloat = 26

        Circle()
            .fill(
                RadialGradient(
                    colors: [WC.accent.opacity(0.9), WC.accent.opacity(0.6)],
                    center: .center,
                    startRadius: 2,
                    endRadius: 14
                )
            )
            .shadow(color: WC.accent.opacity(0.4), radius: 8)
            .frame(width: knobSize, height: knobSize)
            .overlay(Circle().stroke(Color.white.opacity(0.3), lineWidth: 1))
            .offset(
                x: axis == .horizontal ? displacement : 0,
                y: axis == .vertical ? -displacement : 0
            )
            .animation(.spring(response: 0.25, dampingFraction: 0.7), value: zoomCommand)
    }
}

// MARK: - GlassLockChip

/// Minimal HUD chip showing lock state + plain-English reason (wraps FeedLockReason logic).
private struct GlassLockChip: View {
    let status: WCStatus?
    let connected: Bool

    private var locked: Bool { connected && status?.tracking.locked == true }
    private var isRecording: Bool { connected && status?.media?.recording == true }
    private var killed: Bool { connected && status?.safety.killed == true }

    private var lockLabel: String {
        if !connected { return "OFFLINE" }
        if killed { return "STOPPED" }
        if locked { return "LOCKED" }
        return lockHintText ?? "SEARCH"
    }

    private var lockColor: Color {
        if !connected { return WC.warn }
        if killed { return WC.kill }
        if locked { return WC.ok }
        return WC.warn
    }

    private var lockHintText: String? {
        guard connected, let t = status?.tracking else { return nil }
        if t.locked { return nil }
        let hasColor = t.hasColor ?? false
        let hasPerson = t.hasPerson ?? false
        if !hasColor && !hasPerson { return nil }
        if hasColor && !hasPerson { return "CLR·NO YOLO" }
        if !hasColor && hasPerson { return "YOLO·NO CLR" }
        return "ACQUIRING"
    }

    var body: some View {
        HStack(spacing: WCSpace.sm) {
            GlassChip(text: lockLabel, color: lockColor, dot: locked)
            if isRecording {
                GlassChip(text: "REC", color: WC.kill, dot: true)
            }
        }
    }
}

/// At-a-glance LoRa GPS health on the feed. Feature-detected: shown only once the
/// backend reports a gps.source, so the HUD stays clean until GPS is live. Colour +
/// dot = freshness (green/live vs amber/stale); label carries the camera→target range
/// and bearing — or "NO FIX" when GPS is live but the camera-reference (base) position
/// hasn't locked yet (distance/bearing null), so the field operator sees *why* it's not
/// pointing without SSHing the Orin.
private struct GlassGPSChip: View {
    let status: WCStatus?
    let connected: Bool
    @State private var showDetail = false

    private var gps: WCStatus.GPS? {
        guard connected, let g = status?.gps, g.source != nil else { return nil }
        return g
    }

    // distance/bearing are null until BOTH the remote fix (source) and the base fix
    // exist; with source live, a null distance means the base hasn't locked its 3D fix.
    private var hasFix: Bool { gps?.distanceM != nil }
    private var stale: Bool { gps?.stale ?? false }

    var body: some View {
        if let g = gps {
            Button { showDetail = true } label: {
                GlassChip(text: label(g),
                          color: (!hasFix || stale) ? WC.warn : WC.ok,
                          dot: hasFix && !stale)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(voiceOver(g))
            .accessibilityHint("Shows GPS detail")
            .popover(isPresented: $showDetail) {
                GPSDetailCard(gps: g)
                    .presentationCompactAdaptation(.popover)
            }
        }
    }

    private func label(_ g: WCStatus.GPS) -> String {
        guard let d = g.distanceM else { return "GPS·NO FIX" }
        if let b = g.bearingDeg {
            return "GPS \(Int(d.rounded()))m·\(Int(b.rounded()))°"
        }
        return "GPS \(Int(d.rounded()))m"
    }

    private func voiceOver(_ g: WCStatus.GPS) -> String {
        guard g.distanceM != nil else { return "GPS live, no base fix yet" }
        var parts = ["GPS"]
        if let d = g.distanceM { parts.append("\(Int(d.rounded())) meters") }
        if let b = g.bearingDeg { parts.append("bearing \(Int(b.rounded())) degrees") }
        parts.append(stale ? "stale" : "live")
        return parts.joined(separator: ", ")
    }
}

/// Tap-through detail for the GPS chip — a solid, outdoor-legible readout so the field
/// operator can see source, range/bearing, target freshness, and base-fix status (the
/// base-fix line is the usual culprit when GPS is live but the camera isn't pointing).
private struct GPSDetailCard: View {
    let gps: WCStatus.GPS
    @Environment(WaveCamClient.self) private var client
    @State private var confirmRestart = false

    var body: some View {
        OperatorCard(title: "GPS") {
            VStack(alignment: .leading, spacing: WCSpace.sm) {
                row("Source", gps.source?.uppercased() ?? "—", WC.accent)
                row("Range", gps.distanceM.map { "\(Int($0.rounded())) m" } ?? "—",
                    gps.distanceM != nil ? WC.accent : WC.faint)
                row("Bearing", gps.bearingDeg.map { "\(Int($0.rounded()))°" } ?? "—",
                    gps.bearingDeg != nil ? WC.accent : WC.faint)
                row("Target", targetText, (gps.stale ?? false) ? WC.warn : WC.ok)
                row("Base fix", baseText, gps.baseAgeSec != nil ? WC.ok : WC.warn)
                if let alive = gps.readerAlive {
                    row("Ingest", ingestText(alive), alive ? WC.ok : WC.warn)
                    // The fix for a dead reader IS a service restart — put the button
                    // where the operator is looking when they discover it.
                    if !alive {
                        GlassButton(
                            label: "Restart WaveCam service",
                            icon: "arrow.clockwise.circle",
                            role: .normal,
                            disabled: client.mode != .live,
                            action: { confirmRestart = true }
                        )
                    }
                }
            }
            .frame(width: 230, alignment: .leading)
        }
        .confirmationDialog("Restart the vision service? PTZ stops first; ~15 s outage.",
                            isPresented: $confirmRestart, titleVisibility: .visible) {
            Button("Restart", role: .destructive) { Task { await client.systemRestart() } }
            Button("Cancel", role: .cancel) {}
        }
    }

    private func row(_ label: String, _ value: String, _ tint: Color) -> some View {
        HStack(spacing: WCSpace.md) {
            Text(label).font(WCFont.label).tracking(1.0).foregroundStyle(WC.faint)
            Spacer(minLength: WCSpace.md)
            Text(value).font(WCFont.mono).foregroundStyle(tint).lineLimit(1)
        }
    }

    // null target_age with source present = remote hasn't reported a position yet.
    private var targetText: String {
        let fresh = !(gps.stale ?? false)
        if let a = gps.targetAgeSec { return "\(fresh ? "LIVE" : "STALE") · \(age(a))" }
        return fresh ? "LIVE" : "STALE"
    }
    // null base_age with source present = base GPS has no 3D fix (needs open sky).
    private var baseText: String {
        if let a = gps.baseAgeSec { return "LOCKED · \(age(a))" }
        return "NO FIX — sky"
    }
    // Reader DOWN = the Orin's serial ingest thread is dead/disconnected (the
    // "silently stale GPS" failure) — restart wavecam.service, not the Wios.
    private func ingestText(_ alive: Bool) -> String {
        guard alive else { return "DOWN — restart svc" }
        if let p = gps.lastPollAgeSec { return "OK · \(age(p))" }
        return "OK"
    }
    private func age(_ s: Double) -> String {
        s < 60 ? "\(Int(s.rounded()))s" : "\(Int((s / 60).rounded()))m"
    }
}

// MARK: - Preview

#Preview {
    MergedLiveView()
        .environment(WaveCamClient(mode: .mock))
        .preferredColorScheme(.dark)
}
