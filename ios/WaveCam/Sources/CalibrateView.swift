import SwiftUI

/// Calibration wizard: preflight, base lock, heading, tilt, zoom/FOV, and dry-run.
struct CalibrateView: View {
    @Environment(WaveCamClient.self) private var client
    @State private var activeStepID = CalibrationStep.heading.id
    @State private var capturedStepIDs: Set<Int> = [CalibrationStep.preflight.id, CalibrationStep.baseLock.id]

    private var activeStep: CalibrationStep {
        CalibrationStep.all.first { $0.id == activeStepID } ?? .heading
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                CalibrationStatusStrip(status: client.status)
                CalibrationStepsCard(
                    activeStepID: activeStepID,
                    capturedStepIDs: capturedStepIDs,
                    onSelect: selectStep
                )
                CalibrationActiveCard(
                    step: activeStep,
                    canGoBack: activeStepID > CalibrationStep.preflight.id,
                    canGoForward: activeStepID < CalibrationStep.dryRun.id,
                    isCaptured: capturedStepIDs.contains(activeStepID),
                    onBack: moveBack,
                    onCapture: captureActiveStep,
                    onForward: moveForward
                )
            }
            .padding(.horizontal, 16)
            .padding(.top, 6)
            .padding(.bottom, 96)
        }
        .background(WC.bg.ignoresSafeArea())
        .scrollIndicators(.hidden)
        .task { await client.refresh() }
    }

    private func selectStep(_ step: CalibrationStep) {
        activeStepID = step.id
    }

    private func captureActiveStep() {
        capturedStepIDs.insert(activeStepID)
        if activeStepID < CalibrationStep.dryRun.id {
            activeStepID += 1
        }
    }

    private func moveBack() {
        activeStepID = max(CalibrationStep.preflight.id, activeStepID - 1)
    }

    private func moveForward() {
        activeStepID = min(CalibrationStep.dryRun.id, activeStepID + 1)
    }
}

private struct CalibrationStep: Identifiable, Equatable {
    let id: Int
    let title: String
    let shortStatus: String
    let headline: String
    let detail: String
    let actionTitle: String
    let systemImage: String

    static let preflight = CalibrationStep(
        id: 1,
        title: "Preflight checks",
        shortStatus: "Done",
        headline: "Confirm camera and network",
        detail: "Verify the camera feed, PTZ link, GPS source, storage, and safety latch before alignment begins.",
        actionTitle: "Confirm preflight",
        systemImage: "checklist"
    )

    static let baseLock = CalibrationStep(
        id: 2,
        title: "Base lock (GPS)",
        shortStatus: "Done",
        headline: "Lock the base location",
        detail: "Use the Orin/base position as the fixed reference point before heading and tilt are solved.",
        actionTitle: "Confirm base",
        systemImage: "location.fill"
    )

    static let heading = CalibrationStep(
        id: 3,
        title: "Heading - landmark",
        shortStatus: "Now",
        headline: "Aim at a known landmark",
        detail: "Center the camera on a fixed point you can identify on the map, such as a pier end or channel marker. WaveCam reads motor position and solves reference_heading without a magnetometer.",
        actionTitle: "Capture heading",
        systemImage: "safari.fill"
    )

    static let tilt = CalibrationStep(
        id: 4,
        title: "Tilt reference",
        shortStatus: "Next",
        headline: "Capture a level reference",
        detail: "Aim at a stable horizon or known-height reference so the tracker can map target elevation into camera tilt.",
        actionTitle: "Capture tilt",
        systemImage: "arrow.up.and.down"
    )

    static let zoom = CalibrationStep(
        id: 5,
        title: "Zoom / FOV curve",
        shortStatus: "Next",
        headline: "Map zoom to field of view",
        detail: "Sample wide, mid, and tele positions so the tracker can estimate box size and vision confidence at each zoom state.",
        actionTitle: "Capture zoom",
        systemImage: "plus.magnifyingglass"
    )

    static let dryRun = CalibrationStep(
        id: 6,
        title: "Dry-run",
        shortStatus: "Ready",
        headline: "Run without recording",
        detail: "Exercise GPS pointing, vision lock, and PTZ authority while recording stays optional and the stop latch remains visible.",
        actionTitle: "Mark ready",
        systemImage: "play.circle.fill"
    )

    static let all: [CalibrationStep] = [.preflight, .baseLock, .heading, .tilt, .zoom, .dryRun]
}

private struct CalibrationStatusStrip: View {
    let status: WCStatus?

    private var gpsText: String {
        guard let distance = status?.gps?.distanceM else { return "UNKNOWN" }
        return "\(Int(distance.rounded()))m"
    }

    var body: some View {
        HStack(spacing: 8) {
            CalibrationMetric(label: "SESSION", value: status?.session.state ?? "READY", tint: WC.ok)
            CalibrationMetric(label: "GPS", value: gpsText, tint: WC.ok)
            CalibrationMetric(label: "OWNER", value: status?.ptz.owner.ptzOwnerLabel ?? "IDLE", tint: WC.brand)
        }
    }
}

private struct CalibrationMetric: View {
    let label: String
    let value: String
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(label)
                .font(.system(size: 9, weight: .semibold))
                .tracking(1.3)
                .foregroundStyle(WC.faint)
            Text(value)
                .font(.system(size: 13, weight: .semibold, design: .monospaced))
                .foregroundStyle(tint)
                .lineLimit(1)
                .minimumScaleFactor(0.62)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(WC.panel, in: .rect(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(WC.line))
    }
}

