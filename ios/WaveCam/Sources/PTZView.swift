import SwiftUI

/// Manual PTZ screen: velocity joystick, zoom control, command readout, and safety stop.
struct PTZView: View {
    @Environment(WaveCamClient.self) private var client
    @Environment(\.verticalSizeClass) private var verticalSizeClass

    @State private var controller = PTZManualController()

    private var isLandscapeControl: Bool {
        verticalSizeClass == .compact
    }

    var body: some View {
        ZStack(alignment: .bottomTrailing) {
            ScrollView {
                if isLandscapeControl {
                    landscapeControls
                } else {
                    portraitControls
                }
            }
            .safeAreaPadding(.bottom, isLandscapeControl ? 60 : 0)
            .scrollIndicators(.hidden)

            if isLandscapeControl {
                EmergencyStopButton(style: .compact)
                    .frame(width: 220)
                    .padding(.horizontal, 14)
                    .padding(.bottom, 8)
            }
        }
        .background(WC.bg.ignoresSafeArea())
        .task { await client.refresh() }
        .onDisappear { controller.cleanup(client: client) }
        .onChange(of: client.status?.revision) { _, _ in
            controller.syncCommandState(with: client)
        }
    }

    private var portraitControls: some View {
        VStack(spacing: 12) {
            PTZHeader(status: client.status)
            joystickCard()
            zoomCard()
            actionRow()
            PTZControlFeedback(commandState: controller.commandState, controlError: client.lastControlError, refusalText: controller.refusalText)
            EmergencyStopButton()
        }
        .padding(.horizontal, 16)
        .padding(.top, 6)
        .padding(.bottom, 22)
    }

    private var landscapeControls: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(spacing: 10) {
                PTZHeader(status: client.status)
                joystickCard(joystickSize: 176, compact: true)
            }
            .frame(maxWidth: .infinity)

            VStack(spacing: 10) {
                zoomCard()
                actionRow()
                PTZControlFeedback(commandState: controller.commandState, controlError: client.lastControlError, refusalText: controller.refusalText)
            }
            .frame(width: 220)
        }
        .padding(.horizontal, 14)
        .padding(.top, 8)
        .padding(.bottom, 12)
    }

    private func joystickCard(joystickSize: CGFloat = 230, compact: Bool = false) -> some View {
        PTZJoystickCard(
            pan: controller.pan,
            tilt: controller.tilt,
            knobOffset: Binding(get: { controller.knobOffset }, set: { controller.knobOffset = $0 }),
            owner: client.owner,
            joystickSize: joystickSize,
            compact: compact,
            onCommand: { p, t in controller.sendVelocity(pan: p, tilt: t, client: client) },
            onStop: { controller.releaseManualPTZ(client: client) }
        )
    }

    private func zoomCard() -> some View {
        PTZZoomCard(zoomCommand: Binding(
            get: { controller.zoomCommand },
            set: { controller.updateZoom($0, client: client) }
        ))
    }

    private func actionRow() -> some View {
        PTZActionRow(
            isAuto: controller.commandState.isAutoActive || client.owner.isAutonomousPTZOwner,
            isStopped: controller.commandState.isStopActive || controller.backendHeldStop(client: client),
            compact: isLandscapeControl,
            onStartAuto: { controller.startAutoPTZ(client: client) },
            onStop: { controller.holdPTZ(client: client) },
            onRefresh: { Task { await client.refresh() } }
        )
    }
}

private struct PTZHeader: View {
    let status: WCStatus?

    private var mode: String { status?.session.mode ?? "manual" }
    private var state: String { status?.session.state ?? "READY" }
    private var owner: String { status?.ptz.owner.ptzOwnerLabel ?? "IDLE" }
    private var ownerColor: Color {
        switch owner {
        case "AUTO": return WC.ok
        case "MANUAL": return WC.warn
        default: return WC.faint
        }
    }
    private var command: String { status?.ptz.panTiltCmd ?? "p0/t0" }

