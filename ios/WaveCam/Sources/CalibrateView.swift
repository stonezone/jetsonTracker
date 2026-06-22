import SwiftUI

// MARK: - Wizard root

/// CALIBRATE wizard — location + heading + validation flow driven by PR #88 backend.
///
/// Feature-detect: probes `calibrateSessionAvailable()` on appear. When the PR #88
/// endpoints are absent the view falls back to the legacy checklist-only mode
/// (existing behaviour preserved). When the new endpoints are live the full wizard
/// replaces the step list.
///
/// KILL / EmergencyStop: always reachable via the top-bar chip (ContentView) and the
/// banner visible at the top of this scroll view throughout the session.
struct CalibrateView: View {
    @Environment(WaveCamClient.self) private var client

    // MARK: state

    /// nil = probing, true = PR #88 endpoints live (renders CalibrateScreenV3), false = legacy mode
    @State private var sessionAvailable: Bool? = nil

    // Legacy (checklist-only) state — preserved from the original CalibrateView
    @State private var activeStepID = LegacyStep.preflight.id
    @State private var capturedStepIDs: Set<Int> = []
    @State private var legacyCalibrationState: WCCalibrationState? = nil
    @State private var legacyCaptureInFlight = false
    @State private var legacyRefusal: String? = nil

    var body: some View {
        // v3 single-screen (no modal sheets, pinned Exit/KILL) on PR #88+ backends;
        // confirmed-legacy backends keep the checklist fallback. While probing (nil) we
        // optimistically show v3 — the live rig supports it.
        Group {
            if sessionAvailable == false {
                legacyScrollBody
            } else {
                CalibrateScreenV3()
            }
        }
        .task {
            await client.refresh()
            await probeSessionEndpoint()
        }
    }

    // MARK: - Legacy checklist fallback (pre-PR #88 backends)

    private var legacyScrollBody: some View {
        ScrollView {
            VStack(spacing: 12) {
                CalibrationUnavailableBanner()
                legacyBody
            }
            .padding(.horizontal, 16)
            .padding(.top, 6)
            .padding(.bottom, 96)
        }
        .background(WC.bg.ignoresSafeArea())
        .scrollIndicators(.hidden)
    }


    // MARK: - Legacy body (checklist, no PR #88)

    @ViewBuilder
    private var legacyBody: some View {
        let available = sessionAvailable

        LegacyStatusStrip(
            status: client.status,
            persistedState: legacyCalibrationState
        )
        LegacyStepsCard(
            activeStepID: activeStepID,
            capturedStepIDs: capturedStepIDs,
            onSelect: { step in
                legacyRefusal = nil
                activeStepID = step.id
            }
        )
        LegacyActiveCard(
            step: LegacyStep.all.first { $0.id == activeStepID } ?? .preflight,
            canGoBack: activeStepID > LegacyStep.preflight.id,
            canGoForward: activeStepID < LegacyStep.dryRun.id
                && capturedStepIDs.contains(activeStepID),
            isCaptured: capturedStepIDs.contains(activeStepID),
            isCaptureInFlight: legacyCaptureInFlight,
            calibrationAvailable: available,
            refusalMessage: legacyRefusal,
            onBack: {
                legacyRefusal = nil
                activeStepID = max(LegacyStep.preflight.id, activeStepID - 1)
            },
            onCapture: { Task { await legacyCaptureActiveStep() } },
            onForward: {
                legacyRefusal = nil
                activeStepID = min(LegacyStep.dryRun.id, activeStepID + 1)
            },
            onDismissRefusal: { legacyRefusal = nil }
        )
    }

    // MARK: - Endpoint probe

    private func probeSessionEndpoint() async {
        let available = await client.calibrateSessionAvailable()
        sessionAvailable = available
        if available != true {
            legacyCalibrationState = await client.calibrationState()
        }
    }


    // MARK: - Legacy capture (preserved from original CalibrateView)

