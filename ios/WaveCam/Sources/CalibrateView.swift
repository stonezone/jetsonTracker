import SwiftUI

/// Calibration wizard: preflight, base lock, heading, tilt, zoom/FOV, and dry-run.
///
/// Feature-detect: probes GET /api/v1/calibration on appear. When the endpoint is absent
/// (backend not yet deployed) the wizard enters `.unavailable` mode — steps are still
/// navigable as a checklist but no capture is sent and no green "DONE" mark appears.
/// When the backend is updated, capture activates automatically on the next appearance.
struct CalibrateView: View {
    @Environment(WaveCamClient.self) private var client
    @State private var activeStepID = CalibrationStep.preflight.id
    @State private var capturedStepIDs: Set<Int> = []
    @State private var persistedState: WCCalibrationState? = nil

    /// nil = probing, true = endpoint present, false = backend not yet deployed
    @State private var calibrationAvailable: Bool? = nil

    /// Populated when the most recent capture was refused (killed, owner_busy, etc).
    @State private var refusalMessage: String? = nil

    /// True while a capture POST is in-flight for the active step.
    @State private var isCaptureInFlight = false

    private var activeStep: CalibrationStep {
        CalibrationStep.all.first { $0.id == activeStepID } ?? .preflight
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                if calibrationAvailable == false {
                    CalibrationUnavailableBanner()
                }
                CalibrationStatusStrip(
                    status: client.status,
                    persistedState: persistedState
                )
                CalibrationStepsCard(
                    activeStepID: activeStepID,
                    capturedStepIDs: capturedStepIDs,
                    onSelect: selectStep
                )
                CalibrationActiveCard(
                    step: activeStep,
                    canGoBack: activeStepID > CalibrationStep.preflight.id,
                    canGoForward: activeStepID < CalibrationStep.dryRun.id
                        && capturedStepIDs.contains(activeStepID),
                    isCaptured: capturedStepIDs.contains(activeStepID),
                    isCaptureInFlight: isCaptureInFlight,
                    calibrationAvailable: calibrationAvailable,
                    refusalMessage: refusalMessage,
                    onBack: moveBack,
                    onCapture: captureActiveStep,
                    onForward: moveForward,
                    onDismissRefusal: { refusalMessage = nil }
                )
            }
            .padding(.horizontal, 16)
            .padding(.top, 6)
            .padding(.bottom, 96)
        }
        .background(WC.bg.ignoresSafeArea())
        .scrollIndicators(.hidden)
        .task {
            await client.refresh()
            await probeCalibrationEndpoint()
        }
    }

    // MARK: - Endpoint probe

    private func probeCalibrationEndpoint() async {
        let available = await client.calibrationAvailable()
        calibrationAvailable = available
        if available == true {
            persistedState = await client.calibrationState()
        }
    }

    // MARK: - Step navigation

    private func selectStep(_ step: CalibrationStep) {
        refusalMessage = nil
        activeStepID = step.id
    }

    private func moveBack() {
        refusalMessage = nil
        activeStepID = max(CalibrationStep.preflight.id, activeStepID - 1)
    }

    private func moveForward() {
        refusalMessage = nil
        activeStepID = min(CalibrationStep.dryRun.id, activeStepID + 1)
    }

    // MARK: - Capture dispatch

    private func captureActiveStep() {
        // Steps without a backend endpoint (preflight, dryRun) advance locally.
        let localSteps: Set<Int> = [
            CalibrationStep.preflight.id,
            CalibrationStep.dryRun.id
        ]
        if localSteps.contains(activeStepID) {
            capturedStepIDs.insert(activeStepID)
            if activeStepID < CalibrationStep.dryRun.id { activeStepID += 1 }
            return
        }

        // When the backend is not yet deployed, treat as a checklist-only confirm.
        if calibrationAvailable == false {
            capturedStepIDs.insert(activeStepID)
            if activeStepID < CalibrationStep.dryRun.id { activeStepID += 1 }
            return
        }

        // Heading is the aim-at-remote capture: it pairs the pan-motor position with
        // the GPS base→remote bearing, so it needs a live bearing (base + remote fixes).
        // Never capture the 0° fallback — that would write a bogus reference_heading.
        if activeStepID == CalibrationStep.heading.id,
           client.status?.gps?.bearingDeg == nil {
            refusalMessage = "GPS bearing needed — aim at the remote and wait for base + remote GPS fixes (the GPS chip shows distance + bearing)."
            return
        }

        // Backend is available (or still probing) — send the real POST.
        Task { await performCapture() }
    }

    private func performCapture() async {
        guard !isCaptureInFlight else { return }
        isCaptureInFlight = true
        refusalMessage = nil
        defer { isCaptureInFlight = false }

        let result: Result<WCCalibrationState, WaveCamCalibrationError>

        switch activeStepID {
        case CalibrationStep.baseLock.id:
            result = await client.captureCalibrationBaseLock()
        case CalibrationStep.heading.id:
            result = await client.captureCalibrationHeading(headingDeg: resolvedHeadingDeg())
        case CalibrationStep.tilt.id:
            result = await client.captureCalibrationTilt(tiltDeg: resolvedTiltDeg())
        case CalibrationStep.zoom.id:
            result = await client.captureCalibrationZoom(zoomFovDeg: resolvedZoomFovDeg())
        default:
            // Should not reach here — local steps short-circuit above.
            capturedStepIDs.insert(activeStepID)
            return
        }

        switch result {
        case let .success(state):
            persistedState = state
            capturedStepIDs.insert(activeStepID)
            if activeStepID < CalibrationStep.dryRun.id { activeStepID += 1 }
        case let .failure(error):
            refusalMessage = error.localizedDescription
        }
    }

    // MARK: - Capture value resolution
    //
    // The operator aims the camera manually; the backend reads the pan-motor position
    // directly from the PTZ. The iOS app supplies the reference angle for each step.
    //
    // Heading is the real aim-at-remote value — the GPS base→remote bearing (the
    // capture is gated on it being present in captureActiveStep, so the 0.0 fallback
    // never reaches the backend). Tilt/zoom remain canonical anchors (level horizon /
    // wide FOV) until their own captures are wired.

    private func resolvedHeadingDeg() -> Double {
        // GPS base→remote bearing — captureActiveStep gates on this being non-nil.
        client.status?.gps?.bearingDeg ?? 0.0
    }

    private func resolvedTiltDeg() -> Double {
        // Level horizon reference — 0 deg tilt is the canonical level-reference capture.
        0.0
    }

    private func resolvedZoomFovDeg() -> Double {
        // Wide-angle capture for the FOV-curve anchor point.
        31.5
    }
}
// MARK: - Unavailable banner