    var body: some View {
        HStack(spacing: 8) {
            PTZStatusPill(label: "STATE", value: state, color: state == "KILLED" ? WC.kill : WC.ok)
            PTZStatusPill(label: "MODE", value: mode, color: WC.brand)
            PTZStatusPill(label: "OWNER", value: owner, color: ownerColor)
            PTZStatusPill(label: "CMD", value: command, color: WC.txt)
        }
    }
}

private struct PTZStatusPill: View {
    let label: String
    let value: String
    let color: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.system(size: 9, weight: .semibold))
                .tracking(1.2)
                .foregroundStyle(WC.faint)
            Text(value.uppercased())
                .font(.system(size: 13, weight: .semibold, design: .monospaced))
                .foregroundStyle(color)
                .lineLimit(1)
                .minimumScaleFactor(0.72)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(WC.panel, in: .rect(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(WC.line))
    }
}

private struct PTZJoystickCard: View {
    // pan/tilt are display-only values read from the controller for the readout cells.
    // JoystickPad owns its own drag-local state and reports new values via onCommand.
    let pan: Double
    let tilt: Double
    @Binding var knobOffset: CGSize

    let owner: String
    var joystickSize: CGFloat = 230
    var compact = false
    let onCommand: (Double, Double) -> Void
    let onStop: () -> Void

