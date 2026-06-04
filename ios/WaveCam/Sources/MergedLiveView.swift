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
                isFullscreen: isFullscreen,
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
                isFullscreen: isFullscreen,
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

            // Exit — top-right, same toggle/position as the in-feed control
            fullscreenToggleButton
                .padding(WCSpace.md)

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
    let isFullscreen: Bool
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

                Divider().background(Color.white.opacity(0.18))

                // AUTO toggle
                GlassIconButton(
                    systemImage: isAutoActive ? "viewfinder.circle.fill" : "viewfinder.circle",
                    state: isAutoActive ? .active : .normal,
                    action: onAutoToggle
                )
                .accessibilityLabel(isAutoActive ? "Stop auto tracking" : "Start auto tracking")

                // REC
                RecordButton(compact: true)
                    .frame(width: 44)

                // HOME
                GlassIconButton(
                    systemImage: "house",
                    state: .normal,
                    disabled: !homeSupported,
                    action: onHome
                )
                .accessibilityLabel(homeSupported ? "Camera home" : "Home unavailable")

                Spacer(minLength: WCSpace.sm)

                // STOP — always at bottom, distinct red, pinned
                EmergencyStopButton(style: .compact)
                    .frame(width: 44, height: 44)
                    .clipShape(.rect(cornerRadius: WCRadius.xs))
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

                // AUTO toggle
                GlassIconButton(
                    systemImage: isAutoActive ? "viewfinder.circle.fill" : "viewfinder.circle",
                    state: isAutoActive ? .active : .normal,
                    action: onAutoToggle
                )
                .accessibilityLabel(isAutoActive ? "Stop auto tracking" : "Start auto tracking")

                // REC
                RecordButton(compact: true)
                    .frame(width: 44)

                // HOME
                GlassIconButton(
                    systemImage: "house",
                    state: .normal,
                    disabled: !homeSupported,
                    action: onHome
                )
                .accessibilityLabel(homeSupported ? "Camera home" : "Home unavailable")

                // STOP — pinned end, red
                EmergencyStopButton(style: .compact)
                    .frame(width: 44, height: 44)
                    .clipShape(.rect(cornerRadius: WCRadius.xs))
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
                    .fill(Color.white.opacity(0.10))
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

// MARK: - Preview

#Preview {
    MergedLiveView()
        .environment(WaveCamClient(mode: .mock))
        .preferredColorScheme(.dark)
}
