import SwiftUI

/// Native tuning panel -- parity with the :8088 web UI. Every setting here is
/// "hot": it applies on the next frame via POST /config/hot, no restart needed.
/// Loads current values once on appear, then pushes each change live.
struct TuneView: View {
    @Environment(WaveCamClient.self) private var client

    @State private var loaded = false
    @State private var loading = false
    @State private var colorPreset = "orange_red"
    @State private var yoloClass = 0
    @State private var aimY = 0.5
    @State private var conf = 0.35
    @State private var requirePerson = false
    @State private var showMask = false
    @State private var maxPan = 10.0
    @State private var maxTilt = 8.0
    @State private var deadzone = 0.08
    @State private var ffGain = 0.0
    @State private var model: String?
    @State private var restartKeys: [String] = []
    @State private var showRestartConfirm = false
    @State private var configError: String?
    @State private var cinematicAvailable = false
    @State private var cinematicEnabled = false
    @State private var subjectSize = 0.5
    // DETECTION / ADVANCED
    @State private var everyN: Int? = nil
    @State private var lockThreshold: Double? = nil
    @State private var unlockThreshold: Double? = nil
    @State private var matchDist: Double? = nil
    // COLOR
    @State private var colorMinArea: Int? = nil
    @State private var colorMaxArea: Int? = nil
    @State private var morphKernel: Int? = nil
    // MOTION advanced
    @State private var ffDeadzoneMult: Double? = nil
    @State private var ptzMinSpeed: Int? = nil
    @State private var commandMinInterval: Double? = nil
    @State private var invertTilt: Bool? = nil
    @State private var invertPan: Bool? = nil
    // STREAM
    @State private var jpegQuality: Int? = nil

    private let presets: [(id: String, name: String)] = [
        ("orange_red", "Orange / red (rashguard)"), ("orange", "Orange"),
        ("blue", "Blue"), ("green", "Green"), ("yellow", "Yellow"), ("pink", "Pink"),
    ]
    private let classes: [(id: Int, name: String)] = [
        (0, "person"), (1, "bicycle"), (2, "car"), (3, "motorcycle"),
        (14, "bird"), (15, "cat"), (16, "dog"), (32, "sports ball"),
        (37, "surfboard"), (41, "cup"),
    ]

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                header

                if let configError {
                    TuneNotice(configError, tint: WC.kill)
                        .onTapGesture { self.configError = nil }
                }

                TuneCard(title: "TARGET") {
                    pickerRow("Color preset", selection: $colorPreset, options: presets.map { ($0.id, $0.name) }, key: "color.preset")
                    tuneCaption("Match the subject's color. Your orange rashguard → Orange / red. Other presets chase that color instead, so the camera won't lock onto you.")
                    TuneDivider()
                    pickerRow("YOLO target", selection: $yoloClass, options: classes.map { ($0.id, $0.name) }, key: "detector.person_class")
                    TuneDivider()
                    infoRow("Model", model ?? "—")
                    TuneDivider()
                    sliderRow("Aim point", value: $aimY, range: 0.2...0.75, step: 0.01, readout: aimLabel(aimY), key: "fusion.person_aim_y")
                }

                TuneCard(title: "DETECTION") {
                    sliderRow("YOLO confidence", value: $conf, range: 0.05...0.95, step: 0.05, readout: fmt(conf), key: "detector.conf")
                    TuneDivider()
                    toggleRow("Require YOLO person", isOn: $requirePerson, key: "fusion.require_person")
                    tuneCaption("Off: track the color cue even when YOLO can't make out a person — best when you're far offshore. On: only lock onto a confirmed person (you'll lose lock at distance).")
                    TuneDivider()
                    toggleRow("Show detection mask", isOn: $showMask, key: "web.show_mask")
                }

                TuneCard(title: "MOTION") {
                    sliderRow("Max pan speed", value: $maxPan, range: 1...24, step: 1, readout: "\(Int(maxPan))", key: "ptz.max_pan_speed", isInt: true)
                    TuneDivider()
                    sliderRow("Max tilt speed", value: $maxTilt, range: 1...20, step: 1, readout: "\(Int(maxTilt))", key: "ptz.max_tilt_speed", isInt: true)
                    TuneDivider()
                    sliderRow("Deadband", value: $deadzone, range: 0.02...0.30, step: 0.01, readout: fmt(deadzone), key: "ptz.deadzone")
                    TuneDivider()
                    sliderRow("Feed-forward gain", value: $ffGain, range: 0.0...1.0, step: 0.05, readout: fmt(ffGain), key: "ptz.ff_gain")
                }