private struct CalibrationStepsCard: View {
    let activeStepID: Int
    let capturedStepIDs: Set<Int>
    let onSelect: (CalibrationStep) -> Void

    var body: some View {
        VStack(spacing: 8) {
            ForEach(CalibrationStep.all) { step in
                Button {
                    onSelect(step)
                } label: {
                    CalibrationStepRow(
                        step: step,
                        state: rowState(for: step)
                    )
                }
                .buttonStyle(.plain)
            }
        }
        .padding(12)
        .background(WC.panel, in: .rect(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).stroke(WC.line))
    }

    private func rowState(for step: CalibrationStep) -> CalibrationStepRow.StateKind {
        if activeStepID == step.id { return .active }
        if capturedStepIDs.contains(step.id) { return .done }
        return .pending
    }
}

private struct CalibrationStepRow: View {
    enum StateKind { case done, active, pending }

    let step: CalibrationStep
    let state: StateKind

    var body: some View {
        HStack(spacing: 12) {
            StepBadge(stepNumber: step.id, state: state)
            Text(step.title)
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(WC.txt)
                .lineLimit(1)
                .minimumScaleFactor(0.72)
            Spacer(minLength: 8)
            Text(statusText)
                .font(.system(size: 10, weight: .semibold))
                .tracking(1.2)
                .foregroundStyle(statusColor)
        }
        .padding(.horizontal, 11)
        .padding(.vertical, 11)
        .background(rowBackground, in: .rect(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(rowStroke))
    }

    private var statusText: String {
        switch state {
        case .done: "DONE"
        case .active: "NOW"
        case .pending: "WAIT"
        }
    }

    private var statusColor: Color {
        switch state {
        case .done: WC.ok
        case .active: WC.brand
        case .pending: WC.faint
        }
    }

    private var rowBackground: Color {
        state == .active ? WC.brand.opacity(0.1) : WC.ink
    }

    private var rowStroke: Color {
        state == .active ? WC.brand.opacity(0.55) : WC.line
    }
}

private struct StepBadge: View {
    let stepNumber: Int
    let state: CalibrationStepRow.StateKind

    var body: some View {
        ZStack {
            Circle()
                .fill(fill)
                .overlay(Circle().stroke(stroke))
            if state == .done {
                Image(systemName: "checkmark")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(Color.black)
            } else {
                Text(stepNumber, format: .number)
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundStyle(state == .active ? WC.brand : WC.faint)
            }
        }
        .frame(width: 28, height: 28)
    }

    private var fill: Color {
        switch state {
        case .done: WC.ok
        case .active: WC.brand.opacity(0.12)
        case .pending: Color.clear
        }
    }

    private var stroke: Color {
        switch state {
        case .done: Color.clear
        case .active: WC.brand
        case .pending: WC.line
        }
    }
}

private struct CalibrationActiveCard: View {
    let step: CalibrationStep
    let canGoBack: Bool
    let canGoForward: Bool
    let isCaptured: Bool
    let onBack: () -> Void
    let onCapture: () -> Void
    let onForward: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                Image(systemName: step.systemImage)
                    .font(.system(size: 19, weight: .semibold))
                    .foregroundStyle(WC.brand)
                    .frame(width: 38, height: 38)
                    .background(WC.brand.opacity(0.12), in: .rect(cornerRadius: 12))
                VStack(alignment: .leading, spacing: 4) {
                    Text("STEP \(step.id)")
                        .font(.system(size: 10, weight: .semibold))
                        .tracking(1.4)
                        .foregroundStyle(WC.faint)
                    Text(step.headline)
                        .font(.system(size: 20, weight: .bold))
                        .foregroundStyle(WC.txt)
                        .lineLimit(2)
                        .minimumScaleFactor(0.78)
                }
            }

            Text(step.detail)
                .font(.system(size: 13))
                .foregroundStyle(WC.muted)
                .lineSpacing(4)

            HStack(spacing: 8) {
                Button {
                    onBack()
                } label: {
                    Label("Back", systemImage: "chevron.left")
                }
                .buttonStyle(CalibrationButtonStyle(tint: WC.muted, filled: false))
                .disabled(!canGoBack)
                .opacity(canGoBack ? 1 : 0.38)

                Button {
                    onCapture()
                } label: {
                    Label(isCaptured ? "Captured" : step.actionTitle, systemImage: isCaptured ? "checkmark.circle.fill" : "dot.scope")
                }
                .buttonStyle(CalibrationButtonStyle(tint: isCaptured ? WC.ok : WC.brand, filled: true))

                Button {
                    onForward()
                } label: {
                    Label("Next", systemImage: "chevron.right")
                }
                .buttonStyle(CalibrationButtonStyle(tint: WC.ok, filled: false))
                .disabled(!canGoForward)
                .opacity(canGoForward ? 1 : 0.38)
            }
        }
        .padding(14)
        .background(WC.panel, in: .rect(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).stroke(WC.line))
    }
}

private struct CalibrationButtonStyle: ButtonStyle {
    let tint: Color
    let filled: Bool

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 12, weight: .semibold))
            .lineLimit(1)
            .minimumScaleFactor(0.62)
            .foregroundStyle(filled ? Color.black : tint)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 12)
            .background(filled ? tint : WC.panel2, in: .rect(cornerRadius: 13))
            .overlay(RoundedRectangle(cornerRadius: 13).stroke(filled ? tint.opacity(0.7) : WC.line))
            .opacity(configuration.isPressed ? 0.76 : 1)
    }
}
