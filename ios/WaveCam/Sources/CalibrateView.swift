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

    // MARK: wizard state

    /// nil = probing, true = PR #88 endpoints live, false = legacy mode
    @State private var sessionAvailable: Bool? = nil
    @State private var wizardStep: WizardStep = .idle
    @State private var sessionState: WCCalibrationSessionState? = nil
    @State private var isInFlight = false
    @State private var refusalMessage: String? = nil
    @State private var showingMap = false
    @State private var mapPurpose: MapPlacementModel.Mode = .base
    @State private var showingOffset = false

    // Heading capture sub-state
    @State private var headingPreviewPending = false   // preview shown, awaiting tap-accept
    @State private var headingBearingDeg: Double? = nil
    @State private var headingDistanceM: Double? = nil

    // Legacy (checklist-only) state — preserved from the original CalibrateView
    @State private var activeStepID = LegacyStep.preflight.id
    @State private var capturedStepIDs: Set<Int> = []
    @State private var legacyCalibrationState: WCCalibrationState? = nil
    @State private var legacyCaptureInFlight = false
    @State private var legacyRefusal: String? = nil

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                if let available = sessionAvailable, !available {
                    CalibrationUnavailableBanner()
                }

                // Calibration status banner — always visible when session is active or valid.
                if let state = sessionState {
                    CalibrationBannerStrip(state: state)
                }

                if sessionAvailable == true {
                    wizardBody
                } else {
                    legacyBody
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 6)
            .padding(.bottom, 96)
        }
        .background(WC.bg.ignoresSafeArea())
        .scrollIndicators(.hidden)
        .task {
            await client.refresh()
            await probeSessionEndpoint()
        }
        .sheet(isPresented: $showingMap) {
            if let bla = client.status?.sensors?.base?.lat, let blo = client.status?.sensors?.base?.lon {
                MapPlacementView(client: client, initialLat: bla, initialLon: blo, purpose: mapPurpose) { state in
                    if let state { sessionState = state }
                    showingMap = false
                }
            } else {
                Text("No base GPS fix yet to center the map — use the GPS flow above, or wait for a base fix.")
                    .font(WCFont.body).foregroundStyle(WC.muted).padding()
            }
        }
        .sheet(isPresented: $showingOffset) {
            OffsetCalibrateView(client: client,
                                step3BearingDeg: sessionState?.session?.headingLock?.bearingDeg
                                    ?? sessionState?.referenceHeading) { state in
                if let state { sessionState = state }
                showingOffset = false
            }
        }
    }

    // MARK: - Wizard body (PR #88)

    @ViewBuilder
    private var wizardBody: some View {
        switch wizardStep {
        case .idle:
            IdleCard(onEnter: enterCalibration, isInFlight: isInFlight)
        case .location:
            LocationCard(
                sessionState: sessionState,
                isInFlight: isInFlight,
                refusalMessage: refusalMessage,
                onCapture: lockLocation,
                onPlaceOnMap: { mapPurpose = .base; showingMap = true },
                onDismissRefusal: dismissRefusal
            )
        case .heading:
            HeadingCard(
                gpsBearingDeg: client.status?.gps?.bearingDeg,
                gpsDistanceM: client.status?.gps?.distanceM,
                previewPending: headingPreviewPending,
                sessionState: sessionState,
                isInFlight: isInFlight,
                refusalMessage: refusalMessage,
                onPreview: previewHeading,
                onAccept: acceptHeading,
                onCancel: cancelHeadingPreview,
                onDismissRefusal: dismissRefusal,
                onSetHeadingOnMap: { mapPurpose = .headingLookAt; showingMap = true }
            )
        case .validation:
            ValidationCard(
                gpsBearingDeg: client.status?.gps?.bearingDeg,
                gpsDistanceM: client.status?.gps?.distanceM,
                sessionState: sessionState,
                isInFlight: isInFlight,
                refusalMessage: refusalMessage,
                onValidate: sendValidation,
                onDismissRefusal: dismissRefusal
            )
        case .confirm:
            ConfirmCard(
                sessionState: sessionState,
                isInFlight: isInFlight,
                refusalMessage: refusalMessage,
                onConfirm: confirmValidation,
                onDismissRefusal: dismissRefusal
            )
        case .done:
            DoneCard(sessionState: sessionState, onExit: exitCalibration, isInFlight: isInFlight)
        }

        if sessionState?.session?.headingLock != nil && (wizardStep == .heading || wizardStep == .validation) {
            Button { showingOffset = true } label: {
                Label("Refine: aim camera at tracker (offset)", systemImage: "scope")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .disabled(isInFlight)
        }

        if wizardStep != .idle {
            ExitCalibrationButton(isInFlight: isInFlight, onExit: exitCalibration)
        }
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

    // MARK: - Wizard actions

    private func enterCalibration() {
        guard !isInFlight else { return }
        Task {
            isInFlight = true
            refusalMessage = nil
            let result = await client.calibrateSessionStart()
            isInFlight = false
            switch result {
            case let .success(state):
                sessionState = state
                wizardStep = .location
            case let .failure(error):
                refusalMessage = error.localizedDescription
            }
        }
    }

    private func lockLocation() {
        guard !isInFlight else { return }
        Task {
            isInFlight = true
            refusalMessage = nil
            let result = await client.calibrateLocation()
            isInFlight = false
            switch result {
            case let .success(state):
                sessionState = state
                // Level step removed 2026-06-17: the rig-phone mounts off the camera and on
                // its side, so it can't sense pan-axis tilt. Operator levels by hand; go
                // straight to heading. (Backend no longer requires a level check.)
                wizardStep = .heading
            case let .failure(error):
                refusalMessage = error.localizedDescription
            }
        }
    }

    private func previewHeading() {
        guard let bearingDeg = client.status?.gps?.bearingDeg else {
            refusalMessage = "GPS bearing needed — wait for base + remote fixes (the GPS chip shows distance + bearing)."
            return
        }
        guard !isInFlight else { return }
        headingBearingDeg = bearingDeg
        headingDistanceM = client.status?.gps?.distanceM
        Task {
            isInFlight = true
            refusalMessage = nil
            let result = await client.calibrateHeadingLockPreview(
                bearingDeg: bearingDeg,
                distanceM: headingDistanceM
            )
            isInFlight = false
            switch result {
            case let .success(state):
                // Unexpected: backend accepted without operator_accepted flag.
                sessionState = state
                wizardStep = .validation
            case let .failure(error):
                // .operatorAcceptRequired is the normal path — show the preview panel.
                if case .operatorAcceptRequired = error {
                    headingPreviewPending = true
                } else {
                    refusalMessage = error.localizedDescription
                }
            }
        }
    }

    private func acceptHeading() {
        guard let bearingDeg = headingBearingDeg, !isInFlight else { return }
        Task {
            isInFlight = true
            refusalMessage = nil
            let result = await client.calibrateHeadingLockAccept(
                bearingDeg: bearingDeg,
                distanceM: headingDistanceM
            )
            isInFlight = false
            headingPreviewPending = false
            switch result {
            case let .success(state):
                sessionState = state
                wizardStep = .validation
            case let .failure(error):
                refusalMessage = error.localizedDescription
            }
        }
    }

    private func cancelHeadingPreview() {
        headingPreviewPending = false
        headingBearingDeg = nil
        headingDistanceM = nil
        refusalMessage = nil
    }

    private func sendValidation() {
        guard let bearingDeg = client.status?.gps?.bearingDeg else {
            refusalMessage = "GPS bearing needed — aim at a different landmark or stationary point."
            return
        }
        guard !isInFlight else { return }
        Task {
            isInFlight = true
            refusalMessage = nil
            let result = await client.calibrateValidation(
                bearingDeg: bearingDeg,
                distanceM: client.status?.gps?.distanceM
            )
            isInFlight = false
            switch result {
            case let .success(state):
                sessionState = state
                wizardStep = .confirm
            case let .failure(error):
                refusalMessage = error.localizedDescription
            }
        }
    }

    private func confirmValidation() {
        guard !isInFlight else { return }
        Task {
            isInFlight = true
            refusalMessage = nil
            let result = await client.calibrateValidationConfirm(accepted: true)
            isInFlight = false
            switch result {
            case let .success(state):
                sessionState = state
                wizardStep = .done
            case let .failure(error):
                refusalMessage = error.localizedDescription
            }
        }
    }

    private func exitCalibration() {
        guard !isInFlight else { return }
        Task {
            isInFlight = true
            let result = await client.calibrateSessionExit()
            isInFlight = false
            switch result {
            case let .success(state):
                sessionState = state
                wizardStep = .idle
                headingPreviewPending = false
                headingBearingDeg = nil
                headingDistanceM = nil
                refusalMessage = nil
            case let .failure(error):
                // Exit POST failed — the backend may still hold the calibrate PTZ
                // lockout. Keep the wizard open and surface the error so the operator
                // doesn't believe calibration exited while it is still active.
                refusalMessage = error.localizedDescription
            }
        }
    }

    private func dismissRefusal() { refusalMessage = nil }

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

// MARK: - Wizard steps enum

private enum WizardStep {
    case idle, location, heading, validation, confirm, done
}

// MARK: - Wizard cards

private struct CalibrationBannerStrip: View {
    let state: WCCalibrationSessionState

    private var bannerColor: Color {
        if state.active { return WC.warn }
        if state.valid { return WC.ok }
        return WC.faint
    }

    var body: some View {
        HStack(spacing: WCSpace.sm) {
            Circle().fill(bannerColor).frame(width: 8, height: 8)
            Text(state.banner)
                .font(WCFont.bodyBold)
                .foregroundStyle(bannerColor)
            Spacer(minLength: WCSpace.sm)
            if let ageSec = state.ageSec, state.valid {
                Text(String(format: "%.0f s ago", ageSec))
                    .font(WCFont.caption)
                    .foregroundStyle(WC.muted)
            }
        }
        .padding(.horizontal, WCSpace.md)
        .padding(.vertical, WCSpace.sm)
        .background(bannerColor.opacity(0.12), in: .rect(cornerRadius: WCRadius.sm))
        .overlay(RoundedRectangle(cornerRadius: WCRadius.sm).stroke(bannerColor.opacity(0.28)))
    }
}

private struct IdleCard: View {
    let onEnter: () -> Void
    let isInFlight: Bool

    var body: some View {
        GlassCard(cornerRadius: WCRadius.lg, padding: WCSpace.md) {
            VStack(alignment: .leading, spacing: WCSpace.md) {
                calHeaderRow(icon: "scope", title: "CALIBRATE MODE", subtitle: "Lock location + heading before filming")

                Text("Sets camera location and heading reference so GPS pointing lands the subject in frame at range. Run once per session (or whenever the tripod moves). KILL remains reachable throughout.")
                    .font(WCFont.body)
                    .foregroundStyle(WC.muted)
                    .lineSpacing(4)

                GlassButton(
                    label: isInFlight ? "Starting…" : "Enter Calibrate",
                    icon: "scope",
                    role: .normal,
                    disabled: isInFlight,
                    action: onEnter
                )
            }
        }
    }
}

private struct LocationCard: View {
    let sessionState: WCCalibrationSessionState?
    let isInFlight: Bool
    let refusalMessage: String?
    let onCapture: () -> Void
    let onPlaceOnMap: () -> Void
    let onDismissRefusal: () -> Void

    var body: some View {
        GlassCard(cornerRadius: WCRadius.lg, padding: WCSpace.md) {
            VStack(alignment: .leading, spacing: WCSpace.md) {
                calHeaderRow(icon: "location.fill", title: "STEP 1 OF 3", subtitle: "Lock base location")

                Text("Averages the base Wio GPS fixes and applies an HDOP×UERE error-radius model (realistic ~2–15 m estimate, not just sample noise). The lever-arm offset (antenna → camera origin) is subtracted automatically.")
                    .font(WCFont.body)
                    .foregroundStyle(WC.muted)
                    .lineSpacing(4)

                if let loc = sessionState?.session?.location {
                    LocationResultRow(entry: loc)
                }

                calRefusalRow(refusalMessage, onDismiss: onDismissRefusal)

                GlassButton(
                    label: isInFlight ? "Averaging…" : (sessionState?.session?.location != nil ? "Re-lock location" : "Lock location"),
                    icon: "location.fill",
                    role: .normal,
                    disabled: isInFlight,
                    action: onCapture
                )

                GlassButton(
                    label: "Place on map (satellite)",
                    icon: "map.fill",
                    role: .normal,
                    disabled: isInFlight,
                    action: onPlaceOnMap
                )
            }
        }
    }
}

private struct LocationResultRow: View {
    let entry: WCCalLocationEntry

    var body: some View {
        VStack(alignment: .leading, spacing: WCSpace.xs) {
            if let lat = entry.lat, let lon = entry.lon {
                calDataRow("Position", value: String(format: "%.5f, %.5f", lat, lon))
            }
            if let radius = entry.errorRadiusM {
                calDataRow("Error radius", value: String(format: "±%.1f m (model)", radius))
            }
            if let n = entry.sampleCount {
                calDataRow("Samples", value: "\(n)")
            }
        }
        .padding(WCSpace.sm)
        .background(WC.ok.opacity(0.08), in: .rect(cornerRadius: WCRadius.sm))
        .overlay(RoundedRectangle(cornerRadius: WCRadius.sm).stroke(WC.ok.opacity(0.22)))
    }
}

private struct HeadingCard: View {
    let gpsBearingDeg: Double?
    let gpsDistanceM: Double?
    let previewPending: Bool
    let sessionState: WCCalibrationSessionState?
    let isInFlight: Bool
    let refusalMessage: String?
    let onPreview: () -> Void
    let onAccept: () -> Void
    let onCancel: () -> Void
    let onDismissRefusal: () -> Void
    let onSetHeadingOnMap: () -> Void

    private var hasBearing: Bool { gpsBearingDeg != nil }

    var body: some View {
        GlassCard(cornerRadius: WCRadius.lg, padding: WCSpace.md) {
            VStack(alignment: .leading, spacing: WCSpace.md) {
                calHeaderRow(icon: "safari.fill", title: "STEP 2 OF 3", subtitle: "Capture heading")

                if previewPending {
                    previewPanel
                } else {
                    instructionPanel
                }

                calRefusalRow(refusalMessage, onDismiss: onDismissRefusal)
            }
        }
    }

    @ViewBuilder
    private var instructionPanel: some View {
        Text("Aim the camera at a STATIONARY object at range (≥50 m — a buoy, pier piling, or headland). Vision is the aim-aid. When the target is centred and the GPS lock is stable, tap Preview to freeze the candidate. Review it, then tap Accept to lock the heading. No silent auto-capture.")
            .font(WCFont.body)
            .foregroundStyle(WC.muted)
            .lineSpacing(4)

        if let bearing = gpsBearingDeg, let dist = gpsDistanceM {
            HStack(spacing: WCSpace.sm) {
                OperatorMetric(label: "BEARING", value: String(format: "%.1f°", bearing),
                               tint: WC.ok, cornerRadius: WCRadius.sm, uppercaseValue: false)
                OperatorMetric(label: "DIST", value: String(format: "%.0f m", dist),
                               tint: WC.ok, cornerRadius: WCRadius.sm, uppercaseValue: false)
            }
        } else {
            OperatorNotice("GPS bearing not available — wait for base + remote fixes.", tint: WC.warn)
        }

        GlassButton(
            label: isInFlight ? "Fetching preview…" : "Preview candidate",
            icon: "viewfinder",
            role: .normal,
            disabled: isInFlight || !hasBearing,
            action: onPreview
        )

        GlassButton(
            label: "Set heading on map (satellite)",
            icon: "map.fill",
            role: .normal,
            disabled: isInFlight,
            action: onSetHeadingOnMap
        )
    }

    @ViewBuilder
    private var previewPanel: some View {
        OperatorNotice("Preview captured — verify the target is the correct stationary landmark, then tap Accept to lock the heading. Tap Cancel to re-aim.", tint: WC.warn)

        if let hl = sessionState?.session?.headingLock {
            VStack(alignment: .leading, spacing: WCSpace.xs) {
                if let b = hl.bearingDeg {
                    calDataRow("Bearing", value: String(format: "%.1f°", b))
                }
                if let d = hl.distanceM {
                    calDataRow("Distance", value: String(format: "%.0f m", d))
                }
                if let unc = hl.uncertaintyDeg {
                    let color: Color = unc <= 1.0 ? WC.ok : (unc <= 2.0 ? WC.warn : WC.kill)
                    calDataRow("Uncertainty", value: String(format: "±%.2f°", unc), valueColor: color)
                }
                if let conf = hl.confidence {
                    let color: Color = conf >= 0.7 ? WC.ok : WC.warn
                    calDataRow("Confidence", value: String(format: "%.0f%%", conf * 100), valueColor: color)
                }
            }
            .padding(WCSpace.sm)
            .background(WC.warn.opacity(0.08), in: .rect(cornerRadius: WCRadius.sm))
            .overlay(RoundedRectangle(cornerRadius: WCRadius.sm).stroke(WC.warn.opacity(0.22)))
        }

        HStack(spacing: WCSpace.sm) {
            GlassButton(label: "Cancel", icon: "xmark", role: .normal,
                        disabled: isInFlight, action: onCancel)
            GlassButton(label: isInFlight ? "Locking…" : "Accept — lock heading",
                        icon: "checkmark.circle.fill", role: .active,
                        disabled: isInFlight, action: onAccept)
        }
    }
}

private struct ValidationCard: View {
    let gpsBearingDeg: Double?
    let gpsDistanceM: Double?
    let sessionState: WCCalibrationSessionState?
    let isInFlight: Bool
    let refusalMessage: String?
    let onValidate: () -> Void
    let onDismissRefusal: () -> Void

    var body: some View {
        GlassCard(cornerRadius: WCRadius.lg, padding: WCSpace.md) {
            VStack(alignment: .leading, spacing: WCSpace.md) {
                calHeaderRow(icon: "checkmark.seal", title: "STEP 3A OF 3", subtitle: "Validation check")

                Text("Aim at a DIFFERENT stationary landmark (independent of the heading capture). The backend predicts where it should be and shows the miss. This is the guard against a confidently-wrong calibration.")
                    .font(WCFont.body)
                    .foregroundStyle(WC.muted)
                    .lineSpacing(4)

                if let bearing = gpsBearingDeg, let dist = gpsDistanceM {
                    HStack(spacing: WCSpace.sm) {
                        OperatorMetric(label: "BEARING", value: String(format: "%.1f°", bearing),
                                       tint: WC.ok, cornerRadius: WCRadius.sm, uppercaseValue: false)
                        OperatorMetric(label: "DIST", value: String(format: "%.0f m", dist),
                                       tint: WC.ok, cornerRadius: WCRadius.sm, uppercaseValue: false)
                    }
                } else {
                    OperatorNotice("GPS bearing not available — wait for base + remote fixes.", tint: WC.warn)
                }

                calRefusalRow(refusalMessage, onDismiss: onDismissRefusal)

                GlassButton(
                    label: isInFlight ? "Checking…" : "Validate",
                    icon: "checkmark.seal",
                    role: .normal,
                    disabled: isInFlight || gpsBearingDeg == nil,
                    action: onValidate
                )
            }
        }
    }
}

private struct ConfirmCard: View {
    let sessionState: WCCalibrationSessionState?
    let isInFlight: Bool
    let refusalMessage: String?
    let onConfirm: () -> Void
    let onDismissRefusal: () -> Void

    private var validation: WCCalValidationEntry? { sessionState?.session?.validation }

    var body: some View {
        GlassCard(cornerRadius: WCRadius.lg, padding: WCSpace.md) {
            VStack(alignment: .leading, spacing: WCSpace.md) {
                calHeaderRow(icon: "checkmark.seal.fill", title: "STEP 3B OF 3", subtitle: "Confirm validation")

                if let v = validation {
                    VStack(alignment: .leading, spacing: WCSpace.xs) {
                        if let miss = v.missDeg {
                            let color: Color = miss <= 1.5 ? WC.ok : (miss <= 3.0 ? WC.warn : WC.kill)
                            calDataRow("Miss", value: String(format: "%.2f°", miss), valueColor: color)
                        }
                        if let predicted = v.predictedBearingDeg, let actual = v.bearingDeg {
                            calDataRow("Predicted", value: String(format: "%.1f°", predicted))
                            calDataRow("Actual", value: String(format: "%.1f°", actual))
                        }
                        if let dist = v.distanceM {
                            calDataRow("Distance", value: String(format: "%.0f m", dist))
                        }
                    }
                    .padding(WCSpace.sm)
                    .background(WC.panel2, in: .rect(cornerRadius: WCRadius.sm))
                    .overlay(RoundedRectangle(cornerRadius: WCRadius.sm).stroke(WC.line))
                }

                Text("Review the miss above. ≤2° is within budget and GPS can hand off to vision at range. A larger miss means the heading capture may have been off — use Exit to re-calibrate.")
                    .font(WCFont.body)
                    .foregroundStyle(WC.muted)
                    .lineSpacing(4)

                calRefusalRow(refusalMessage, onDismiss: onDismissRefusal)

                GlassButton(
                    label: isInFlight ? "Confirming…" : "Confirm — looks good",
                    icon: "checkmark.circle.fill",
                    role: .active,
                    disabled: isInFlight,
                    action: onConfirm
                )
            }
        }
    }
}

private struct DoneCard: View {
    let sessionState: WCCalibrationSessionState?
    let onExit: () -> Void
    let isInFlight: Bool

    var body: some View {
        GlassCard(cornerRadius: WCRadius.lg, padding: WCSpace.md) {
            VStack(alignment: .leading, spacing: WCSpace.md) {
                calHeaderRow(icon: "checkmark.circle.fill", title: "CALIBRATED", subtitle: "Ready to film")

                if let hl = sessionState?.session?.headingLock {
                    VStack(alignment: .leading, spacing: WCSpace.xs) {
                        if let b = hl.bearingDeg {
                            calDataRow("Reference bearing", value: String(format: "%.1f°", b))
                        }
                        if let unc = hl.uncertaintyDeg {
                            calDataRow("Uncertainty", value: String(format: "±%.2f°", unc))
                        }
                        if let conf = hl.confidence {
                            let color: Color = conf >= 0.7 ? WC.ok : WC.warn
                            calDataRow("Confidence", value: String(format: "%.0f%%", conf * 100), valueColor: color)
                        }
                    }
                    .padding(WCSpace.sm)
                    .background(WC.ok.opacity(0.08), in: .rect(cornerRadius: WCRadius.sm))
                    .overlay(RoundedRectangle(cornerRadius: WCRadius.sm).stroke(WC.ok.opacity(0.22)))
                }

                Text("Calibration is session-scoped — it auto-invalidates on service restart or tripod move. Re-calibrate whenever the tripod is repositioned.")
                    .font(WCFont.caption)
                    .foregroundStyle(WC.muted)
                    .lineSpacing(3)

                GlassButton(
                    label: isInFlight ? "Exiting…" : "Exit calibrate — go film",
                    icon: "checkmark",
                    role: .active,
                    disabled: isInFlight,
                    action: onExit
                )
            }
        }
    }
}

private struct ExitCalibrationButton: View {
    let isInFlight: Bool
    let onExit: () -> Void

    var body: some View {
        GlassButton(
            label: "Exit calibrate (restore tracker)",
            icon: "xmark.circle",
            role: .normal,
            disabled: isInFlight,
            action: onExit
        )
        .padding(.top, WCSpace.sm)
    }
}

// MARK: - Shared layout helpers (file-private, prefixed to avoid conflicts)

private func calHeaderRow(icon: String, title: String, subtitle: String) -> some View {
    HStack(spacing: WCSpace.sm) {
        Image(systemName: icon)
            .font(.system(size: 19, weight: .semibold))
            .foregroundStyle(WC.accent)
            .frame(width: 38, height: 38)
            .background(WC.accent.opacity(0.12), in: .rect(cornerRadius: WCRadius.sm))
        VStack(alignment: .leading, spacing: WCSpace.xs) {
            Text(title)
                .font(WCFont.label)
                .tracking(1.4)
                .foregroundStyle(WC.muted)
            Text(subtitle)
                .font(WCFont.title)
                .foregroundStyle(WC.txt)
                .lineLimit(2)
                .minimumScaleFactor(0.78)
        }
    }
}

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

private func calDataRow(_ label: String, value: String, valueColor: Color = WC.txt) -> some View {
    HStack {
        Text(label)
            .font(WCFont.caption)
            .foregroundStyle(WC.muted)
        Spacer(minLength: WCSpace.sm)
        Text(value)
            .font(WCFont.captionMono)
            .foregroundStyle(valueColor)
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