                if cinematicAvailable {
                    TuneCard(title: "CINEMATIC ZOOM") {
                        toggleRow("Cinematic zoom (auto-frame)", isOn: $cinematicEnabled, key: "ptz.cinematic_zoom_enabled")
                        if cinematicEnabled {
                            TuneDivider()
                            sliderRow("Subject size", value: $subjectSize, range: 0.2...0.8, step: 0.05, readout: fmt(subjectSize), key: "ptz.zoom_target_frac")
                        }
                    }
                }

                advancedDetectionCard
                colorCard
                advancedMotionCard

                if !restartKeys.isEmpty {
                    TuneCard(title: "SERVICE") {
                        Text("Restart-only settings (change on the Orin web UI, then restart):")
                            .font(.system(size: 11)).foregroundStyle(WC.muted)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Text(restartKeys.joined(separator: ", "))
                            .font(.system(size: 11, design: .monospaced)).foregroundStyle(WC.faint)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Button {
                            showRestartConfirm = true
                        } label: {
                            Label("Restart WaveCam", systemImage: "arrow.clockwise.circle")
                                .font(.system(size: 14, weight: .bold))
                                .frame(maxWidth: .infinity, minHeight: 44)
                                .foregroundStyle(WC.warn)
                                .background(WC.warn.opacity(0.12), in: .rect(cornerRadius: 13))
                                .overlay(RoundedRectangle(cornerRadius: 13).stroke(WC.warn.opacity(0.6)))
                        }
                        .buttonStyle(.plain)
                        .disabled(client.mode != .live)
                    }
                }