private struct CalibrationUnavailableBanner: View {
    var body: some View {
        OperatorNotice(
            "On-device calibration requires the latest Orin build — checklist only for now.",
            tint: WC.warn
        )
    }
}

// MARK: - Step model

private struct CalibrationStep: Identifiable, Equatable {
    let id: Int
    let title: String
    let headline: String
    let detail: String
    let actionTitle: String
    let systemImage: String

    static let preflight = CalibrationStep(
        id: 1,
        title: "Preflight checks",
        headline: "Confirm camera and network",
        detail: "Verify the camera feed, PTZ link, GPS source, storage, and safety latch before alignment begins.",
        actionTitle: "Confirm preflight",
        systemImage: "checklist"
    )

    static let baseLock = CalibrationStep(
        id: 2,
        title: "Base lock (GPS)",
        headline: "Lock the base location",
        detail: "Latches the base GPS position as the camera reference. Needs the base tracker to have a fix — watch for the Base fix line on the GPS chip first.",
        actionTitle: "Capture base lock",
        systemImage: "location.fill"
    )

    static let heading = CalibrationStep(
        id: 3,
        title: "Heading — aim at remote",
        headline: "Aim the camera at the remote tracker",
        detail: "Place the LoRa remote where you can see it, center the camera on it, then capture. WaveCam reads the pan-motor position and pairs it with the GPS base→remote bearing to solve reference_heading — no magnetometer. Needs base + remote GPS fixes (the GPS chip shows distance + bearing).",
        actionTitle: "Capture heading",
        systemImage: "safari.fill"
    )

