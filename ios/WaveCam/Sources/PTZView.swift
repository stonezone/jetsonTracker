import SwiftUI

/// Manual PTZ screen: velocity joystick, zoom control, command readout, and safety stop.
struct PTZView: View {
    @Environment(WaveCamClient.self) private var client
    @Environment(\.verticalSizeClass) private var verticalSizeClass

    @State private var pan: Double = 0
    @State private var tilt: Double = 0
    @State private var knobOffset: CGSize = .zero
    @State private var zoomCommand: Double = 0
    @State private var velocityRepeatTask: Task<Void, Never>?
    @State private var zoomRepeatTask: Task<Void, Never>?
    @State private var commandState = PTZCommandState.idle

    private let velocityRepeatIntervalNs: UInt64 = 300_000_000
    private let zoomRepeatIntervalNs: UInt64 = 300_000_000
    private var isLandscapeControl: Bool {
        verticalSizeClass == .compact
    }

    var body: some View {
        ScrollView {
            if isLandscapeControl {
                landscapeControls
            } else {
                portraitControls
            }
        }
        .background(WC.bg.ignoresSafeArea())
        .scrollIndicators(.hidden)
        .task { await client.refresh() }
        .onDisappear {
            stopVelocityRepeat()
            stopZoomCommand()
        }
        .onChange(of: client.status?.revision) { _, _ in
            syncCommandStateWithBackend()
        }
    }

    private var portraitControls: some View {
        VStack(spacing: 12) {
            PTZHeader(status: client.status)
            joystickCard()
            zoomCard()
            actionRow()
            PTZControlFeedback(commandState: commandState, lastError: client.lastError)
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
                PTZControlFeedback(commandState: commandState, lastError: client.lastError)
                EmergencyStopButton(style: .compact)
            }
            .frame(width: 220)
        }
        .padding(.horizontal, 14)
        .padding(.top, 8)
        .padding(.bottom, 12)
    }

    private func joystickCard(joystickSize: CGFloat = 230, compact: Bool = false) -> some View {
        PTZJoystickCard(
            pan: $pan,
            tilt: $tilt,
            knobOffset: $knobOffset,
            owner: client.owner,
            joystickSize: joystickSize,
            compact: compact,
            onCommand: sendVelocity,
            onStop: releaseManualPTZ
        )
    }

    private func zoomCard() -> some View {
        PTZZoomCard(zoomCommand: $zoomCommand)
            .onChange(of: zoomCommand) { _, newValue in
                updateZoom(newValue)
            }
    }

    private func actionRow() -> some View {
        PTZActionRow(
            isAuto: commandState.isAutoActive || client.owner.isAutonomousPTZOwner,
            isStopped: commandState.isStopActive || backendHeldStop,
            onStartAuto: startAutoPTZ,
            onStop: holdPTZ,
            onRefresh: { Task { await client.refresh() } }
        )
    }

    private func sendVelocity(pan: Double, tilt: Double) {
        self.pan = pan
        self.tilt = tilt
        let isActive = pan != 0 || tilt != 0
        commandState = isActive ? .manual : .idle
        Task { await client.ptzVelocity(pan: pan, tilt: tilt) }
        if isActive {
            startVelocityRepeat()
        } else {
            stopVelocityRepeat()
        }
    }

    private func releaseManualPTZ() {
        stopVelocityRepeat()
        pan = 0
        tilt = 0
        knobOffset = .zero
        if commandState != .held {
            commandState = .idle
        }
        Task { await client.ptzStop(hold: false) }
    }

    private func holdPTZ() {
        stopVelocityRepeat()
        pan = 0
        tilt = 0
        knobOffset = .zero
        zoomCommand = 0
        commandState = .stopping
        zoomRepeatTask?.cancel()
        zoomRepeatTask = nil
        Task { @MainActor in
            let accepted = await client.ptzStop(hold: true)
            commandState = accepted ? .held : .idle
            syncCommandStateWithBackend()
        }
    }

    private func startAutoPTZ() {
        stopVelocityRepeat()
        pan = 0
        tilt = 0
        knobOffset = .zero
        zoomCommand = 0
        commandState = .startingAuto
        zoomRepeatTask?.cancel()
        zoomRepeatTask = nil
        Task { @MainActor in
            let accepted = await client.ptzStartAuto()
            commandState = accepted ? .auto : .idle
            syncCommandStateWithBackend()
        }
    }

    private var backendHeldStop: Bool {
        guard let ptz = client.status?.ptz else { return false }
        return ptz.owner == "manual" && ptz.panTiltCmd?.lowercased() == "stop"
    }

    private func syncCommandStateWithBackend() {
        guard !commandState.isPending else { return }
        if client.owner.isAutonomousPTZOwner {
            commandState = .auto
        } else if backendHeldStop {
            commandState = .held
        } else if commandState == .auto || commandState == .held {
            commandState = .idle
        }
    }

    private func startVelocityRepeat() {
        guard velocityRepeatTask == nil else { return }
        velocityRepeatTask = Task { @MainActor in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: velocityRepeatIntervalNs)
                guard !Task.isCancelled else { return }
                guard pan != 0 || tilt != 0 else {
                    velocityRepeatTask = nil
                    return
                }
                await client.ptzVelocity(pan: pan, tilt: tilt)
            }
        }
    }

    private func stopVelocityRepeat() {
        velocityRepeatTask?.cancel()
        velocityRepeatTask = nil
    }

    private func updateZoom(_ value: Double) {
        zoomRepeatTask?.cancel()
        zoomRepeatTask = nil

        if value != 0 {
            commandState = .manual
        }
        Task { await client.zoom(value) }
        guard value != 0 else { return }

        // The backend has a manual deadman timer, so nonzero zoom must be refreshed
        // while the control remains away from HOLD.
        zoomRepeatTask = Task {
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: zoomRepeatIntervalNs)
                guard !Task.isCancelled else { return }
                await client.zoom(value)
            }
        }
    }

    private func stopZoomCommand() {
        zoomRepeatTask?.cancel()
        zoomRepeatTask = nil
        zoomCommand = 0
        Task { await client.zoom(0) }
    }
}

