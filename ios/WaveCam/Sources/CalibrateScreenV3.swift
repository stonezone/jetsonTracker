import SwiftUI
import MapKit

/// Calibration v3 — ONE screen. Pinned Exit + KILL (never covered), an always-on map of
/// base + tracker, and the flow inline (no modal sheets, no hard blockers): set
/// location+height+heading, aim on the Live tab (feed + zoom), capture/refine the offset,
/// validate, confirm → tracking. A Settings disclosure re-edits the pose afterward.
/// Self-contained: drives the calibration endpoints directly.
struct CalibrateScreenV3: View {
    @Environment(WaveCamClient.self) private var client
    @State private var session: WCCalibrationSessionState?
    @State private var busy = false
    @State private var note: String?
    @State private var showRefine = false   // in a multi-point refine sequence (show the readout)

    @State private var camPos: MapCameraPosition = .automatic
    @State private var mapCenter = CLLocationCoordinate2D(latitude: 21.6808, longitude: -158.0364)
    @State private var headingDeg: Double = 0
    @State private var cameraHeightFt = "0"         // camera height above the water (ft); foiler = water = 0
    @State private var showSettings = false

    private var active: Bool { session?.active == true }
    private var killed: Bool { client.status?.safety.killed == true }
    private var bannerText: String { session?.banner ?? (active ? "CALIBRATE" : "IDLE") }

    var body: some View {
        VStack(spacing: 0) {
            header                          // pinned — Exit + KILL never scroll away
            if let note { noteBar(note) }   // errors prominent under the header, not buried at the bottom
            ScrollView {
                VStack(spacing: 10) {
                    mapPanel.frame(height: 200)
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
                .padding(12)
                .controlSize(.small)   // denser buttons/pickers/fields throughout the workflow
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
            Text("Set location, heading, and tracker offset.")
                .font(.caption).foregroundStyle(.secondary)
            Button { run { await client.calibrateSessionStart() } } label: {
                Text("Enter CALIBRATE").frame(maxWidth: .infinity)
            }.buttonStyle(.borderedProminent).disabled(busy)
        }.cardBG()
    }

    private var locationHeightCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            cardTitle("1 · Location + height")
            Button { usePhoneLocation() } label: {
                Label("Center map on my phone", systemImage: "location.fill").frame(maxWidth: .infinity)
            }.buttonStyle(.bordered).disabled(busy)
            Text("Crosshair = tripod spot.").font(.caption2).foregroundStyle(.secondary)
            heightField("Camera height above water (ft)", $cameraHeightFt)
            Text(depressionHint).font(.caption2).foregroundStyle(.secondary)
            Button { setLocationAndHeight() } label: {
                Text("Set location + height").frame(maxWidth: .infinity)
            }.buttonStyle(.borderedProminent).disabled(busy || killed)
        }.cardBG()
    }

