import SwiftUI

/// Native tuning panel -- parity with the :8088 web UI. Every setting here is
/// "hot": it applies on the next frame via POST /config/hot, no restart needed.
/// Loads current values once on appear, then pushes each change live.
struct TuneView: View {
    @Environment(WaveCamClient.self) private var client

    @State private var loaded = false
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

    private let presets = ["orange_red", "orange", "blue", "green", "yellow", "pink"]
    private let classes: [(id: Int, name: String)] = [
        (0, "person"), (1, "bicycle"), (2, "car"), (3, "motorcycle"),
        (14, "bird"), (15, "cat"), (16, "dog"), (32, "sports ball"),
        (37, "surfboard"), (41, "cup"),
    ]

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                header

                TuneCard(title: "TARGET") {
                    pickerRow("Color preset", selection: $colorPreset, options: presets.map { ($0, $0) }, key: "color.preset")
                    TuneDivider()
                    pickerRow("YOLO target", selection: $yoloClass, options: classes.map { ($0.id, $0.name) }, key: "detector.person_class")
                    TuneDivider()
                    sliderRow("Aim point", value: $aimY, range: 0.2...0.75, step: 0.01, readout: aimLabel(aimY), key: "fusion.person_aim_y")
                }

                TuneCard(title: "DETECTION") {
                    sliderRow("YOLO confidence", value: $conf, range: 0.05...0.95, step: 0.05, readout: fmt(conf), key: "detector.conf")
                    TuneDivider()
                    toggleRow("Require YOLO person", isOn: $requirePerson, key: "fusion.require_person")
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

                Text("Changes apply live (no restart). Camera/model changes that need a restart aren't shown here yet.")
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
    }

    @ViewBuilder private var header: some View {
        if client.mode != .live {
            TuneNotice("Switch to Live mode on the Connect tab to tune.", tint: WC.warn)
        } else if !loaded {
            TuneNotice(client.connected ? "Loading current settings..." : "Connecting to the Orin...", tint: WC.muted)
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
            .tint(WC.brand)
            .onChange(of: selection.wrappedValue) { _, v in send([key: v]) }
        }
    }

    @ViewBuilder
    private func sliderRow(_ label: String, value: Binding<Double>, range: ClosedRange<Double>, step: Double, readout: String, key: String, isInt: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(label).font(.system(size: 13, weight: .medium)).foregroundStyle(WC.txt)
                Spacer()
                Text(readout).font(.system(size: 13, weight: .semibold, design: .monospaced)).foregroundStyle(WC.brand)
            }
            Slider(value: value, in: range, step: step) { editing in
                if !editing { send([key: isInt ? Int(value.wrappedValue) : value.wrappedValue]) }
            }
            .tint(WC.brand)
        }
    }

    @ViewBuilder
    private func toggleRow(_ label: String, isOn: Binding<Bool>, key: String) -> some View {
        Toggle(isOn: isOn) {
            Text(label).font(.system(size: 13, weight: .medium)).foregroundStyle(WC.txt)
        }
        .tint(WC.ok)
        .onChange(of: isOn.wrappedValue) { _, v in send([key: v]) }
    }

    private func fmt(_ v: Double) -> String { v.formatted(.number.precision(.fractionLength(2))) }

    private func aimLabel(_ v: Double) -> String {
        if v <= 0.25 { return "HEAD" }
        if v <= 0.4 { return "CHEST" }
        return "CENTER \(fmt(v))"
    }

    private func load() async {
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
        loaded = true
    }

    private func send(_ patch: [String: Any]) {
        guard loaded, client.mode == .live else { return }
        Task { await client.configHot(patch) }
    }
}

private struct TuneCard<Content: View>: View {
    let title: String
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title).font(.system(size: 10, weight: .semibold)).tracking(1.5).foregroundStyle(WC.faint)
            content
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(WC.panel, in: .rect(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).stroke(WC.line))
    }
}

private struct TuneDivider: View {
    var body: some View { Divider().overlay(WC.line) }
}

private struct TuneNotice: View {
    let text: String
    let tint: Color

    init(_ text: String, tint: Color) {
        self.text = text
        self.tint = tint
    }

    var body: some View {
        Text(text)
            .font(.system(size: 12, weight: .medium))
            .foregroundStyle(tint)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(12)
            .background(tint.opacity(0.12), in: .rect(cornerRadius: 12))
    }
}

#Preview {
    TuneView()
        .environment(WaveCamClient(mode: .mock))
        .preferredColorScheme(.dark)
}