    static let tilt = CalibrationStep(
        id: 4,
        title: "Tilt reference",
        headline: "Capture a level reference",
        detail: "Aim at a stable horizon or known-height reference so the tracker can map target elevation into camera tilt.",
        actionTitle: "Capture tilt",
        systemImage: "arrow.up.and.down"
    )

    static let zoom = CalibrationStep(
        id: 5,
        title: "Zoom / FOV curve",
        headline: "Map zoom to field of view",
        detail: "Sample wide, mid, and tele positions so the tracker can estimate box size and vision confidence at each zoom state.",
        actionTitle: "Capture zoom",
        systemImage: "plus.magnifyingglass"
    )

    static let dryRun = CalibrationStep(
        id: 6,
        title: "Dry-run",
        headline: "Run without recording",
        detail: "Exercise GPS pointing, vision lock, and PTZ authority while recording stays optional and the stop latch remains visible.",
        actionTitle: "Mark ready",
        systemImage: "play.circle.fill"
    )

    static let all: [CalibrationStep] = [.preflight, .baseLock, .heading, .tilt, .zoom, .dryRun]
}

// MARK: - Status strip

private struct CalibrationStatusStrip: View {
    let status: WCStatus?
    let persistedState: WCCalibrationState?

    private var gpsText: String {
        guard let distance = status?.gps?.distanceM else { return "UNKNOWN" }
        return "\(Int(distance.rounded()))m"
    }

    private var refHeadingText: String {
        guard let deg = persistedState?.referenceHeading else { return "—" }
        return String(format: "%.1f°", deg)
    }

    var body: some View {
        HStack(spacing: 8) {
            OperatorMetric(label: "SESSION", value: status?.session.state ?? "READY", tint: WC.ok, cornerRadius: WCRadius.sm, uppercaseValue: false)
            OperatorMetric(label: "GPS", value: gpsText, tint: WC.ok, cornerRadius: WCRadius.sm, uppercaseValue: false)
            OperatorMetric(label: "OWNER", value: status?.ptz.owner.ptzOwnerLabel ?? "IDLE", tint: WC.brand, cornerRadius: WCRadius.sm, uppercaseValue: false)
            OperatorMetric(label: "REF HDG", value: refHeadingText, tint: WC.muted, cornerRadius: WCRadius.sm, uppercaseValue: false)
        }
    }
}

// MARK: - Steps list card

private struct CalibrationStepsCard: View {
    let activeStepID: Int
    let capturedStepIDs: Set<Int>
    let onSelect: (CalibrationStep) -> Void

    var body: some View {
        GlassCard(cornerRadius: WCRadius.lg, padding: WCSpace.sm) {
            VStack(spacing: WCSpace.sm) {
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
        }
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
        HStack(spacing: WCSpace.md) {
            StepBadge(stepNumber: step.id, state: state)
            Text(step.title)
                .font(WCFont.bodyBold)
                .foregroundStyle(WC.txt)
                .lineLimit(1)
                .minimumScaleFactor(0.72)
            Spacer(minLength: WCSpace.sm)
            GlassChip(text: statusText, color: statusColor)
        }
        .padding(.horizontal, WCSpace.md - 1)
        .padding(.vertical, WCSpace.md - 1)
        .background(rowBackground, in: .rect(cornerRadius: WCRadius.sm))
        .overlay(RoundedRectangle(cornerRadius: WCRadius.sm).stroke(rowStroke))
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
        case .active: WC.accent
        case .pending: WC.faint
        }
    }

    private var rowBackground: Color {
        state == .active ? WC.accent.opacity(0.1) : WC.ink
    }

    private var rowStroke: Color {
        state == .active ? WC.accent.opacity(0.55) : WC.line
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
                    .foregroundStyle(state == .active ? WC.accent : WC.faint)
            }
        }
        .frame(width: 28, height: 28)
    }

    private var fill: Color {
        switch state {
        case .done: WC.ok
        case .active: WC.accent.opacity(0.12)
        case .pending: Color.clear
        }
    }

    private var stroke: Color {
        switch state {
        case .done: Color.clear
        case .active: WC.accent
        case .pending: WC.line
        }
    }
}

// MARK: - Active step card

