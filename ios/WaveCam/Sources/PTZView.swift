import SwiftUI

/// Manual PTZ screen: velocity joystick, zoom control, command readout, and safety stop.
struct PTZView: View {
    @Environment(WaveCamClient.self) private var client

    @State private var pan: Double = 0
    @State private var tilt: Double = 0
    @State private var knobOffset: CGSize = .zero
    @State private var zoomVelocity: Double = 0

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                PTZHeader(status: client.status)
                PTZJoystickCard(
                    pan: $pan,
                    tilt: $tilt,
                    knobOffset: $knobOffset,
                    owner: client.owner,
                    onCommand: sendVelocity,
                    onStop: stopPTZ
                )
                PTZZoomCard(zoomVelocity: $zoomVelocity)
                    .onChange(of: zoomVelocity) { _, newValue in
                        Task { await client.zoom(newValue) }
                    }
                PTZActionRow(
                    onStop: stopPTZ,
                    onRefresh: { Task { await client.refresh() } }
                )
                PTZEmergencyStopButton()
            }
            .padding(.horizontal, 16)
            .padding(.top, 6)
            .padding(.bottom, 22)
        }
        .background(WC.bg.ignoresSafeArea())
        .scrollIndicators(.hidden)
        .task { await client.refresh() }
    }

    private func sendVelocity(pan: Double, tilt: Double) {
        self.pan = pan
        self.tilt = tilt
        Task { await client.ptzVelocity(pan: pan, tilt: tilt) }
    }

    private func stopPTZ() {
        pan = 0
        tilt = 0
        knobOffset = .zero
        zoomVelocity = 0
        Task {
            await client.ptzStop()
            await client.zoom(0)
        }
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
    let onCommand: (Double, Double) -> Void
    let onStop: () -> Void

    var body: some View {
        VStack(spacing: 12) {
            Text("Manual PTZ - release to stop")
                .font(.system(size: 10, weight: .semibold))
                .tracking(1.4)
                .foregroundStyle(WC.muted)
                .frame(maxWidth: .infinity, alignment: .leading)

            JoystickPad(
                pan: $pan,
                tilt: $tilt,
                knobOffset: $knobOffset,
                onCommand: onCommand,
                onStop: onStop
            )

            HStack(spacing: 8) {
                PTZReadoutCell(label: "PAN", value: pan.signedPTZ)
                PTZReadoutCell(label: "TILT", value: tilt.signedPTZ)
                PTZReadoutCell(label: "OWNER", value: owner.uppercased(), tint: WC.brand)
            }
        }
        .padding(14)
        .background(WC.panel, in: .rect(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).stroke(WC.line))
    }
}

private struct JoystickPad: View {
    @Binding var pan: Double
    @Binding var tilt: Double
    @Binding var knobOffset: CGSize

    let onCommand: (Double, Double) -> Void
    let onStop: () -> Void

    private let diameter: CGFloat = 230
    private let commandRadius: CGFloat = 82
    private let deadzone: Double = 0.05

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
                .padding(30)
            Rectangle()
                .fill(Color.white.opacity(0.08))
                .frame(width: 1, height: diameter - 64)
            Rectangle()
                .fill(Color.white.opacity(0.08))
                .frame(width: diameter - 64, height: 1)
            JoystickLabels()
            JoystickNub()
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
    var body: some View {
        ZStack {
            Text("TILT +")
                .offset(y: -101)
            Text("TILT -")
                .offset(y: 101)
            Text("PAN -")
                .offset(x: -98)
            Text("PAN +")
                .offset(x: 98)
        }
        .font(.system(size: 9, weight: .semibold))
        .tracking(1.3)
        .foregroundStyle(WC.faint)
    }
}

private struct JoystickNub: View {
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
                .frame(width: 14, height: 14)
        }
        .frame(width: 74, height: 74)
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
    @Binding var zoomVelocity: Double

    var body: some View {
        VStack(spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 5) {
                    Text("ZOOM VELOCITY")
                        .font(.system(size: 10, weight: .semibold))
                        .tracking(1.4)
                        .foregroundStyle(WC.muted)
                    Text(zoomVelocityLabel)
                        .font(.system(size: 18, weight: .semibold, design: .monospaced))
                        .foregroundStyle(WC.brand)
                }
                Spacer()
                Button {
                    zoomVelocity = 0
                } label: {
                    Image(systemName: "pause.fill")
                        .font(.system(size: 13, weight: .bold))
                        .frame(width: 38, height: 34)
                }
                .buttonStyle(.plain)
                .foregroundStyle(WC.txt)
                .background(WC.panel2, in: .rect(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(WC.line))
                .accessibilityLabel("Stop zoom")
            }

            Slider(value: $zoomVelocity, in: -1...1, step: 0.05)
                .tint(WC.brand)

            HStack {
                Text("WIDE")
                Spacer()
                Text("HOLD")
                Spacer()
                Text("TELE")
            }
            .font(.system(size: 9, weight: .semibold))
            .tracking(1.2)
            .foregroundStyle(WC.faint)
        }
        .padding(14)
        .background(WC.panel, in: .rect(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).stroke(WC.line))
    }

    private var zoomVelocityLabel: String {
        zoomVelocity.signedPTZ
    }
}

private struct PTZActionRow: View {
    let onStop: () -> Void
    let onRefresh: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Button {
                onStop()
            } label: {
                Label("Stop PTZ", systemImage: "stop.fill")
            }
            .buttonStyle(PTZActionButtonStyle(tint: WC.kill, filled: false))

            Button {
                onRefresh()
            } label: {
                Label("Refresh", systemImage: "arrow.clockwise")
            }
            .buttonStyle(PTZActionButtonStyle(tint: WC.ok, filled: false))
        }
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

private struct PTZEmergencyStopButton: View {
    @Environment(WaveCamClient.self) private var client

    var body: some View {
        Button {
            Task { await client.kill() }
        } label: {
            HStack(spacing: 9) {
                Image(systemName: "stop.fill")
                    .font(.system(size: 13, weight: .black))
                Text("Emergency Stop")
                    .font(.system(size: 16, weight: .black))
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 16)
        }
        .buttonStyle(.plain)
        .foregroundStyle(.white)
        .background(WC.kill, in: .rect(cornerRadius: 16))
        .shadow(color: WC.kill.opacity(0.25), radius: 18, y: 8)
        .accessibilityLabel("Emergency stop")
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
