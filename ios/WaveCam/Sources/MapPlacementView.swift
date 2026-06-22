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
            // KILL stays reachable while this sheet covers the root chip (audit KILL-1).
            EmergencyStopButton(style: .compact)
            if purpose != .base {
                manualHeadingSection
                Text("…or set heading on the map:").font(.caption2).foregroundStyle(.secondary)
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
        .environment(client)   // EmergencyStopButton resolves the client from the environment
        .onAppear {
            model.mode = purpose
            // Capture the initial center immediately so confirm isn't gated on a first
            // pan (review F-002). Base mode overwrites this from regionDidChange as the
            // operator pans; heading mode keeps it as the locked base.
            model.baseLat = initialLat
            model.baseLon = initialLon
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

    /// Manual heading entry — the primary path (operator reads a bearing off a phone
    /// compass / nav device); the map look-at/arrow below are alternatives.
    @ViewBuilder private var manualHeadingSection: some View {
        HStack {
            Text("Heading °").font(.footnote)
            TextField("deg", value: $model.manualHeadingDeg, format: .number)
                .textFieldStyle(.roundedBorder).frame(width: 90).keyboardType(.numbersAndPunctuation)
            Button("Set") {
                if let h = model.manualHeadingDeg {
                    Task { await confirm { await client.calibrateHeadingLockAccept(bearingDeg: h, distanceM: nil) } }
                }
            }.disabled(model.manualHeadingDeg == nil || busy)
        }
        Text("Type the bearing from your compass / nav device.")
            .font(.caption2).foregroundStyle(.secondary)
    }

    @ViewBuilder private var baseHeightSection: some View {
        HStack {
            TextField("Lat", text: $model.manualLatText).keyboardType(.numbersAndPunctuation)
            TextField("Lon", text: $model.manualLonText).keyboardType(.numbersAndPunctuation)
        }.textFieldStyle(.roundedBorder).font(.footnote)
        HStack {
            Text("Cam ht above water/ground (m)").font(.footnote)
            TextField("m", value: $model.baseHeightM, format: .number)
                .textFieldStyle(.roundedBorder).frame(width: 60).keyboardType(.numbersAndPunctuation)
            Text(String(format: "≈%.0f° down @100 m", -model.predictedDepressionDeg(atMeters: 100)))
                .font(.caption2).foregroundStyle(.secondary)
        }
        Text("How high the camera is above the surface the subject sits on (the water for surf; the ground for a tracker test) — NOT your altitude above sea level.")
            .font(.caption2).foregroundStyle(.secondary)
    }

    @ViewBuilder private var controls: some View {
        switch model.mode {
        case .base:
            Text("Center the crosshair on the real tripod spot, or type coordinates.")
                .font(.caption).foregroundStyle(.secondary)
            baseHeightSection
            Button {
                let coord = model.parsedManualCoord
                Task { await confirm { await client.calibrateLocationManual(
                    lat: coord?.lat ?? model.baseLat ?? initialLat,
                    lon: coord?.lon ?? model.baseLon ?? initialLon,
                    errorRadiusM: model.lastErrorRadiusM, altM: model.baseHeightM) } }
            } label: { Text("Use this location (±\(Int(model.lastErrorRadiusM)) m)") }
                .disabled(!model.canConfirmLocation || busy)

        case .headingLookAt:
            Text("Aim the camera at a distant landmark in Live, hold it still, center the crosshair on that same landmark.")
                .font(.caption).foregroundStyle(.secondary)
            if let d = model.lookAtDistanceM, let b = model.lookAtBearingDeg {
                Text(String(format: "look-at %.0f m @ %.0f° (heading this sets)", d, b)).font(.footnote)
            }
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