    var body: some View {
        VStack(spacing: compact ? 8 : 12) {
            HStack {
                Text("Manual PTZ - release to stop")
                    .font(.system(size: 10, weight: .semibold))
                    .tracking(1.4)
                    .foregroundStyle(WC.muted)
                Spacer()
                if compact {
                    Text("OWNER \(owner.ptzOwnerLabel)")
                        .font(.system(size: 9, weight: .semibold, design: .monospaced))
                        .tracking(1.1)
                        .foregroundStyle(WC.brand)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            JoystickPad(
                knobOffset: $knobOffset,
                diameter: joystickSize,
                onCommand: onCommand,
                onStop: onStop
            )

            if !compact {
                HStack(spacing: 8) {
                    PTZReadoutCell(label: "PAN", value: pan.signedPTZ)
                    PTZReadoutCell(label: "TILT", value: tilt.signedPTZ)
                    PTZReadoutCell(label: "OWNER", value: owner.ptzOwnerLabel, tint: WC.brand)
                }
            }
        }
        .padding(compact ? 12 : 14)
        .background(WC.panel, in: .rect(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).stroke(WC.line))
    }
}

struct JoystickPad: View {
    @Binding var knobOffset: CGSize

    let diameter: CGFloat
    let onCommand: (Double, Double) -> Void
    let onStop: () -> Void
    /// When set, tapping or long-pressing the center nub fires this handler.
    var onHome: (() -> Void)? = nil
    /// When true, the pad background uses reduced-opacity gradients for feed overlay use.
    var semiTransparent: Bool = false

    private let deadzone: Double = 0.05
    private var commandRadius: CGFloat {
        diameter * 0.3565
    }
    private var nubSize: CGFloat { diameter * 0.32 }

    var body: some View {
        ZStack {
            Circle()
                .fill(
                    RadialGradient(
                        colors: semiTransparent
                            ? [Color(hex: 0x1A2730, alpha: 0.75), Color(hex: 0x0E161D, alpha: 0.75)]
                            : [Color(hex: 0x1A2730), Color(hex: 0x0E161D)],
                        center: .center,
                        startRadius: 8,
                        endRadius: 118
                    )
                )
            Circle()
                .stroke(Color.white.opacity(0.08), lineWidth: 1)
            Circle()
                .stroke(style: StrokeStyle(lineWidth: 1, dash: [5, 5]))
                .foregroundStyle(Color.white.opacity(0.1))
                .padding(diameter * 0.13)
            Rectangle()
                .fill(Color.white.opacity(0.08))
                .frame(width: 1, height: diameter * 0.72)
            Rectangle()
                .fill(Color.white.opacity(0.08))
                .frame(width: diameter * 0.72, height: 1)
            JoystickLabels(diameter: diameter)
            JoystickNub(size: nubSize, onHome: onHome)
                .offset(knobOffset)
        }
        .frame(width: diameter, height: diameter)
        .contentShape(Circle())
        .gesture(
            DragGesture(minimumDistance: onHome != nil ? 4 : 0)
                .onChanged { value in update(location: value.location) }
                .onEnded { _ in reset() }
        )
    }

    private func update(location: CGPoint) {
        let center = CGPoint(x: diameter / 2, y: diameter / 2)
        let raw = CGSize(width: location.x - center.x, height: location.y - center.y)
        let clamped = raw.clamped(to: commandRadius)
        knobOffset = clamped

        let nextPan = Double(clamped.width / commandRadius).zeroed(deadzone: deadzone)
        let nextTilt = Double(-clamped.height / commandRadius).zeroed(deadzone: deadzone)
        onCommand(nextPan, nextTilt)
    }

    private func reset() {
        knobOffset = .zero
        onStop()
    }
}

private struct JoystickLabels: View {
    let diameter: CGFloat

    var body: some View {
        ZStack {
            Text("TILT +")
                .offset(y: -diameter * 0.44)
            Text("TILT -")
                .offset(y: diameter * 0.44)
            Text("PAN -")
                .offset(x: -diameter * 0.43)
            Text("PAN +")
                .offset(x: diameter * 0.43)
        }
        .font(.system(size: 9, weight: .semibold))
        .tracking(1.3)
        .foregroundStyle(WC.faint)
    }
}

private struct JoystickNub: View {
    let size: CGFloat
    var onHome: (() -> Void)? = nil

    var body: some View {
        ZStack {
            Circle()
                .fill(
                    RadialGradient(
                        colors: [Color(hex: 0xFF8A4D), Color(hex: 0xE2540F)],
                        center: .top,
                        startRadius: 4,
                        endRadius: 42
                    )
                )
            Circle()
                .stroke(Color.white.opacity(0.18), lineWidth: 1)
            if onHome != nil {
                // Home affordance ring + icon (only shown in overlay/home-capable mode)
                Circle()
                    .stroke(Color.white.opacity(0.55), lineWidth: 1.5)
                    .frame(width: size * 0.26, height: size * 0.26)
                Image(systemName: "house")
                    .font(.system(size: size * 0.18, weight: .semibold))
                    .foregroundStyle(Color.white.opacity(0.72))
            } else {
                Circle()
                    .stroke(Color.white.opacity(0.38), lineWidth: 1)
                    .frame(width: size * 0.19, height: size * 0.19)
            }
        }
        .frame(width: size, height: size)
        .shadow(color: WC.brand.opacity(0.45), radius: 18, y: 8)
        .onTapGesture { onHome?() }
        .onLongPressGesture(minimumDuration: 0.4) { onHome?() }
        .accessibilityLabel(onHome != nil ? "Home camera" : "Joystick nub")
        .accessibilityHint(onHome != nil ? "Tap or hold to return camera to home position" : "")
    }
}

private struct PTZReadoutCell: View {
    let label: String
    let value: String
    var tint: Color = WC.txt

    var body: some View {
        VStack(spacing: 5) {
            Text(label)
                .font(.system(size: 9, weight: .semibold))
                .tracking(1.4)
                .foregroundStyle(WC.faint)
            Text(value)
                .font(.system(size: 17, weight: .semibold, design: .monospaced))
                .foregroundStyle(tint)
                .lineLimit(1)
                .minimumScaleFactor(0.64)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 10)
        .background(WC.ink, in: .rect(cornerRadius: 13))
        .overlay(RoundedRectangle(cornerRadius: 13).stroke(WC.line))
    }
}

struct PTZZoomCard: View {
    @Binding var zoomCommand: Double

    var body: some View {
        VStack(spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 5) {
                    Text("ZOOM")
                        .font(.system(size: 10, weight: .semibold))
                        .tracking(1.4)
                        .foregroundStyle(WC.muted)
                    Text(zoomCommandLabel)
                        .font(.system(size: 18, weight: .semibold, design: .monospaced))
                        .foregroundStyle(WC.brand)
                }
                Spacer()
                Button {
                    zoomCommand = 0
                } label: {
                    Image(systemName: "pause.fill")
                        .font(.system(size: 13, weight: .bold))
                        .frame(width: 44, height: 44)
                }
                .buttonStyle(.plain)
                .foregroundStyle(WC.txt)
                .background(WC.panel2, in: .rect(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(WC.line))
                .accessibilityLabel("Hold zoom")
            }

            Slider(value: $zoomCommand, in: -1...1, step: 0.05)
                .tint(WC.brand)

            HStack {
                Text("OUT")
                Spacer()
                Text("HOLD")
                Spacer()
                Text("IN")
            }
            .font(.system(size: 9, weight: .semibold))
            .tracking(1.2)
            .foregroundStyle(WC.faint)
        }
        .padding(14)
        .background(WC.panel, in: .rect(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).stroke(WC.line))
    }

    private var zoomCommandLabel: String {
        if zoomCommand > 0 {
            return "IN \(zoomCommand.signedPTZ)"
        }
        if zoomCommand < 0 {
            return "OUT \(zoomCommand.signedPTZ)"
        }
        return "HOLD"
    }
}

struct PTZActionRow: View {
    let isAuto: Bool
    let isStopped: Bool
    var compact = false
    let onStartAuto: () -> Void
    let onStop: () -> Void
    let onRefresh: () -> Void

