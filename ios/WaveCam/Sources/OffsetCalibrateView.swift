import SwiftUI
import MapKit

/// Calibration v2 step 4: walk the tracker 50-100 m out (4a — confirm a stable fix), then
/// return to base, frame it, and capture (4b). The backend re-anchors pan+tilt from its own
/// live tracker fix. Base + tracker render on the map for a visual geometry check. Skipping
/// is allowed but produces an explicitly-labeled coarse calibration.
struct OffsetCalibrateView: View {
    @State private var model = OffsetCalibrateModel()
    let client: WaveCamClient
    let step3BearingDeg: Double?
    let onDone: (WCCalibrationSessionState?) -> Void
    @State private var busy = false
    @State private var message: String?
    @State private var capturedOffset: Double?

    /// Offset the capture will set = (base→tracker bearing) − the coarse step-3 heading.
    /// base→tracker bearing == status.gps.bearingDeg, so it previews before capture.
    private var previewOffset: Double? {
        guard let s3 = step3BearingDeg, let b = model.bearingDeg else { return nil }
        let d = (b - s3).truncatingRemainder(dividingBy: 360)
        return d > 180 ? d - 360 : (d <= -180 ? d + 360 : d)
    }

    private var baseHeightWarning: Bool {
        guard let d = model.distanceM else { return false }
        return abs(GeoMath.elevationDeg(baseAltM: model.baseHeightM, distanceM: d)) > 30 && d > 50
    }

    var body: some View {
        VStack(spacing: 12) {
            Text("Walk the tracker 50–100 m out with a stable fix, then return to base and frame it dead-center.")
                .font(.caption).foregroundStyle(.secondary)

            qualityPanel
            map.frame(minHeight: 220)

            if let o = capturedOffset ?? previewOffset {
                offsetReadout(o)
            }

            captureControls
            if let message { Text(message).font(.footnote).foregroundStyle(.secondary) }
        }
        .padding()
        .task { await pollStatus() }
    }

    @ViewBuilder private var qualityPanel: some View {
        HStack(spacing: 14) {
            stat("sats", model.targetSats.map { "\($0)" } ?? "—")
            stat("dist", model.distanceM.map { String(format: "%.0f m", $0) } ?? "—")
            stat("age", model.targetAgeSec.map { String(format: "%.0fs", $0) } ?? "—")
        }.font(.footnote)
        if let g = model.gateMessage {
            Label(g, systemImage: "exclamationmark.triangle.fill").font(.footnote).foregroundStyle(.orange)
        } else {
            Label("Tracker fix is stable — ready to capture", systemImage: "checkmark.seal.fill")
                .font(.footnote).foregroundStyle(.green)
        }
    }

    private func stat(_ k: String, _ v: String) -> some View {
        VStack { Text(v).bold(); Text(k).font(.caption2).foregroundStyle(.secondary) }
    }

    private var map: some View {
        Map {
            if let bla = model.baseLat, let blo = model.baseLon {
                Marker("Base", coordinate: CLLocationCoordinate2D(latitude: bla, longitude: blo))
            }
            if let t = model.trackerCoord {
                Marker("Tracker", coordinate: CLLocationCoordinate2D(latitude: t.lat, longitude: t.lon)).tint(.orange)
            }
        }
        .mapStyle(.hybrid)
    }

    @ViewBuilder private func offsetReadout(_ o: Double) -> some View {
        let band = model.offsetBand(o)
        let color: Color = band == .small ? .green : (band == .moderate ? .yellow : .red)
        VStack(spacing: 2) {
            Text(String(format: "offset %+.1f°", o)).bold().foregroundStyle(color)
            if band == .large {
                Text("Large — tracker may be too close, mis-aimed, or base height is wrong.")
                    .font(.caption2).foregroundStyle(.red)
            }
            if baseHeightWarning {
                Text("Base height looks wrong for this distance — re-check it.")
                    .font(.caption2).foregroundStyle(.red)
            }
        }
    }

    @ViewBuilder private var captureControls: some View {
        Button {
            Task {
                busy = true; message = nil; defer { busy = false }
                capturedOffset = previewOffset
                switch await client.calibrateOffset(step3BearingDeg: step3BearingDeg) {
                case .success(let state): onDone(state)
                case .failure(let e): message = "Failed: \(e.localizedDescription)"
                }
            }
        } label: { Text("Capture — re-anchor pan+tilt").frame(maxWidth: .infinity) }
            .buttonStyle(.borderedProminent)
            .disabled(!model.canCapture || busy)

        Button("Skip — coarse mode (heading uncalibrated)") { onDone(nil) }
            .font(.footnote).foregroundStyle(.orange).disabled(busy)
    }

    private func pollStatus() async {
        while !Task.isCancelled {
            sync()
            try? await Task.sleep(for: .seconds(1))
        }
    }

    private func sync() {
        let s = client.status
        model.baseLat = s?.sensors?.base?.lat
        model.baseLon = s?.sensors?.base?.lon
        if let h = s?.sensors?.base?.altM { model.baseHeightM = h }
        model.targetSats = s?.gps?.targetSats
        model.targetAgeSec = s?.gps?.targetAgeSec
        model.stale = s?.gps?.stale
        model.distanceM = s?.gps?.distanceM
        model.bearingDeg = s?.gps?.bearingDeg
    }
}
