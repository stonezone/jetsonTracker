import SwiftUI
import MapKit

/// Calibration v3 — ONE screen. Pinned Exit + KILL (never covered), an always-on map of
/// base + tracker, and the flow inline (no modal sheets, no hard blockers): set
/// location+height+heading, aim the camera at the tracker with the embedded joystick,
/// capture the offset, validate, confirm → tracking. A Settings disclosure re-edits the
/// pose afterward. Self-contained: drives the calibration endpoints directly.
struct CalibrateScreenV3: View {
    @Environment(WaveCamClient.self) private var client
    @State private var ptz = PTZManualController()
    @State private var knob: CGSize = .zero
    @State private var session: WCCalibrationSessionState?
    @State private var busy = false
    @State private var note: String?

    @State private var camPos: MapCameraPosition = .automatic
    @State private var mapCenter = CLLocationCoordinate2D(latitude: 21.6808, longitude: -158.0364)
    @State private var headingDeg: Double = 0
    @State private var datumSeaLevel = false        // false = relative-to-base
    @State private var baseHeight = "0"             // sea-level base ASL (ignored in base-relative)
    @State private var subjectHeight = "-1"         // tracker: relative offset, or sea-level ASL
    @State private var showSettings = false

    private var active: Bool { session?.active == true }
    private var killed: Bool { client.status?.safety.killed == true }
    private var bannerText: String { session?.banner ?? (active ? "CALIBRATE" : "IDLE") }

    var body: some View {
        VStack(spacing: 0) {
            header                          // pinned — Exit + KILL never scroll away
            if let note { noteBar(note) }   // errors prominent under the header, not buried at the bottom
            ScrollView {
                VStack(spacing: 14) {
                    mapPanel.frame(height: 240)
                    if active {
                        locationHeightCard
                        headingCard
                        aimCard
                        validateCard
                        settingsDisclosure
                    } else {
                        enterCard
                    }
                }
                .padding(16)
            }
            .scrollIndicators(.hidden)
        }
        .background(WC.bg.ignoresSafeArea())
        .environment(client)
        // Seed from the backend so the banner reflects an already-active session
        // (navigating away and back) instead of showing IDLE.
        .task { if session == nil { session = await client.calibrationSessionState() } }
    }