private struct CalibrationActiveCard: View {
    let step: CalibrationStep
    let canGoBack: Bool
    let canGoForward: Bool
    let isCaptured: Bool
    let isCaptureInFlight: Bool
    /// nil = still probing; false = unavailable; true = live
    let calibrationAvailable: Bool?
    let refusalMessage: String?
    let onBack: () -> Void
    let onCapture: () -> Void
    let onForward: () -> Void
    let onDismissRefusal: () -> Void

    /// Steps whose capture is local-only (no backend POST).
    private static let localStepIDs: Set<Int> = [
        CalibrationStep.preflight.id,
        CalibrationStep.baseLock.id,
        CalibrationStep.dryRun.id
    ]

    private var isLocalStep: Bool { Self.localStepIDs.contains(step.id) }

    /// True when the capture button should show a "not wired yet" indicator.
    private var showsChecklistMode: Bool {
        !isLocalStep && calibrationAvailable == false
    }

    var body: some View {
        GlassCard(cornerRadius: WCRadius.lg, padding: WCSpace.md) {
            VStack(alignment: .leading, spacing: WCSpace.md) {
                HStack(spacing: WCSpace.sm) {
                    Image(systemName: step.systemImage)
                        .font(.system(size: 19, weight: .semibold))
                        .foregroundStyle(WC.accent)
                        .frame(width: 38, height: 38)
                        .background(WC.accent.opacity(0.12), in: .rect(cornerRadius: WCRadius.sm))
                    VStack(alignment: .leading, spacing: WCSpace.xs) {
                        Text("STEP \(step.id) OF \(CalibrationStep.all.count)")
                            .font(WCFont.label)
                            .tracking(1.4)
                            .foregroundStyle(WC.muted)
                        Text(step.headline)
                            .font(WCFont.title)
                            .foregroundStyle(WC.txt)
                            .lineLimit(2)
                            .minimumScaleFactor(0.78)
                    }
                }

                Text(step.detail)
                    .font(WCFont.body)
                    .foregroundStyle(WC.muted)
                    .lineSpacing(4)

                // Refusal message strip — appears only when a capture was refused.
                if let msg = refusalMessage {
                    OperatorNotice(msg, tint: WC.kill)
                        .overlay(alignment: .trailing) {
                            Button(action: onDismissRefusal) {
                                Image(systemName: "xmark")
                                    .font(.system(size: 11, weight: .semibold))
                                    .foregroundStyle(WC.faint)
                                    .frame(width: 36, height: 36)
                            }
                            .buttonStyle(.plain)
                            .padding(.trailing, WCSpace.xs)
                        }
                }

                HStack(spacing: WCSpace.sm) {
                    GlassButton(
                        label: "Back",
                        icon: "chevron.left",
                        role: .normal,
                        disabled: !canGoBack,
                        action: onBack
                    )

                    captureButton

                    GlassButton(
                        label: "Next",
                        icon: "chevron.right",
                        role: .normal,
                        disabled: !canGoForward,
                        action: onForward
                    )
                }
            }
        }
    }

    @ViewBuilder
    private var captureButton: some View {
        if isCaptureInFlight {
            // In-flight: show a teal-active button with a spinner — disabled, non-tappable.
            GlassButton(
                label: "Capturing…",
                role: .active,
                disabled: true,
                action: {}
            )
            // Overlay spinner on top of the GlassButton label area.
            .overlay(alignment: .leading) {
                ProgressView()
                    .tint(Color.black)
                    .scaleEffect(0.72)
                    .padding(.leading, WCSpace.lg)
            }
        } else if showsChecklistMode {
            // Backend not deployed — checklist-only confirm. Do NOT show a green checkmark
            // that implies hardware calibration actually ran.
            GlassButton(
                label: isCaptured ? "Noted (checklist)" : step.actionTitle,
                icon: isCaptured ? "list.bullet.clipboard.fill" : "dot.scope",
                role: isCaptured ? .active : .normal,
                action: onCapture
            )
        } else {
            GlassButton(
                label: isCaptured ? "Captured" : step.actionTitle,
                icon: isCaptured ? "checkmark.circle.fill" : "dot.scope",
                role: isCaptured ? .active : .normal,
                disabled: isCaptureInFlight,
                action: onCapture
            )
        }
    }
}