                Text("Tuning changes apply live (no restart). Restart-only keys are under Service.")
                    .font(.system(size: 11)).foregroundStyle(WC.faint)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.horizontal, 16).padding(.top, 8).padding(.bottom, 24)
            .disabled(!loaded)
            .opacity(loaded ? 1 : 0.55)
        }
        .background(WC.bg.ignoresSafeArea())
        .scrollIndicators(.hidden)
        .task { await load() }
        .alert("Restart WaveCam?", isPresented: $showRestartConfirm) {
            Button("Restart", role: .destructive) { Task { await client.systemRestart() } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Stops PTZ and restarts the vision service. The live feed drops for a few seconds.")
        }
    }

    @ViewBuilder private var header: some View {
        if client.mode != .live {
            TuneNotice("Switch to Live mode on the Connect tab to tune.", tint: WC.warn)
        } else if !loaded {
            TuneNotice(client.connected ? "Loading current settings..." : "Connecting to the Orin...", tint: WC.muted)
        }
    }

    // MARK: - Feature-detected advanced cards

    @ViewBuilder private var advancedDetectionCard: some View {
        let anyVisible = everyN != nil || lockThreshold != nil || unlockThreshold != nil || matchDist != nil
        if anyVisible {
            TuneCard(title: "DETECTION ADVANCED") {
                if let n = everyN {
                    sliderRow("YOLO every N frames", value: Binding(get: { Double(n) }, set: { everyN = Int($0) }),
                              range: 1...30, step: 1, readout: "\(n)", key: "detector.every_n", isInt: true)
                }
                if let lt = lockThreshold {
                    if everyN != nil { TuneDivider() }
                    sliderRow("Lock threshold", value: Binding(get: { lt }, set: { lockThreshold = $0 }),
                              range: 0.05...0.95, step: 0.05, readout: fmt(lt), key: "fusion.lock_threshold")
                }
                if let ut = unlockThreshold {
                    if everyN != nil || lockThreshold != nil { TuneDivider() }
                    sliderRow("Unlock threshold", value: Binding(get: { ut }, set: { unlockThreshold = $0 }),
                              range: 0.05...0.95, step: 0.05, readout: fmt(ut), key: "fusion.unlock_threshold")
                }
                if let md = matchDist {
                    if everyN != nil || lockThreshold != nil || unlockThreshold != nil { TuneDivider() }
                    sliderRow("Color/YOLO match px", value: Binding(get: { md }, set: { matchDist = $0 }),
                              range: 20...500, step: 10, readout: "\(Int(md))", key: "fusion.match_dist")
                }
            }
        }
    }

    @ViewBuilder private var colorCard: some View {
        let anyVisible = colorMinArea != nil || colorMaxArea != nil || morphKernel != nil
        if anyVisible {
            TuneCard(title: "COLOR") {
                if let mn = colorMinArea {
                    numberRow("Min color blob area", value: Binding(get: { mn }, set: { colorMinArea = $0 }),
                              bounds: 1...500_000, key: "color.min_area")
                }
                if let mx = colorMaxArea {
                    if colorMinArea != nil { TuneDivider() }
                    numberRow("Max color blob area", value: Binding(get: { mx }, set: { colorMaxArea = $0 }),
                              bounds: 100...1_000_000, key: "color.max_area")
                }
                if let mk = morphKernel {
                    if colorMinArea != nil || colorMaxArea != nil { TuneDivider() }
                    sliderRow("Mask cleanup kernel", value: Binding(get: { Double(mk) }, set: { morphKernel = Int($0) }),
                              range: 1...31, step: 2, readout: "\(mk)", key: "color.morph_kernel", isInt: true)
                }
            }
        }
    }

    @ViewBuilder private var advancedMotionCard: some View {
        let anyVisible = ffDeadzoneMult != nil || ptzMinSpeed != nil || commandMinInterval != nil
                      || invertTilt != nil || invertPan != nil || jpegQuality != nil
        if anyVisible {
            TuneCard(title: "MOTION ADVANCED") {
                if let m = ffDeadzoneMult {
                    sliderRow("FF deadband mult", value: Binding(get: { m }, set: { ffDeadzoneMult = $0 }),
                              range: 1...4, step: 0.1, readout: fmt1(m), key: "ptz.ff_deadzone_mult")
                }
                if let s = ptzMinSpeed {
                    if ffDeadzoneMult != nil { TuneDivider() }
                    sliderRow("Min speed", value: Binding(get: { Double(s) }, set: { ptzMinSpeed = Int($0) }),
                              range: 1...8, step: 1, readout: "\(s)", key: "ptz.min_speed", isInt: true)
                }
                if let ci = commandMinInterval {
                    if ffDeadzoneMult != nil || ptzMinSpeed != nil { TuneDivider() }
                    sliderRow("Command interval", value: Binding(get: { ci }, set: { commandMinInterval = $0 }),
                              range: 0.01...0.5, step: 0.01, readout: fmt(ci), key: "ptz.command_min_interval")
                }
                if let it = invertTilt {
                    if ffDeadzoneMult != nil || ptzMinSpeed != nil || commandMinInterval != nil { TuneDivider() }
                    toggleRow("Invert tilt", isOn: Binding(get: { it }, set: { invertTilt = $0 }), key: "ptz.invert_tilt")
                }
                if let ip = invertPan {
                    if ffDeadzoneMult != nil || ptzMinSpeed != nil || commandMinInterval != nil || invertTilt != nil { TuneDivider() }
                    toggleRow("Invert pan", isOn: Binding(get: { ip }, set: { invertPan = $0 }), key: "ptz.invert_pan")
                }
                if let jq = jpegQuality {
                    if ffDeadzoneMult != nil || ptzMinSpeed != nil || commandMinInterval != nil || invertTilt != nil || invertPan != nil { TuneDivider() }
                    sliderRow("JPEG quality", value: Binding(get: { Double(jq) }, set: { jpegQuality = Int($0) }),
                              range: 30...95, step: 5, readout: "\(jq)", key: "web.jpeg_quality", isInt: true)
                }
            }
        }
    }

    @ViewBuilder
    private func pickerRow<T: Hashable>(_ label: String, selection: Binding<T>, options: [(T, String)], key: String) -> some View {
        HStack {
            Text(label).font(.system(size: 13, weight: .medium)).foregroundStyle(WC.txt)
            Spacer()
            Picker(label, selection: selection) {
                ForEach(options, id: \.0) { Text($0.1).tag($0.0) }
            }
            .pickerStyle(.menu)
            .tint(WC.accent)
            .onChange(of: selection.wrappedValue) { _, v in send([key: v]) }
        }
    }

    @ViewBuilder
    private func infoRow(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label).font(.system(size: 13, weight: .medium)).foregroundStyle(WC.txt)
            Spacer()
            Text(value).font(.system(size: 12, design: .monospaced)).foregroundStyle(WC.muted)
                .lineLimit(1).truncationMode(.middle)
        }
    }

    @ViewBuilder
    private func sliderRow(_ label: String, value: Binding<Double>, range: ClosedRange<Double>, step: Double, readout: String, key: String, isInt: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(label).font(.system(size: 13, weight: .medium)).foregroundStyle(WC.txt)
                Spacer()
                Text(readout).font(.system(size: 13, weight: .semibold, design: .monospaced)).foregroundStyle(WC.accent)
            }
            Slider(value: value, in: range, step: step) { editing in
                if !editing { send([key: isInt ? Int(value.wrappedValue) : value.wrappedValue]) }
            }
            .tint(WC.accent)
        }
    }

    @ViewBuilder
    private func toggleRow(_ label: String, isOn: Binding<Bool>, key: String) -> some View {
        Toggle(isOn: isOn) {
            Text(label).font(.system(size: 13, weight: .medium)).foregroundStyle(WC.txt)
        }
        .tint(WC.accent)
        .onChange(of: isOn.wrappedValue) { _, v in send([key: v]) }
    }

    @ViewBuilder
    private func tuneCaption(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 11))
            .foregroundStyle(WC.faint)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// Stepper row for wide integer ranges (color.min_area, color.max_area) where a
    /// slider would have too many steps to be usable. Step is 1/1000th of range width.
    @ViewBuilder
    private func numberRow(_ label: String, value: Binding<Int>, bounds: ClosedRange<Int>, key: String) -> some View {
        let step = max(1, (bounds.upperBound - bounds.lowerBound) / 1000)
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(label).font(.system(size: 13, weight: .medium)).foregroundStyle(WC.txt)
                Spacer()
                Text("\(value.wrappedValue)")
                    .font(.system(size: 13, weight: .semibold, design: .monospaced))
                    .foregroundStyle(WC.accent)
                    .frame(minWidth: 64, alignment: .trailing)
            }
            Stepper(value: value, in: bounds, step: step) {
                EmptyView()
            }
            .labelsHidden()
            .onChange(of: value.wrappedValue) { _, v in send([key: v]) }
        }
    }

    private func fmt(_ v: Double) -> String { v.formatted(.number.precision(.fractionLength(2))) }
    private func fmt1(_ v: Double) -> String { v.formatted(.number.precision(.fractionLength(1))) }

    private func aimLabel(_ v: Double) -> String {
        if v <= 0.25 { return "HEAD" }
        if v <= 0.4 { return "CHEST" }
        return "CENTER \(fmt(v))"
    }

    private func load() async {
        guard !loaded, !loading else { return }
        loading = true
        defer { loading = false }
        guard let cfg = await client.config() else { return }
        colorPreset = cfg.current.color.preset
        yoloClass = cfg.current.detector.personClass
        aimY = cfg.current.fusion.personAimY
        conf = cfg.current.detector.conf
        requirePerson = cfg.current.fusion.requirePerson
        showMask = cfg.current.web.showMask
        maxPan = Double(cfg.current.ptz.maxPanSpeed)
        maxTilt = Double(cfg.current.ptz.maxTiltSpeed)
        deadzone = cfg.current.ptz.deadzone
        ffGain = cfg.current.ptz.ffGain
        model = cfg.current.detector.model
        restartKeys = cfg.restartRequiredKeys ?? []
        if let cz = cfg.current.ptz.cinematicZoomEnabled {
            cinematicAvailable = true
            cinematicEnabled = cz
            subjectSize = cfg.current.ptz.zoomTargetFrac ?? 0.5
        }
        // Feature-detected advanced keys — remain nil when backend doesn't expose them
        everyN = cfg.current.detector.everyN
        lockThreshold = cfg.current.fusion.lockThreshold
        unlockThreshold = cfg.current.fusion.unlockThreshold
        matchDist = cfg.current.fusion.matchDist
        colorMinArea = cfg.current.color.minArea
        colorMaxArea = cfg.current.color.maxArea
        morphKernel = cfg.current.color.morphKernel
        ffDeadzoneMult = cfg.current.ptz.ffDeadzoneMult
        ptzMinSpeed = cfg.current.ptz.minSpeed
        commandMinInterval = cfg.current.ptz.commandMinInterval
        invertTilt = cfg.current.ptz.invertTilt
        invertPan = cfg.current.ptz.invertPan
        jpegQuality = cfg.current.web.jpegQuality
        loaded = true
    }

    private func send(_ patch: [String: Any]) {
        guard loaded, client.mode == .live else { return }
        Task {
            if await client.configHot(patch) {
                configError = nil
            } else {
                configError = "Setting not applied: \(client.lastControlError ?? "rejected by the Orin"). Tap to dismiss."
            }
        }
    }
}

private struct TuneCard<Content: View>: View {
    let title: String
    @ViewBuilder var content: Content

    var body: some View {
        OperatorCard(title: title) {
            content
        }
    }
}

private struct TuneDivider: View {
    var body: some View { OperatorDivider() }
}

private struct TuneNotice: View {
    let text: String
    let tint: Color

    init(_ text: String, tint: Color) {
        self.text = text
        self.tint = tint
    }

    var body: some View {
        OperatorNotice(text, tint: tint)
    }
}

#Preview {
    TuneView()
        .environment(WaveCamClient(mode: .mock))
        .preferredColorScheme(.dark)
}