    private func legacyCaptureActiveStep() async {
        let localSteps: Set<Int> = [LegacyStep.preflight.id, LegacyStep.dryRun.id]
        if localSteps.contains(activeStepID) || sessionAvailable == false {
            capturedStepIDs.insert(activeStepID)
            if activeStepID < LegacyStep.dryRun.id { activeStepID += 1 }
            return
        }
        if activeStepID == LegacyStep.heading.id,
           client.status?.gps?.bearingDeg == nil {
            legacyRefusal = "GPS bearing needed — aim at the remote and wait for base + remote GPS fixes."
            return
        }
        guard !legacyCaptureInFlight else { return }
        legacyCaptureInFlight = true
        legacyRefusal = nil
        defer { legacyCaptureInFlight = false }

        let result: Result<WCCalibrationState, WaveCamCalibrationError>
        switch activeStepID {
        case LegacyStep.baseLock.id:
            result = await client.captureCalibrationBaseLock()
        case LegacyStep.heading.id:
            result = await client.captureCalibrationHeading(headingDeg: client.status?.gps?.bearingDeg ?? 0.0)
        case LegacyStep.tilt.id:
            result = await client.captureCalibrationTilt(tiltDeg: 0.0)
        case LegacyStep.zoom.id:
            result = await client.captureCalibrationZoom(zoomFovDeg: 31.5)
        default:
            capturedStepIDs.insert(activeStepID)
            return
        }
        switch result {
        case let .success(state):
            legacyCalibrationState = state
            capturedStepIDs.insert(activeStepID)
            if activeStepID < LegacyStep.dryRun.id { activeStepID += 1 }
        case let .failure(error):
            legacyRefusal = error.localizedDescription
        }
    }
}


// MARK: - Shared layout helpers (file-private, prefixed to avoid conflicts)

@ViewBuilder
private func calRefusalRow(_ message: String?, onDismiss: @escaping () -> Void) -> some View {
    if let msg = message {
        OperatorNotice(msg, tint: WC.kill)
            .overlay(alignment: .trailing) {
                Button(action: onDismiss) {
                    Image(systemName: "xmark")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(WC.faint)
                        .frame(width: 36, height: 36)
                }
                .buttonStyle(.plain)
                .padding(.trailing, WCSpace.xs)
            }
    }
}

// MARK: - Unavailable banner

private struct CalibrationUnavailableBanner: View {
    var body: some View {
        OperatorNotice(
            "Full calibrate wizard requires the latest Orin build (PR #88) — checklist mode only for now.",
            tint: WC.warn
        )
    }
}

// MARK: - Legacy step model (unchanged from original CalibrateView)

private struct LegacyStep: Identifiable, Equatable {
    let id: Int
    let title: String
    let headline: String
    let detail: String
    let actionTitle: String
    let systemImage: String

    static let preflight = LegacyStep(
        id: 1,
        title: "Preflight checks",
        headline: "Confirm camera and network",
        detail: "Live checks below verify the rig end-to-end: camera feed, GPS ingest, remote tracker heard, base fix. All green → confirm. Amber rows tell you exactly what to fix first.",
        actionTitle: "Confirm preflight",
        systemImage: "checklist"
    )
    static let baseLock = LegacyStep(
        id: 2,
        title: "Base lock (GPS)",
        headline: "Lock the base location",
        detail: "Latches the base GPS position as the camera reference. Needs the base tracker to have a fix — watch for the Base fix line on the GPS chip first.",
        actionTitle: "Capture base lock",
        systemImage: "location.fill"
    )
    static let heading = LegacyStep(
        id: 3,
        title: "Heading — aim at remote",
        headline: "Aim the camera at the remote tracker",
        detail: "Place the LoRa remote where you can see it, center the camera on it, then capture. WaveCam reads the pan-motor position and pairs it with the GPS base→remote bearing to solve reference_heading — no magnetometer. Needs base + remote GPS fixes (the GPS chip shows distance + bearing).",
        actionTitle: "Capture heading",
        systemImage: "safari.fill"
    )
    static let tilt = LegacyStep(
        id: 4,
        title: "Tilt reference",
        headline: "Capture a level reference",
        detail: "Aim at a stable horizon or known-height reference so the tracker can map target elevation into camera tilt.",
        actionTitle: "Capture tilt",
        systemImage: "arrow.up.and.down"
    )
    static let zoom = LegacyStep(
        id: 5,
        title: "Zoom / FOV curve",
        headline: "Map zoom to field of view",
        detail: "Sample wide, mid, and tele positions so the tracker can estimate box size and vision confidence at each zoom state.",
        actionTitle: "Capture zoom",
        systemImage: "plus.magnifyingglass"
    )
    static let dryRun = LegacyStep(
        id: 6,
        title: "Dry-run",
        headline: "Prove it before the water",
        detail: "Walk the remote around and watch GPS point the camera, then step into frame and confirm vision lock takes over. First session at a new spot: run all six steps in the yard before any water session. Emergency Stop stays visible throughout.",
        actionTitle: "Mark ready",
        systemImage: "play.circle.fill"
    )
    static let all: [LegacyStep] = [.preflight, .baseLock, .heading, .tilt, .zoom, .dryRun]
}

