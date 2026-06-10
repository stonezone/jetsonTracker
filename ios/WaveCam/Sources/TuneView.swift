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
    @State private var showHud: Bool? = nil
    // GPS (feature-detected; appears once the P2 backend is deployed)
    @State private var gpsBoost: Double? = nil
    @State private var gpsStale: Double? = nil
    @State private var gpsGrace: Double? = nil
    @State private var gpsDriveZoom: Bool? = nil

    // PRESETS feature state
    @State private var presetsSupported = false
    @State private var tunePresets: [WCPreset] = []
    @State private var activePresetName: String? = nil
    /// Keys that were hot-applied when the active preset was applied. Used to compute the
    /// "modified" dot: if any current Tune value differs from the applied preset, show the dot.
    @State private var appliedPresetValues: [String: JSONValue] = [:]
    @State private var showSavePresetAlert = false
    @State private var newPresetName = ""
    @State private var presetDeleteTarget: WCPreset? = nil
    @State private var showPresetDeleteConfirm = false
    @State private var presetApplyRestartKeys: [String] = []
    @State private var showPresetRestartNotice = false

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
                    OperatorNotice(configError, tint: WC.kill)
                        .onTapGesture { self.configError = nil }
                }

                if showPresetRestartNotice {
                    presetRestartNotice
                }

                if presetsSupported {
                    presetsSection
                }

                OperatorCard(title: "TARGET") {
                    pickerRow("Color preset", selection: $colorPreset, options: presets.map { ($0.id, $0.name) }, key: "color.preset")
                    tuneCaption("Match the subject's color. Your orange rashguard → Orange / red. Other presets chase that color instead, so the camera won't lock onto you.")
                    OperatorDivider()
                    pickerRow("YOLO target", selection: $yoloClass, options: classes.map { ($0.id, $0.name) }, key: "detector.person_class")
                    OperatorDivider()
                    infoRow("Model", model ?? "—")
                    OperatorDivider()
                    sliderRow("Aim point", value: $aimY, range: 0.2...0.75, step: 0.01, readout: aimLabel(aimY), key: "fusion.person_aim_y")
                }

                OperatorCard(title: "DETECTION") {
                    sliderRow("YOLO confidence", value: $conf, range: 0.05...0.95, step: 0.05, readout: fmt(conf), key: "detector.conf")
                    OperatorDivider()
                    toggleRow("Require YOLO person", isOn: $requirePerson, key: "fusion.require_person")
                    tuneCaption("Off: track the color cue even when YOLO can't make out a person — best when you're far offshore. On: only lock onto a confirmed person (you'll lose lock at distance).")
                    OperatorDivider()
                    toggleRow("Show detection mask", isOn: $showMask, key: "web.show_mask")
                    if let hud = showHud {
                        OperatorDivider()
                        toggleRow("Show debug HUD", isOn: Binding(get: { hud }, set: { showHud = $0 }), key: "web.show_hud")
                        tuneCaption("The on-video readout (confidence, lock, FPS). Off = a clean picture for filming; on = diagnostics while tuning.")
                    }
                }

                OperatorCard(title: "MOTION") {
                    sliderRow("Max pan speed", value: $maxPan, range: 1...24, step: 1, readout: "\(Int(maxPan))", key: "ptz.max_pan_speed", isInt: true)
                    OperatorDivider()
                    sliderRow("Max tilt speed", value: $maxTilt, range: 1...20, step: 1, readout: "\(Int(maxTilt))", key: "ptz.max_tilt_speed", isInt: true)
                    OperatorDivider()
                    sliderRow("Deadband", value: $deadzone, range: 0.02...0.30, step: 0.01, readout: fmt(deadzone), key: "ptz.deadzone")
                    OperatorDivider()
                    sliderRow("Feed-forward gain", value: $ffGain, range: 0.0...1.0, step: 0.05, readout: fmt(ffGain), key: "ptz.ff_gain")
                }

                if cinematicAvailable {
                    OperatorCard(title: "CINEMATIC ZOOM") {
                        toggleRow("Cinematic zoom (auto-frame)", isOn: $cinematicEnabled, key: "ptz.cinematic_zoom_enabled")
                        if cinematicEnabled {
                            OperatorDivider()
                            sliderRow("Subject size", value: $subjectSize, range: 0.2...0.8, step: 0.05, readout: fmt(subjectSize), key: "ptz.zoom_target_frac")
                        }
                    }
                }

                gpsCard
                advancedDetectionCard
                colorCard
                advancedMotionCard

                // Always visible — restarting the service is beach first-aid (GPS
                // Ingest DOWN, wedged camera link), not just a restart-keys helper.
                OperatorCard(title: "SERVICE") {
                    Text("First aid: restarts the vision service on the Orin (PTZ stops first). Fixes GPS Ingest DOWN and a wedged camera link. ~15 s outage.")
                        .font(WCFont.caption).foregroundStyle(WC.muted)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    if !restartKeys.isEmpty {
                        OperatorDivider()
                        Text("Restart-only settings (change on the Orin web UI, then restart):")
                            .font(WCFont.caption).foregroundStyle(WC.muted)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Text(restartKeys.joined(separator: ", "))
                            .font(WCFont.captionMono).foregroundStyle(WC.faint)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    GlassButton(
                        label: "Restart WaveCam",
                        icon: "arrow.clockwise.circle",
                        role: .normal,
                        disabled: client.mode != .live,
                        action: { showRestartConfirm = true }
                    )
                }

                Text("Tuning changes apply live (no restart). Restart-only keys are under Service.")
                    .font(WCFont.caption).foregroundStyle(WC.faint)
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
        .alert("Save Preset", isPresented: $showSavePresetAlert) {
            TextField("Preset name", text: $newPresetName)
                .autocorrectionDisabled()
            Button("Save") {
                let name = newPresetName.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !name.isEmpty else { return }
                let values = currentTuneValues()
                Task {
                    let ok = await client.savePreset(name: name, values: values)
                    if ok {
                        activePresetName = name
                        appliedPresetValues = values
                        tunePresets = await client.presets() ?? tunePresets
                    } else {
                        configError = "Could not save preset: \(client.lastControlError ?? "rejected"). Tap to dismiss."
                    }
                }
            }
            Button("Cancel", role: .cancel) { newPresetName = "" }
        } message: {
            Text("Enter a name for this preset. Built-in preset names (Default, Tow Foil, etc.) are reserved.")
        }
        .alert("Delete Preset?", isPresented: $showPresetDeleteConfirm) {
            Button("Delete", role: .destructive) {
                guard let target = presetDeleteTarget else { return }
                Task {
                    let ok = await client.deletePreset(name: target.name)
                    if ok {
                        if activePresetName == target.name { activePresetName = nil; appliedPresetValues = [:] }
                        tunePresets = await client.presets() ?? tunePresets
                    } else {
                        configError = "Could not delete preset: \(client.lastControlError ?? "rejected"). Tap to dismiss."
                    }
                    presetDeleteTarget = nil
                }
            }
            Button("Cancel", role: .cancel) { presetDeleteTarget = nil }
        } message: {
            Text("Delete \"\(presetDeleteTarget?.name ?? "")\"? This cannot be undone.")
        }
    }

    @ViewBuilder private var header: some View {
        if client.mode != .live {
            OperatorNotice("Switch to Live mode on the Connect tab to tune.", tint: WC.warn)
        } else if !loaded {
            OperatorNotice(client.connected ? "Loading current settings..." : "Connecting to the Orin...", tint: WC.muted)
        }
    }

    // MARK: - Presets section

    @ViewBuilder private var presetsSection: some View {
        OperatorCard(title: "PRESETS") {
            // Horizontal chip row
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(tunePresets) { preset in
                        presetChip(preset)
                    }
                }
                .padding(.vertical, 2)
            }

            OperatorDivider()

            // Action buttons row
            HStack(spacing: 8) {
                GlassButton(
                    label: "Save",
                    icon: "square.and.arrow.down",
                    role: .normal,
                    disabled: client.mode != .live,
                    action: { newPresetName = ""; showSavePresetAlert = true }
                )
                GlassButton(
                    label: "Reset",
                    icon: "arrow.counterclockwise",
                    role: .normal,
                    disabled: client.mode != .live,
                    action: { applyPreset(named: "Default") }
                )
            }
        }
    }

    @ViewBuilder private func presetChip(_ preset: WCPreset) -> some View {
        let isActive = preset.name == activePresetName
        let isModified = isActive && isPresetModified
        Button {
            applyPreset(named: preset.name)
        } label: {
            HStack(spacing: 4) {
                Text(preset.name)
                    .font(.system(size: 12, weight: .semibold))
                    .lineLimit(1)
                if isModified {
                    Circle()
                        .fill(WC.warn)
                        .frame(width: 5, height: 5)
                }
                if !preset.builtin {
                    Button {
                        presetDeleteTarget = preset
                        showPresetDeleteConfirm = true
                    } label: {
                        Image(systemName: "xmark")
                            .font(.system(size: 9, weight: .bold))
                            .foregroundStyle(isActive ? Color.black.opacity(0.5) : WC.muted)
                    }
                    .buttonStyle(.plain)
                    .padding(.leading, 1)
                }
            }
            .foregroundStyle(isActive ? Color.black : WC.txt)
            .padding(.horizontal, 12)
            .padding(.vertical, 7)
            .background(
                isActive ? WC.accent : WC.accent.opacity(0.12),
                in: .rect(cornerRadius: 10)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .stroke(isActive ? WC.accent : WC.accent.opacity(0.30))
            )
        }
        .buttonStyle(.plain)
        .disabled(client.mode != .live)
    }

    @ViewBuilder private var presetRestartNotice: some View {
        OperatorNotice(
            "Preset applied. Some keys require a restart: \(presetApplyRestartKeys.joined(separator: ", "))",
            tint: WC.warn
        )
        .overlay(alignment: .topTrailing) {
            HStack(spacing: 8) {
                Button("Restart now") {
                    showPresetRestartNotice = false
                    showRestartConfirm = true
                }
                .font(.system(size: 11, weight: .bold))
                .foregroundStyle(WC.warn)

                Button {
                    showPresetRestartNotice = false
                } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 10, weight: .bold))
                        .foregroundStyle(WC.muted)
                }
                .buttonStyle(.plain)
            }
            .padding(.trailing, 10)
            .padding(.top, 10)
        }
    }

    /// True when the current Tune control values differ from those last applied via a preset.
    private var isPresetModified: Bool {
        guard !appliedPresetValues.isEmpty else { return false }
        let current = currentTuneValues()
        for (key, appliedVal) in appliedPresetValues {
            guard let curVal = current[key] else { continue }
            if curVal != appliedVal { return true }
        }
        return false
    }

    private func applyPreset(named name: String) {
        Task {
            let result = await client.applyPreset(name: name)
            if let result {
                if result.ok {
                    activePresetName = name
                    // Snapshot the values the backend actually hot-applied so the modified-dot can compare.
                    appliedPresetValues = result.applied
                    // Re-load controls so sliders reflect the preset's values.
                    loaded = false
                    await load()
                    if result.restartRequired && !result.restartKeys.isEmpty {
                        presetApplyRestartKeys = result.restartKeys
                        showPresetRestartNotice = true
                    }
                } else {
                    configError = "Preset not applied. Tap to dismiss."
                }
            } else {
                configError = "Could not reach the Orin to apply preset. Tap to dismiss."
            }
        }
    }

    /// Builds the current Tune control state as a [String: JSONValue] dict, keyed by
    /// the same config keys used in send(_:). Used when saving a preset snapshot.
    private func currentTuneValues() -> [String: JSONValue] {
        var d: [String: JSONValue] = [
            "color.preset":             .string(colorPreset),
            "detector.person_class":    .int(yoloClass),
            "fusion.person_aim_y":      .double(aimY),
            "detector.conf":            .double(conf),
            "fusion.require_person":    .bool(requirePerson),
            "web.show_mask":            .bool(showMask),
            "ptz.max_pan_speed":        .int(Int(maxPan)),
            "ptz.max_tilt_speed":       .int(Int(maxTilt)),
            "ptz.deadzone":             .double(deadzone),
            "ptz.ff_gain":              .double(ffGain),
        ]
        if cinematicAvailable {
            d["ptz.cinematic_zoom_enabled"] = .bool(cinematicEnabled)
            d["ptz.zoom_target_frac"]       = .double(subjectSize)
        }
        if let v = everyN              { d["detector.every_n"]          = .int(v) }
        if let v = lockThreshold       { d["fusion.lock_threshold"]     = .double(v) }
        if let v = unlockThreshold     { d["fusion.unlock_threshold"]   = .double(v) }
        if let v = matchDist           { d["fusion.match_dist"]         = .double(v) }
        if let v = colorMinArea        { d["color.min_area"]            = .int(v) }
        if let v = colorMaxArea        { d["color.max_area"]            = .int(v) }
        if let v = morphKernel         { d["color.morph_kernel"]        = .int(v) }
        if let v = ffDeadzoneMult      { d["ptz.ff_deadzone_mult"]      = .double(v) }
        if let v = ptzMinSpeed         { d["ptz.min_speed"]             = .int(v) }
        if let v = commandMinInterval  { d["ptz.command_min_interval"]  = .double(v) }
        if let v = invertTilt          { d["ptz.invert_tilt"]           = .bool(v) }
        if let v = invertPan           { d["ptz.invert_pan"]            = .bool(v) }
        if let v = jpegQuality         { d["web.jpeg_quality"]          = .int(v) }
        if let h = showHud             { d["web.show_hud"]              = .bool(h) }
        if let v = gpsBoost            { d["fusion.gps_boost"]          = .double(v) }
        if let v = gpsStale            { d["gps.stale_threshold_sec"]   = .double(v) }
        if let v = gpsGrace            { d["gps.grace_sec"]             = .double(v) }
        if let v = gpsDriveZoom        { d["gps.drive_zoom"]            = .bool(v) }
        return d
    }

    // MARK: - Feature-detected advanced cards

    @ViewBuilder private var gpsCard: some View {
        let anyVisible = gpsBoost != nil || gpsStale != nil || gpsGrace != nil || gpsDriveZoom != nil
        if anyVisible {
            OperatorCard(title: "GPS TRACKING") {
                if let b = gpsBoost {
                    sliderRow("GPS lock boost", value: Binding(get: { b }, set: { gpsBoost = $0 }),
                              range: 0.0...0.4, step: 0.05, readout: fmt(b), key: "fusion.gps_boost")
                    tuneCaption("Confidence added to a color blob near frame center while GPS is pointing the camera — lets the rashguard lock at distance where YOLO sees nothing. 0 = off.")
                }
                if let s = gpsStale {
                    if gpsBoost != nil { OperatorDivider() }
                    sliderRow("GPS stale after", value: Binding(get: { s }, set: { gpsStale = $0 }),
                              range: 2...60, step: 1, readout: "\(Int(s))s", key: "gps.stale_threshold_sec", isInt: true)
                    tuneCaption("Max age of the surfer fix before GPS pointing pauses. Lower = never chase old positions; raise only if LoRa updates are slow.")
                }
                if let g = gpsGrace {
                    if gpsBoost != nil || gpsStale != nil { OperatorDivider() }
                    sliderRow("Vision-loss grace", value: Binding(get: { g }, set: { gpsGrace = $0 }),
                              range: 0.5...5.0, step: 0.5, readout: fmt1(g), key: "gps.grace_sec")
                }
                if let dz = gpsDriveZoom {
                    if gpsBoost != nil || gpsStale != nil || gpsGrace != nil { OperatorDivider() }
                    toggleRow("GPS drives zoom", isOn: Binding(get: { dz }, set: { gpsDriveZoom = $0 }), key: "gps.drive_zoom")
                    tuneCaption("Zoom from GPS distance while GPS points the camera. Leave off until the zoom curve is field-tuned.")
                }
            }
        }
    }

    @ViewBuilder private var advancedDetectionCard: some View {
        let anyVisible = everyN != nil || lockThreshold != nil || unlockThreshold != nil || matchDist != nil
        if anyVisible {
            OperatorCard(title: "DETECTION ADVANCED") {
                if let n = everyN {
                    sliderRow("YOLO every N frames", value: Binding(get: { Double(n) }, set: { everyN = Int($0) }),
                              range: 1...30, step: 1, readout: "\(n)", key: "detector.every_n", isInt: true)
                }
                if let lt = lockThreshold {
                    if everyN != nil { OperatorDivider() }
                    sliderRow("Lock threshold", value: Binding(get: { lt }, set: { lockThreshold = $0 }),
                              range: 0.05...0.95, step: 0.05, readout: fmt(lt), key: "fusion.lock_threshold")
                }
                if let ut = unlockThreshold {
                    if everyN != nil || lockThreshold != nil { OperatorDivider() }
                    sliderRow("Unlock threshold", value: Binding(get: { ut }, set: { unlockThreshold = $0 }),
                              range: 0.05...0.95, step: 0.05, readout: fmt(ut), key: "fusion.unlock_threshold")
                }
                if let md = matchDist {
                    if everyN != nil || lockThreshold != nil || unlockThreshold != nil { OperatorDivider() }
                    sliderRow("Color/YOLO match px", value: Binding(get: { md }, set: { matchDist = $0 }),
                              range: 20...500, step: 10, readout: "\(Int(md))", key: "fusion.match_dist")
                }
            }
        }
    }

    @ViewBuilder private var colorCard: some View {
        let anyVisible = colorMinArea != nil || colorMaxArea != nil || morphKernel != nil
        if anyVisible {
            OperatorCard(title: "COLOR") {
                if let mn = colorMinArea {
                    numberRow("Min color blob area", value: Binding(get: { mn }, set: { colorMinArea = $0 }),
                              bounds: 1...500_000, key: "color.min_area")
                }
                if let mx = colorMaxArea {
                    if colorMinArea != nil { OperatorDivider() }
                    numberRow("Max color blob area", value: Binding(get: { mx }, set: { colorMaxArea = $0 }),
                              bounds: 100...1_000_000, key: "color.max_area")
                }
                if let mk = morphKernel {
                    if colorMinArea != nil || colorMaxArea != nil { OperatorDivider() }
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
            OperatorCard(title: "MOTION ADVANCED") {
                if let m = ffDeadzoneMult {
                    sliderRow("FF deadband mult", value: Binding(get: { m }, set: { ffDeadzoneMult = $0 }),
                              range: 1...4, step: 0.1, readout: fmt1(m), key: "ptz.ff_deadzone_mult")
                }
                if let s = ptzMinSpeed {
                    if ffDeadzoneMult != nil { OperatorDivider() }
                    sliderRow("Min speed", value: Binding(get: { Double(s) }, set: { ptzMinSpeed = Int($0) }),
                              range: 1...8, step: 1, readout: "\(s)", key: "ptz.min_speed", isInt: true)
                }
                if let ci = commandMinInterval {
                    if ffDeadzoneMult != nil || ptzMinSpeed != nil { OperatorDivider() }
                    sliderRow("Command interval", value: Binding(get: { ci }, set: { commandMinInterval = $0 }),
                              range: 0.01...0.5, step: 0.01, readout: fmt(ci), key: "ptz.command_min_interval")
                }
                if let it = invertTilt {
                    if ffDeadzoneMult != nil || ptzMinSpeed != nil || commandMinInterval != nil { OperatorDivider() }
                    toggleRow("Invert tilt", isOn: Binding(get: { it }, set: { invertTilt = $0 }), key: "ptz.invert_tilt")
                }
                if let ip = invertPan {
                    if ffDeadzoneMult != nil || ptzMinSpeed != nil || commandMinInterval != nil || invertTilt != nil { OperatorDivider() }
                    toggleRow("Invert pan", isOn: Binding(get: { ip }, set: { invertPan = $0 }), key: "ptz.invert_pan")
                }
                if let jq = jpegQuality {
                    if ffDeadzoneMult != nil || ptzMinSpeed != nil || commandMinInterval != nil || invertTilt != nil || invertPan != nil { OperatorDivider() }
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
            .onChange(of: selection.wrappedValue) { _, v in if let jv = JSONValue.from(v) { send([key: jv]) } }
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
                if !editing { send([key: isInt ? .int(Int(value.wrappedValue)) : .double(value.wrappedValue)]) }
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
        .onChange(of: isOn.wrappedValue) { _, v in send([key: .bool(v)]) }
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
            .onChange(of: value.wrappedValue) { _, v in send([key: .int(v)]) }
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

        // Load presets feature flag + preset list independently of the config fetch.
        // Feature-detect: show in mock mode (always demoable) or when backend signals support.
        async let cfgTask = client.config()
        async let presetsTask = client.presets()

        guard let cfg = await cfgTask else { return }

        let presetsEnabled = client.mode == .mock || (cfg.supported?.presets == true)
        presetsSupported = presetsEnabled
        if presetsEnabled, let fetchedPresets = await presetsTask {
            tunePresets = fetchedPresets
        }
        colorPreset = cfg.current.color.preset
        yoloClass = cfg.current.detector.personClass
        aimY = cfg.current.fusion.personAimY
        conf = cfg.current.detector.conf
        requirePerson = cfg.current.fusion.requirePerson
        showMask = cfg.current.web.showMask
        showHud = cfg.current.web.showHud
        maxPan = Double(cfg.current.ptz.maxPanSpeed)
        maxTilt = Double(cfg.current.ptz.maxTiltSpeed)
        deadzone = cfg.current.ptz.deadzone
        ffGain = cfg.current.ptz.ffGain
        model = cfg.current.detector.model
        restartKeys = cfg.restartRequiredKeys ?? []
        // Feature-detect strictly on the backend's advertised flag (now shipped). Never
        // infer support from a value being present — a stale field would false-activate
        // the control, violating the feature-detection invariant.
        if cfg.supported?.cinematicZoom == true {
            cinematicAvailable = true
            cinematicEnabled = cfg.current.ptz.cinematicZoomEnabled ?? false
            subjectSize = cfg.current.ptz.zoomTargetFrac ?? 0.5
        }
        // Feature-detected advanced keys — remain nil when backend doesn't expose them
        everyN = cfg.current.detector.everyN
        lockThreshold = cfg.current.fusion.lockThreshold
        unlockThreshold = cfg.current.fusion.unlockThreshold
        matchDist = cfg.current.fusion.matchDist
        gpsBoost = cfg.current.fusion.gpsBoost
        gpsStale = cfg.current.gps?.staleThresholdSec
        gpsGrace = cfg.current.gps?.graceSec
        gpsDriveZoom = cfg.current.gps?.driveZoom
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

    private func send(_ patch: [String: JSONValue]) {
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

#Preview {
    TuneView()
        .environment(WaveCamClient(mode: .mock))
        .preferredColorScheme(.dark)
}