    private var headingCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            cardTitle("2 · Heading")
            Text("Camera's forward bearing.").font(.caption2).foregroundStyle(.secondary)
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
        VStack(alignment: .leading, spacing: 6) {
            cardTitle("3 · Aim + capture")
            Text("Aim on Live (view + zoom, 50+ m), then Capture. Refine +1 from new spots to tighten.")
                .font(.caption2).foregroundStyle(.secondary)
            if showRefine, let hl = session?.session?.headingLock, let n = hl.sampleCount {
                Text("refined: \(n) aim\(n == 1 ? "" : "s") · residual \(hl.rmsResidualDeg.map { String(format: "%.1f°", $0) } ?? "—")")
                    .font(.caption2.weight(.semibold)).foregroundStyle(WC.ok)
            }
            HStack {
                Button { captureOffset() } label: {
                    Text("Capture").frame(maxWidth: .infinity)
                }.buttonStyle(.borderedProminent)
                Button { refineOffset() } label: {
                    Text("Refine +1").frame(maxWidth: .infinity)
                }.buttonStyle(.bordered)
            }.disabled(busy || killed)
            if showRefine {
                Button { resetRefine() } label: {
                    Text("Reset refine").frame(maxWidth: .infinity)
                }.buttonStyle(.plain).font(.caption2).foregroundStyle(WC.muted).disabled(busy)
            }
        }.cardBG()
    }

    private var validateCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            cardTitle("4 · Validate + confirm")
            if let v = session?.session?.validation, let miss = v.missDeg {
                Text(String(format: "last miss: %.1f°", miss)).font(.caption2).foregroundStyle(.secondary)
            }
            HStack {
                Button { run { await client.calibrateValidation(bearingDeg: client.status?.gps?.bearingDeg ?? headingDeg,
                                                                distanceM: client.status?.gps?.distanceM) } } label: {
                    Text("Validate").frame(maxWidth: .infinity)
                }.buttonStyle(.bordered)
                Button { confirmAndFinish() } label: {
                    Text("Confirm & finish").frame(maxWidth: .infinity)
                }.buttonStyle(.borderedProminent)
            }.disabled(busy || killed)
            Text("Validate a check-point, then Confirm — commits VALID + exits to GPS tracking.")
                .font(.caption2).foregroundStyle(.secondary)
        }.cardBG()
    }

    private var settingsDisclosure: some View {
        DisclosureGroup(isExpanded: $showSettings) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Re-apply after moving the tripod. Reloads live.")
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
        // The foiler is on the water (subject = 0); the only unknown is the camera's height above
        // it. Down-tilt = atan2(0 − camHeight, dist) (matches the backend's subject_alt_m − alt_m).
        // The height delta dominates UP CLOSE and is ~nil offshore, so preview a near range too.
        let camM = (Double(cameraHeightFt) ?? 0) * 0.3048
        let at15 = -GeoMath.elevationDeg(baseAltM: camM, distanceM: 15, subjectAltM: 0)
        let at100 = -GeoMath.elevationDeg(baseAltM: camM, distanceM: 100, subjectAltM: 0)
        return String(format: "≈%.0f° down @15 m · %.0f° @100 m", at15, at100)
    }

    /// The phone's own GPS fix, as posted to the backend at 1 Hz by PhoneSensorPublisher
    /// (independent of the base Wio). nil until Location is authorized + a fix lands.
    private var phoneCoord: CLLocationCoordinate2D? {
        guard let la = client.status?.sensors?.phone?.lat, let lo = client.status?.sensors?.phone?.lon else { return nil }
        return CLLocationCoordinate2D(latitude: la, longitude: lo)
    }

    /// Quick-start that bypasses the base Wio: drop the crosshair on the phone's location,
    /// then the operator drags to the exact tripod spot. onMapCameraChange keeps mapCenter
    /// in sync, so "Set base location + height" commits wherever they end up.
    private func usePhoneLocation() {
        guard let c = phoneCoord else {
            note = "No phone location yet — allow Location for WaveCam and give it a moment to get a fix."
            return
        }
        mapCenter = c
        camPos = .region(MKCoordinateRegion(center: c, latitudinalMeters: 150, longitudinalMeters: 150))
    }

    private func setLocationAndHeight() {
        // Camera height above the water → alt_m (meters); subject fixed at the water = 0, so the
        // backend's depression atan2(subject_alt_m − alt_m, dist) = atan2(−camHeight, dist) (down).
        let altM = (Double(cameraHeightFt) ?? 0) * 0.3048
        let lat = mapCenter.latitude, lon = mapCenter.longitude
        run { await client.calibrateLocationManual(lat: lat, lon: lon, errorRadiusM: 5, altM: altM, subjectAltM: 0) }
    }

    /// Confirm marks VALID but leaves owner=calibrate (arbiter locked out); the session must
    /// also EXIT to hand PTZ back so the arbiter can select gps_tracker. Chain them so
    /// "Confirm" means done + tracking — otherwise the operator sees VALID and no tracking.
    /// calibration_valid (valid ∧ confirmed) survives the exit, so tracking starts after it.
    private func confirmAndFinish() {
        guard !busy else { return }
        Task {
            busy = true; note = nil; defer { busy = false }
            switch await client.calibrateValidationConfirm(accepted: true) {
            case .success:
                switch await client.calibrateSessionExit(confirm: true) {
                case .success(let s): session = s
                case .failure(let e): note = "Confirmed VALID, but exit failed — tap Exit to start tracking: \(e.localizedDescription)"
                }
            case .failure(let e):
                note = "Failed: \(e.localizedDescription)"
            }
        }
    }

    // Capture = single baseline aim (clears refine accumulation). Refine = add this aim to the
    // multi-point least-squares fit. Aim on the Live tab first; the camera holds where it points.
    private func captureOffset() {
        showRefine = false
        run { await client.calibrateOffset(step3BearingDeg: headingDeg, mode: "replace") }
    }

    private func refineOffset() {
        showRefine = true
        run { await client.calibrateOffset(step3BearingDeg: headingDeg, mode: "accumulate") }
    }

    private func resetRefine() {
        guard !busy else { return }
        showRefine = false
        Task {
            busy = true; note = nil; defer { busy = false }
            _ = await client.calibrateOffsetReset()
        }
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
        self.padding(10)
            .background(RoundedRectangle(cornerRadius: 10).fill(Color.white.opacity(0.05)))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.white.opacity(0.1)))
    }
}
