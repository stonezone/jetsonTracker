import SwiftUI
import MapKit

/// Place the camera base + set heading on Apple satellite imagery, bypassing GPS
/// noise. Presented from CalibrateView inside an active CALIBRATE session. The map
/// center sits under a fixed crosshair; "confirm" writes via WaveCamClient. Failures
/// (incl. a dropped CALIBRATE session, V7) surface as a message — nothing silent.
struct MapPlacementView: View {
    @State private var model = MapPlacementModel()
    let client: WaveCamClient
    let initialLat: Double
    let initialLon: Double
    let purpose: MapPlacementModel.Mode
    let onDone: (WCCalibrationSessionState?) -> Void
    @State private var busy = false
    @State private var message: String?

    var body: some View {
        VStack(spacing: 12) {
            if purpose != .base {
                Picker("", selection: $model.mode) {
                    Text("Look-at").tag(MapPlacementModel.Mode.headingLookAt)
                    Text("Arrow").tag(MapPlacementModel.Mode.headingArrow)
                }
                .pickerStyle(.segmented)
            }
            MapKitContainer(model: model, initialLat: initialLat, initialLon: initialLon)
                .overlay(crosshair)
                .overlay(arrowOverlay)
            if !model.tilesLoaded {
                Label("Map imagery not loaded — connect to load satellite tiles", systemImage: "wifi.slash")
                    .font(.footnote).foregroundStyle(.orange)
            }
            controls
            if let message { Text(message).font(.footnote).foregroundStyle(.secondary) }
        }
        .padding()
        .onAppear {
            model.mode = purpose
            if purpose == .headingLookAt { model.baseLat = initialLat; model.baseLon = initialLon }
        }
    }

    @ViewBuilder private var crosshair: some View {
        if model.mode != .headingArrow {
            Image(systemName: "plus").font(.title2).foregroundStyle(.white).shadow(radius: 2)
        }
    }

    @ViewBuilder private var arrowOverlay: some View {
        if model.mode == .headingArrow {
            Image(systemName: "location.north.fill")
                .font(.largeTitle).foregroundStyle(.cyan)
                .rotationEffect(.degrees(model.arrowBearingDeg))
        }
    }

    @ViewBuilder private var controls: some View {
        switch model.mode {
        case .base:
            Text("Center the crosshair on the real tripod spot.")
                .font(.caption).foregroundStyle(.secondary)
            Button {
                Task { await confirm { await client.calibrateLocationManual(lat: model.baseLat ?? initialLat,
                                                                            lon: model.baseLon ?? initialLon,
                                                                            errorRadiusM: model.lastErrorRadiusM) } }
            } label: { Text("Use this location (±\(Int(model.lastErrorRadiusM)) m)") }
                .disabled(!model.canConfirmLocation || busy)

        case .headingLookAt:
            Text("Aim the camera at a distant landmark in Live, hold it still, center the crosshair on that same landmark.")
                .font(.caption).foregroundStyle(.secondary)
            if let d = model.lookAtDistanceM { Text(String(format: "look-at distance %.0f m", d)).font(.footnote) }
            if !model.isLookAtValid {
                Text("Move ≥50 m from the base for a usable heading").font(.footnote).foregroundStyle(.orange)
            }
            Button {
                Task { await confirm { await client.calibrateMapHeading(preview: false,
                                                                        targetLat: model.lookAtLat ?? 0,
                                                                        targetLon: model.lookAtLon ?? 0) } }
            } label: { Text("Set heading from look-at") }
                .disabled(!model.canConfirmHeading || busy)

        case .headingArrow:
            Text("North-up. Rotate to the camera's forward direction.")
                .font(.caption).foregroundStyle(.secondary)
            Slider(value: $model.arrowBearingDeg, in: 0...360, step: 1)
            Text(String(format: "%.0f°", model.arrowBearingDeg)).font(.footnote)
            Button {
                Task { await confirm { await client.calibrateHeadingLockAccept(bearingDeg: model.arrowBearingDeg, distanceM: nil) } }
            } label: { Text("Set heading from arrow") }
                .disabled(busy)
        }
    }

    private func confirm(_ op: @escaping () async -> Result<WCCalibrationSessionState, WaveCamCalibrationError>) async {
        busy = true; message = nil; defer { busy = false }
        switch await op() {
        case .success(let state): onDone(state)
        case .failure(let e): message = "Failed: \(e.localizedDescription)"
        }
    }
}