// MARK: - Legacy status strip

private struct LegacyStatusStrip: View {
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
            OperatorMetric(label: "SESSION", value: status?.session.state ?? "READY",
                           tint: WC.ok, cornerRadius: WCRadius.sm, uppercaseValue: false)
            OperatorMetric(label: "GPS", value: gpsText,
                           tint: WC.ok, cornerRadius: WCRadius.sm, uppercaseValue: false)
            OperatorMetric(label: "OWNER", value: status?.ptz.owner.ptzOwnerLabel ?? "IDLE",
                           tint: WC.brand, cornerRadius: WCRadius.sm, uppercaseValue: false)
            OperatorMetric(label: "REF HDG", value: refHeadingText,
                           tint: WC.muted, cornerRadius: WCRadius.sm, uppercaseValue: false)
        }
    }
}

// MARK: - Legacy steps list card

private struct LegacyStepsCard: View {
    let activeStepID: Int
    let capturedStepIDs: Set<Int>
    let onSelect: (LegacyStep) -> Void

    var body: some View {
        GlassCard(cornerRadius: WCRadius.lg, padding: WCSpace.sm) {
            VStack(spacing: WCSpace.sm) {
                ForEach(LegacyStep.all) { step in
                    Button { onSelect(step) } label: {
                        LegacyStepRow(step: step, state: rowState(for: step))
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    private func rowState(for step: LegacyStep) -> LegacyStepRow.StateKind {
        if activeStepID == step.id { return .active }
        if capturedStepIDs.contains(step.id) { return .done }
        return .pending
    }
}

private struct LegacyStepRow: View {
    enum StateKind { case done, active, pending }
    let step: LegacyStep
    let state: StateKind

    var body: some View {
        HStack(spacing: WCSpace.md) {
            LegacyStepBadge(stepNumber: step.id, state: state)
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
        switch state { case .done: "DONE"; case .active: "NOW"; case .pending: "WAIT" }
    }
    private var statusColor: Color {
        switch state { case .done: WC.ok; case .active: WC.accent; case .pending: WC.faint }
    }
    private var rowBackground: Color { state == .active ? WC.accent.opacity(0.1) : WC.ink }
    private var rowStroke: Color { state == .active ? WC.accent.opacity(0.55) : WC.line }
}

private struct LegacyStepBadge: View {
    let stepNumber: Int
    let state: LegacyStepRow.StateKind

    var body: some View {
        ZStack {
            Circle().fill(fill).overlay(Circle().stroke(stroke))
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
        switch state { case .done: WC.ok; case .active: WC.accent.opacity(0.12); case .pending: Color.clear }
    }
    private var stroke: Color {
        switch state { case .done: Color.clear; case .active: WC.accent; case .pending: WC.line }
    }
}

// MARK: - Legacy preflight checklist

private struct LegacyPreflightChecklist: View {
    @Environment(WaveCamClient.self) private var client

    var body: some View {
        VStack(alignment: .leading, spacing: WCSpace.sm) {
            checkRow("Camera feed",
                     ok: client.connected && (client.status?.tracking.fps ?? 0) > 10,
                     okText: "live · \(Int(client.status?.tracking.fps ?? 0)) fps",
                     failText: client.connected ? "no frames" : "Orin offline")
            if let alive = client.status?.gps?.readerAlive {
                checkRow("GPS ingest", ok: alive,
                         okText: "OK", failText: "DOWN — restart wavecam service")
            }
            checkRow("Remote tracker",
                     ok: (client.status?.gps?.targetAgeSec ?? .infinity) < 120,
                     okText: "heard \(Int(client.status?.gps?.targetAgeSec ?? 0))s ago",
                     failText: client.status?.gps?.targetAgeSec == nil
                        ? "not heard — power it on, give it sky"
                        : "stale — check it's on and within range")
            checkRow("Base GPS fix",
                     ok: client.status?.gps?.baseAgeSec != nil,
                     okText: "fix acquired",
                     failText: "no fix — needs open sky")
        }
        .padding(WCSpace.sm)
        .background(WC.ink.opacity(0.6), in: .rect(cornerRadius: WCRadius.sm))
    }

    private func checkRow(_ label: String, ok: Bool, okText: String, failText: String) -> some View {
        HStack(spacing: WCSpace.sm) {
            Image(systemName: ok ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                .font(.system(size: 13, weight: .bold))
                .foregroundStyle(ok ? WC.ok : WC.warn)
            Text(label).font(WCFont.label).foregroundStyle(WC.txt)
            Spacer(minLength: WCSpace.sm)
            Text(ok ? okText : failText)
                .font(WCFont.caption)
                .foregroundStyle(ok ? WC.muted : WC.warn)
                .multilineTextAlignment(.trailing)
        }
    }
}

// MARK: - Legacy active step card

private struct LegacyActiveCard: View {
    let step: LegacyStep
    let canGoBack: Bool
    let canGoForward: Bool
    let isCaptured: Bool
    let isCaptureInFlight: Bool
    let calibrationAvailable: Bool?
    let refusalMessage: String?
    let onBack: () -> Void
    let onCapture: () -> Void
    let onForward: () -> Void
    let onDismissRefusal: () -> Void

    private static let localStepIDs: Set<Int> = [LegacyStep.preflight.id, LegacyStep.dryRun.id]
    private var isLocalStep: Bool { Self.localStepIDs.contains(step.id) }
    private var showsChecklistMode: Bool { !isLocalStep && calibrationAvailable == false }

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
                        Text("STEP \(step.id) OF \(LegacyStep.all.count)")
                            .font(WCFont.label).tracking(1.4).foregroundStyle(WC.muted)
                        Text(step.headline)
                            .font(WCFont.title).foregroundStyle(WC.txt)
                            .lineLimit(2).minimumScaleFactor(0.78)
                    }
                }
                Text(step.detail)
                    .font(WCFont.body).foregroundStyle(WC.muted).lineSpacing(4)
                if step.id == LegacyStep.preflight.id {
                    LegacyPreflightChecklist()
                }
                calRefusalRow(refusalMessage, onDismiss: onDismissRefusal)
                HStack(spacing: WCSpace.sm) {
                    GlassButton(label: "Back", icon: "chevron.left", role: .normal,
                                disabled: !canGoBack, action: onBack)
                    legacyCaptureButton
                    GlassButton(label: "Next", icon: "chevron.right", role: .normal,
                                disabled: !canGoForward, action: onForward)
                }
            }
        }
    }

    @ViewBuilder
    private var legacyCaptureButton: some View {
        if isCaptureInFlight {
            GlassButton(label: "Capturing…", role: .active, disabled: true, action: {})
                .overlay(alignment: .leading) {
                    ProgressView().tint(Color.black).scaleEffect(0.72).padding(.leading, WCSpace.lg)
                }
        } else if showsChecklistMode {
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