private enum PTZCommandState {
    case idle
    case manual
    case held
    case auto
    case stopping
    case startingAuto

    var isPending: Bool {
        self == .stopping || self == .startingAuto
    }

    var isAutoActive: Bool {
        self == .auto || self == .startingAuto
    }

    var isStopActive: Bool {
        self == .held || self == .stopping
    }
}

private struct PTZHeader: View {
    let status: WCStatus?

    private var mode: String { status?.session.mode ?? "manual" }
    private var state: String { status?.session.state ?? "READY" }
    private var command: String { status?.ptz.panTiltCmd ?? "p0/t0" }

    var body: some View {
        HStack(spacing: 8) {
            PTZStatusPill(label: "STATE", value: state, color: state == "KILLED" ? WC.kill : WC.ok)
            PTZStatusPill(label: "MODE", value: mode, color: WC.brand)
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
    @Binding var pan: Double
    @Binding var tilt: Double
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
                    Text("OWNER \(owner.uppercased())")
                        .font(.system(size: 9, weight: .semibold, design: .monospaced))
                        .tracking(1.1)
                        .foregroundStyle(WC.brand)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            JoystickPad(
                pan: $pan,
                tilt: $tilt,
                knobOffset: $knobOffset,
                diameter: joystickSize,
                onCommand: onCommand,
                onStop: onStop
            )

            if !compact {
                HStack(spacing: 8) {
                    PTZReadoutCell(label: "PAN", value: pan.signedPTZ)
                    PTZReadoutCell(label: "TILT", value: tilt.signedPTZ)
                    PTZReadoutCell(label: "OWNER", value: owner.uppercased(), tint: WC.brand)
                }
            }
        }
        .padding(compact ? 12 : 14)
        .background(WC.panel, in: .rect(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).stroke(WC.line))
    }
}

private struct JoystickPad: View {
    @Binding var pan: Double
    @Binding var tilt: Double
    @Binding var knobOffset: CGSize

    let diameter: CGFloat
    let onCommand: (Double, Double) -> Void
    let onStop: () -> Void

    private let deadzone: Double = 0.05
    private var commandRadius: CGFloat {
        diameter * 0.3565
    }

    var body: some View {
        ZStack {
            Circle()
                .fill(
                    RadialGradient(
                        colors: [Color(hex: 0x1A2730), Color(hex: 0x0E161D)],
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
            JoystickNub(size: diameter * 0.32)
                .offset(knobOffset)
        }
        .frame(width: diameter, height: diameter)
        .contentShape(Circle())
        .gesture(
            DragGesture(minimumDistance: 0)
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
        pan = nextPan
        tilt = nextTilt
        onCommand(nextPan, nextTilt)
    }

    private func reset() {
        pan = 0
        tilt = 0
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
            Circle()
                .stroke(Color.white.opacity(0.38), lineWidth: 1)
                .frame(width: size * 0.19, height: size * 0.19)
        }
        .frame(width: size, height: size)
        .shadow(color: WC.brand.opacity(0.45), radius: 18, y: 8)
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

private struct PTZZoomCard: View {
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
                        .frame(width: 38, height: 34)
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
            return "OUT \(abs(zoomCommand).formatted(.number.precision(.fractionLength(2))))"
        }
        return "HOLD"
    }
}

private struct PTZActionRow: View {
    let isAuto: Bool
    let isStopped: Bool
    let onStartAuto: () -> Void
    let onStop: () -> Void
    let onRefresh: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Button {
                onStartAuto()
            } label: {
                Label("Start Auto", systemImage: "play.fill")
            }
            .buttonStyle(PTZActionButtonStyle(tint: WC.ok, filled: isAuto && !isStopped))

            Button {
                onStop()
            } label: {
                Label("Stop PTZ", systemImage: "stop.fill")
            }
            .buttonStyle(PTZActionButtonStyle(tint: WC.kill, filled: isStopped))

            Button {
                onRefresh()
            } label: {
                Label("Refresh", systemImage: "arrow.clockwise")
            }
            .buttonStyle(PTZActionButtonStyle(tint: WC.ok, filled: false))
        }
    }
}

private struct PTZControlFeedback: View {
    let commandState: PTZCommandState
    let lastError: String?

    var body: some View {
        if let lastError {
            PTZFeedbackPill(text: lastError, color: WC.warn, icon: "exclamationmark.triangle.fill")
        } else if commandState == .stopping {
            PTZFeedbackPill(text: "Stopping PTZ...", color: WC.kill, icon: "stop.fill")
        } else if commandState == .startingAuto {
            PTZFeedbackPill(text: "Starting Auto PTZ...", color: WC.ok, icon: "play.fill")
        } else if commandState == .held {
            PTZFeedbackPill(text: "PTZ held. Tap Start Auto to resume tracking.", color: WC.kill, icon: "stop.fill")
        } else if commandState == .auto {
            PTZFeedbackPill(text: "Auto PTZ active.", color: WC.ok, icon: "play.fill")
        }
    }
}

private struct PTZFeedbackPill: View {
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

private struct PTZActionButtonStyle: ButtonStyle {
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

private extension String {
    var isAutonomousPTZOwner: Bool {
        self == "testbed" || self == "vision_follow" || self == "gps_tracker"
    }
}