    var body: some View {
        Group {
            if compact {
                VStack(spacing: 8) {
                    startAutoButton
                    stopButton
                    refreshButton
                }
            } else {
                HStack(spacing: 8) {
                    startAutoButton
                    stopButton
                    refreshButton
                }
            }
        }
    }

    private var startAutoButton: some View {
        Button {
            onStartAuto()
        } label: {
            Label("Start Auto", systemImage: "play.fill")
        }
        .buttonStyle(PTZActionButtonStyle(tint: WC.ok, filled: isAuto && !isStopped))
    }

    private var stopButton: some View {
        Button {
            onStop()
        } label: {
            Label("Stop PTZ", systemImage: "stop.fill")
        }
        .buttonStyle(PTZActionButtonStyle(tint: WC.kill, filled: isStopped))
    }

    private var refreshButton: some View {
        Button {
            onRefresh()
        } label: {
            Label("Refresh", systemImage: "arrow.clockwise")
        }
        .buttonStyle(PTZActionButtonStyle(tint: WC.ok, filled: false))
    }
}

struct PTZControlFeedback: View {
    let commandState: PTZCommandState
    let controlError: String?
    let refusalText: String?

    var body: some View {
        if let controlError {
            PTZFeedbackPill(text: controlError, color: WC.warn, icon: "exclamationmark.triangle.fill")
        } else if let refusalText {
            PTZFeedbackPill(text: refusalText, color: WC.warn, icon: "exclamationmark.triangle.fill")
        } else if commandState == .stopping {
            PTZFeedbackPill(text: "Stopping PTZ...", color: WC.kill, icon: "stop.fill")
        } else if commandState == .startingAuto {
            PTZFeedbackPill(text: "Starting Auto PTZ...", color: WC.ok, icon: "play.fill")
        } else if commandState == .held {
            PTZFeedbackPill(text: "Backend confirms PTZ hold. Tap Start Auto to resume.", color: WC.kill, icon: "stop.fill")
        } else if commandState == .auto {
            PTZFeedbackPill(text: "Backend confirms Auto PTZ.", color: WC.ok, icon: "play.fill")
        }
    }
}

struct PTZFeedbackPill: View {
    let text: String
    let color: Color
    let icon: String

    var body: some View {
        HStack(spacing: 7) {
            Image(systemName: icon)
                .font(.system(size: 10, weight: .bold))
            Text(text)
                .font(.system(size: 11, weight: .semibold))
                .lineLimit(2)
                .minimumScaleFactor(0.8)
        }
        .foregroundStyle(color)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .background(color.opacity(0.14), in: .rect(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(color.opacity(0.35)))
    }
}

struct PTZActionButtonStyle: ButtonStyle {
    let tint: Color
    let filled: Bool

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 13, weight: .semibold))
            .lineLimit(1)
            .minimumScaleFactor(0.72)
            .foregroundStyle(filled ? Color.black : tint)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 12)
            .background(filled ? tint : WC.panel2, in: .rect(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(filled ? tint.opacity(0.7) : WC.line))
            .opacity(configuration.isPressed ? 0.74 : 1)
    }
}

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
