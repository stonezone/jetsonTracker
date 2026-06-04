import SwiftUI

/// Merged Live + PTZ operator screen.
///
/// The live MJPEG feed fills the available space with the manual joystick
/// overlaid in the bottom-right corner, semi-transparent, so Zack can frame
/// the shot while controlling the camera. The Emergency Stop is always visible
/// via the TopBar chip; the fullscreen toggle keeps all PTZ controls overlaid.
///
/// Portrait: feed 16:9, joystick+action strip below/overlaid, zoom strip below.
/// Landscape (verticalSizeClass == .compact): feed fills width, joystick + controls
/// overlaid at the bottom-right.
///
/// Home gesture: tap or long-press the joystick center ring. Feature-detected
/// against WCConfig.supported.ptzHome — absent flag = silent no-op with hint.
struct MergedLiveView: View {
    @Environment(WaveCamClient.self) private var client
    @Environment(\.verticalSizeClass) private var verticalSizeClass

    @State private var controller = PTZManualController()
    @State private var isFullscreen = false
    @State private var config: WCConfig?
    @State private var showHomeUnavailableHint = false

    private var isLandscape: Bool { verticalSizeClass == .compact }
    private var homeSupported: Bool { config?.supported?.ptzHome == true }

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
        .task {
            config = await client.config()
        }
        .onDisappear { controller.cleanup(client: client) }
        .onChange(of: client.status?.revision) { _, _ in
            controller.syncCommandState(with: client)
        }
    }

    // MARK: - Portrait

    private var portraitLayout: some View {
        ScrollView {
            VStack(spacing: 0) {
                feedWithOverlay(height: 430)
                VStack(spacing: 10) {
                    mergedZoomCard()
                    mergedActionRow(compact: false)
                    RecordButton()
                    PTZControlFeedback(
                        commandState: controller.commandState,
                        controlError: client.lastControlError,
                        refusalText: controller.refusalText
                    )
                    if showHomeUnavailableHint {
                        homeUnavailablePill
                    }
                }
                .padding(.horizontal, 16)
                .padding(.top, 10)
                .padding(.bottom, 22)
            }
        }
        .scrollIndicators(.hidden)
    }

    // MARK: - Landscape

    private var landscapeLayout: some View {
        HStack(alignment: .top, spacing: 0) {
            // Feed takes remaining width; overlaid joystick + fullscreen icon inside
            feedWithOverlay(height: nil)
                .frame(maxWidth: .infinity, maxHeight: .infinity)

            // Right sidebar: zoom + action + record + feedback
            VStack(spacing: 8) {
                ScrollView {
                    VStack(spacing: 8) {
                        mergedZoomCard()
                        mergedActionRow(compact: true)
                        RecordButton(compact: true)
                        PTZControlFeedback(
                            commandState: controller.commandState,
                            controlError: client.lastControlError,
                            refusalText: controller.refusalText
                        )
                        if showHomeUnavailableHint {
                            homeUnavailablePill
                        }
                    }
                }
                .scrollIndicators(.hidden)
                EmergencyStopButton(style: .compact)
            }
            .frame(width: 190)
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
        }
        .padding(.leading, 14)
    }

    // MARK: - Fullscreen

    private var fullscreenLayout: some View {
        ZStack {
            // Feed fills screen
            feedCard(height: nil)
                .ignoresSafeArea()

            // Overlaid controls
            VStack {
                HStack {
                    // Exit fullscreen top-left
                    fullscreenToggleButton
                    Spacer()
                    PTZControlFeedback(
                        commandState: controller.commandState,
                        controlError: client.lastControlError,
                        refusalText: controller.refusalText
                    )
                    .frame(maxWidth: 200)
                }
                .padding(.horizontal, 14)
                .padding(.top, 10)

                Spacer()

                HStack(alignment: .bottom, spacing: 12) {
                    // Zoom strip + record bottom-left
                    VStack(spacing: 8) {
                        RecordButton(compact: true)
                            .frame(maxWidth: 200)
                        PTZZoomCard(zoomCommand: Binding(
                            get: { controller.zoomCommand },
                            set: { controller.updateZoom($0, client: client) }
                        ))
                        .frame(maxWidth: 200)
                    }

                    Spacer()

                    // Joystick bottom-right
                    overlaidJoystick(size: 190)
                }
                .padding(.horizontal, 14)
                .padding(.bottom, 16)
            }
        }
    }

    // MARK: - Feed with overlaid joystick

    /// Feed card (with all existing overlay HUD elements) plus the transparent
    /// joystick overlaid bottom-right and the fullscreen toggle top-right.
    @ViewBuilder
    private func feedWithOverlay(height: CGFloat?) -> some View {
        ZStack(alignment: .bottomTrailing) {
            feedCard(height: height)

            // Joystick overlay, bottom-right, semi-transparent
            overlaidJoystick(size: isLandscape ? 160 : 180)
                .padding(.trailing, 10)
                .padding(.bottom, 14)

            // Fullscreen toggle, top-right inside the feed
            VStack {
                HStack {
                    Spacer()
                    if !isLandscape {
                        fullscreenToggleButton
                            .padding(.top, 10)
                            .padding(.trailing, 10)
                    }
                }
                Spacer()
            }
        }
    }

    // MARK: - Feed card (reusing LiveView's exact feed components)

    @ViewBuilder
    private func feedCard(height: CGFloat?) -> some View {
        let feed = ZStack {
            // Feed or mock ocean
            if let previewURL = client.previewURL {
                MJPEGPreviewView(url: previewURL)
            } else {
                mergedMockFeed
            }
            // HUD overlays from LiveView (now internal)
            FeedReticles()
            FeedAimReticle(status: client.status, connected: client.connected)
            FeedPTZOverlay(status: client.status, connected: client.connected)
            FeedTopTags(
                isLocked: client.connected && client.status?.tracking.locked == true,
                isRecording: client.connected && client.status?.media?.recording == true,
                connected: client.connected
            )
            FeedLockReason(status: client.status, connected: client.connected)
        }
        .clipShape(.rect(cornerRadius: isFullscreen ? 0 : 20))
        .overlay(isFullscreen ? nil : RoundedRectangle(cornerRadius: 20).stroke(Color.white.opacity(0.14)))
        .shadow(color: .black.opacity(0.32), radius: 24, y: 14)

        if let height {
            feed.frame(height: height)
        } else {
            feed.frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private var mergedMockFeed: some View {
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
                    .padding(14)
                    .background(Color.black.opacity(0.45), in: .rect(cornerRadius: 16))
            }
        }
    }

    // MARK: - Overlaid joystick

    /// Semi-transparent joystick that overlays the feed. The center nub gets
    /// tap + long-press for home (feature-detected via JoystickPad.onHome param).
    private func overlaidJoystick(size: CGFloat) -> some View {
        JoystickPad(
            knobOffset: Binding(get: { controller.knobOffset }, set: { controller.knobOffset = $0 }),
            diameter: size,
            onCommand: { p, t in controller.sendVelocity(pan: p, tilt: t, client: client) },
            onStop: { controller.releaseManualPTZ(client: client) },
            onHome: handleHomeGesture,
            semiTransparent: true
        )
        .opacity(0.82)
    }

    private func handleHomeGesture() {
        guard homeSupported else {
            showHomeUnavailableHint = true
            DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
                showHomeUnavailableHint = false
            }
            return
        }
        controller.ptzHome(client: client)
    }

    // MARK: - Action row (reusing PTZActionRow)

    private func mergedActionRow(compact: Bool) -> some View {
        PTZActionRow(
            isAuto: controller.commandState.isAutoActive || client.owner.isAutonomousPTZOwner,
            isStopped: controller.commandState.isStopActive || controller.backendHeldStop(client: client),
            compact: compact,
            onStartAuto: { controller.startAutoPTZ(client: client) },
            onStop: { controller.holdPTZ(client: client) },
            onRefresh: { Task { await client.refresh() } }
        )
    }

    // MARK: - Zoom card passthrough

    private func mergedZoomCard() -> some View {
        PTZZoomCard(zoomCommand: Binding(
            get: { controller.zoomCommand },
            set: { controller.updateZoom($0, client: client) }
        ))
    }

    // MARK: - Fullscreen toggle

    private var fullscreenToggleButton: some View {
        Button {
            withAnimation(.easeInOut(duration: 0.22)) {
                isFullscreen.toggle()
            }
        } label: {
            Image(systemName: isFullscreen ? "arrow.down.right.and.arrow.up.left" : "arrow.up.left.and.arrow.down.right")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(WC.txt)
                .frame(width: 36, height: 36)
                .background(Color.black.opacity(0.58), in: .rect(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(WC.line))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(isFullscreen ? "Exit fullscreen" : "Fullscreen")
    }

    // MARK: - Home unavailable hint

    private var homeUnavailablePill: some View {
        HStack(spacing: 6) {
            Image(systemName: "house.slash")
                .font(.system(size: 10, weight: .semibold))
            Text("Home unavailable — backend does not support /ptz/home yet")
                .font(.system(size: 11, weight: .medium))
                .lineLimit(2)
                .minimumScaleFactor(0.8)
        }
        .foregroundStyle(WC.muted)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(WC.muted.opacity(0.12), in: .rect(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(WC.muted.opacity(0.3)))
    }
}

// MARK: - Extensions (file-private; PTZView.swift has its own private copies)

private extension CGSize {
    func clamped(to radius: CGFloat) -> CGSize {
        let distance = sqrt(width * width + height * height)
        guard distance > radius, distance > 0 else { return self }
        let scale = radius / distance
        return CGSize(width: width * scale, height: height * scale)
    }
}

private extension Double {
    func zeroed(deadzone: Double) -> Double {
        abs(self) < deadzone ? 0 : self
    }

    var signedPTZ: String {
        formatted(.number.sign(strategy: .always()).precision(.fractionLength(2)))
    }
}

#Preview {
    MergedLiveView()
        .environment(WaveCamClient(mode: .mock))
        .preferredColorScheme(.dark)
}