    /// COR5: surface a failed action where the operator is looking, not as a footnote
    /// that scrolls out of view below the cards.
    private func noteBar(_ text: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill")
            Text(text).font(.footnote.weight(.medium))
            Spacer()
            Button { note = nil } label: { Image(systemName: "xmark") }.buttonStyle(.plain)
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 16).padding(.vertical, 8)
        .background(WC.kill)
    }

    // MARK: pinned header
    private var header: some View {
        HStack(spacing: 10) {
            Text(bannerText)
                .font(.headline).bold()
                .foregroundStyle(bannerText == "VALID" ? WC.ok : (active ? WC.warn : WC.muted))
            Spacer()
            if active {
                Button("Exit") { run { await client.calibrateSessionExit() } }
                    .buttonStyle(.bordered).disabled(busy)
            }
            EmergencyStopButton(style: .compact)
        }
        .padding(.horizontal, 16).padding(.vertical, 10)
        .background(WC.bg)
        .overlay(alignment: .bottom) { Rectangle().fill(.white.opacity(0.1)).frame(height: 1) }
    }

    // MARK: live map (base + tracker)
    private var baseCoord: CLLocationCoordinate2D? {
        if let la = client.status?.sensors?.base?.lat, let lo = client.status?.sensors?.base?.lon {
            return CLLocationCoordinate2D(latitude: la, longitude: lo)
        }
        return nil
    }
    private var trackerCoord: CLLocationCoordinate2D? {
        guard let b = baseCoord, let d = client.status?.gps?.distanceM, let brg = client.status?.gps?.bearingDeg else { return nil }
        let t = GeoMath.destination(fromLat: b.latitude, fromLon: b.longitude, bearingDeg: brg, distanceM: d)
        return CLLocationCoordinate2D(latitude: t.lat, longitude: t.lon)
    }

    private var mapPanel: some View {
        VStack(spacing: 4) {
            Map(position: $camPos) {
                if let b = baseCoord { Marker("Base", coordinate: b).tint(.blue) }
                if let t = trackerCoord { Marker("Tracker", coordinate: t).tint(.orange) }
            }
            .mapStyle(.hybrid)
            .onMapCameraChange { ctx in mapCenter = ctx.region.center }
            .overlay { Image(systemName: "plus").font(.title3).foregroundStyle(.white).shadow(radius: 2) }
            .clipShape(RoundedRectangle(cornerRadius: 10))
            statsLine
        }
    }
    private var statsLine: some View {
        let g = client.status?.gps
        return Text(statsText(g)).font(.caption2).foregroundStyle(.secondary)
    }
    private func statsText(_ g: WCStatus.GPS?) -> String {
        let dist = g?.distanceM.map { String(format: "%.0f m", $0) } ?? "—"
        let brg = g?.bearingDeg.map { String(format: "%.0f°", $0) } ?? "—"
        let sats = g?.targetSats.map { "\($0)" } ?? "—"
        let age = g?.targetAgeSec.map { String(format: "%.0fs", $0) } ?? "—"
        return "tracker: \(dist) @ \(brg)  ·  sats \(sats)  ·  fix \(age)"
    }

    // MARK: cards
    private var enterCard: some View {
        VStack(spacing: 10) {
            Text("Start a calibration session to set location, height, heading, and the tracker offset.")
                .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
            Button { run { await client.calibrateSessionStart() } } label: {
                Text("Enter CALIBRATE").frame(maxWidth: .infinity)
            }.buttonStyle(.borderedProminent).disabled(busy)
        }.cardBG()
    }

    private var locationHeightCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            cardTitle("1 · Location + height")
            Text("Pan the map so the crosshair is on the real tripod spot.")
                .font(.caption2).foregroundStyle(.secondary)
            Picker("Datum", selection: $datumSeaLevel) {
                Text("Relative to base").tag(false)
                Text("Above sea level").tag(true)
            }.pickerStyle(.segmented)
            if datumSeaLevel {
                heightField("Base height (ASL m)", $baseHeight)
                heightField("Tracker height (ASL m)", $subjectHeight)
            } else {
                Text("Base = 0. Enter the tracker's height relative to the base (e.g. −1 if the camera is 1 m up and the tracker's on the ground).")
                    .font(.caption2).foregroundStyle(.secondary)
                heightField("Tracker vs base (m)", $subjectHeight)
            }
            Text(depressionHint).font(.caption2).foregroundStyle(.secondary)
            Button { setLocationAndHeight() } label: {
                Text("Set base location + height").frame(maxWidth: .infinity)
            }.buttonStyle(.borderedProminent).disabled(busy || killed)
        }.cardBG()
    }

    private var headingCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            cardTitle("2 · Heading")
            Text("Slide to the camera's forward bearing, or type it from a compass/nav. (GPS bearing to the tracker is shown above.)")
                .font(.caption2).foregroundStyle(.secondary)
            HStack {
                Slider(value: $headingDeg, in: 0...360, step: 1)
                Text(String(format: "%.0f°", headingDeg)).font(.footnote).frame(width: 44)
            }
            Button { run { await client.calibrateHeadingLockAccept(bearingDeg: headingDeg, distanceM: nil) } } label: {
                Text("Set heading \(Int(headingDeg))°").frame(maxWidth: .infinity)
            }.buttonStyle(.bordered).disabled(busy || killed)
        }.cardBG()
    }

    private var aimCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            cardTitle("3 · Aim at the tracker + capture")
            Text("Use the joystick to put the tracker dead-center (50+ m out), then capture — it sets the pan+tilt offset from the tracker's GPS.")
                .font(.caption2).foregroundStyle(.secondary)
            HStack {
                Spacer()
                JoystickPad(knobOffset: $knob, diameter: 150,
                            onCommand: { p, t in ptz.sendVelocity(pan: p, tilt: t, client: client) },
                            onStop: { ptz.holdPTZ(client: client) })
                Spacer()
            }
            Button { run { await client.calibrateOffset(targetLat: nil, targetLon: nil,
                                                         step3BearingDeg: headingDeg) } } label: {
                Text("Capture offset (re-anchor pan+tilt)").frame(maxWidth: .infinity)
            }.buttonStyle(.borderedProminent).disabled(busy || killed)
        }.cardBG()
    }

    private var validateCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            cardTitle("4 · Validate + confirm")
            if let v = session?.session?.validation, let miss = v.missDeg {
                Text(String(format: "last miss: %.1f°", miss)).font(.caption2).foregroundStyle(.secondary)
            }
            HStack {
                Button { run { await client.calibrateValidation(bearingDeg: client.status?.gps?.bearingDeg ?? headingDeg,
                                                                distanceM: client.status?.gps?.distanceM) } } label: {
                    Text("Validate").frame(maxWidth: .infinity)
                }.buttonStyle(.bordered)
                Button { run { await client.calibrateValidationConfirm(accepted: true) } } label: {
                    Text("Confirm → VALID").frame(maxWidth: .infinity)
                }.buttonStyle(.borderedProminent)
            }.disabled(busy || killed)
            Text("Validate first (sight a check-point), then Confirm. The miss is advisory — Confirm always commits; it just shows aim accuracy.")
                .font(.caption2).foregroundStyle(.secondary)
        }.cardBG()
    }

    private var settingsDisclosure: some View {
        DisclosureGroup(isExpanded: $showSettings) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Re-apply the location + height above (e.g. after moving the tripod) or re-set the heading. Changes reload live.")
                    .font(.caption2).foregroundStyle(.secondary)
                Button { setLocationAndHeight() } label: {
                    Text("Re-apply location + height").frame(maxWidth: .infinity)
                }.buttonStyle(.bordered).disabled(busy)
            }.padding(.top, 6)
        } label: {
            Text("Calibration settings").font(.subheadline).bold()
        }.cardBG()
    }

    // MARK: helpers
    private var depressionHint: String {
        // Datum-consistent: base-relative → base=0 is the GROUND reference (not sea level)
        // and subj is the tracker's offset from it; sea-level → both are ASL. atan2 gives the
        // correct depression in any datum as long as base and subj share one (matches the
        // backend's pose.subject_alt_m − pose.alt_m). Mixing datums is the −63° dive trap.
        let base = datumSeaLevel ? (Double(baseHeight) ?? 0) : 0
        let subj = Double(subjectHeight) ?? (datumSeaLevel ? 1 : -1)
        return String(format: "camera looks ≈%.0f° down at 100 m", -GeoMath.elevationDeg(baseAltM: base, distanceM: 100, subjectAltM: subj))
    }

    private func setLocationAndHeight() {
        let alt = datumSeaLevel ? (Double(baseHeight) ?? 0) : 0.0
        let subj = Double(subjectHeight) ?? (datumSeaLevel ? 1.0 : -1.0)
        let lat = mapCenter.latitude, lon = mapCenter.longitude
        run { await client.calibrateLocationManual(lat: lat, lon: lon, errorRadiusM: 5, altM: alt, subjectAltM: subj) }
    }

    private func run(_ op: @escaping () async -> Result<WCCalibrationSessionState, WaveCamCalibrationError>) {
        guard !busy else { return }
        Task {
            busy = true; note = nil; defer { busy = false }
            switch await op() {
            case .success(let s): session = s
            case .failure(let e): note = "Failed: \(e.localizedDescription)"
            }
        }
    }

    private func cardTitle(_ s: String) -> some View {
        Text(s).font(.subheadline).bold().foregroundStyle(.primary)
    }
    private func heightField(_ label: String, _ binding: Binding<String>) -> some View {
        HStack {
            Text(label).font(.footnote)
            Spacer()
            TextField("m", text: binding).keyboardType(.numbersAndPunctuation)
                .textFieldStyle(.roundedBorder).frame(width: 80).multilineTextAlignment(.trailing)
        }
    }
}

private extension View {
    func cardBG() -> some View {
        self.padding(12)
            .background(RoundedRectangle(cornerRadius: 10).fill(Color.white.opacity(0.05)))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.white.opacity(0.1)))
    }
}
