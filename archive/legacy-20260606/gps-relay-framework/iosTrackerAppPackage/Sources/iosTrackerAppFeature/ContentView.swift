import SwiftUI
import LocationCore

public struct ContentView: View {
    @StateObject private var viewModel = LocationRelayViewModel()
    @State private var showDebugFeatures = false

    public init() {}

    public var body: some View {
        NavigationView {
            ScrollView {
                VStack(spacing: 24) {
                    relayControlSection
                    trackingModeSection
                    webSocketConfigSection
                    connectionStatusSection
                    baseStationSection

                    if showDebugFeatures {
                        remoteTrackerSection
                        relayHealthSection
                        watchConnectionSection
                    }

                    debugControlSection

                    Spacer()

                    Text("v\(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0.4")")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                        .padding(.bottom, 8)
                }
                .padding()
            }
            .navigationTitle("iOS Tracker")
        }
        .alert(
            viewModel.authorizationMessage ?? "",
            isPresented: Binding(
                get: { viewModel.authorizationMessage != nil },
                set: { if !$0 { viewModel.dismissAuthorizationMessage() } }
            )
        ) {
            Button("OK", role: .cancel) {
                viewModel.dismissAuthorizationMessage()
            }
        }
        .task {
            viewModel.resumeRelayIfNeeded()
        }
    }

    // MARK: - Relay Control

    private var relayControlSection: some View {
        VStack(spacing: 12) {
            Button {
                viewModel.isRelayActive ? viewModel.stopRelay() : viewModel.startRelay()
            } label: {
                HStack {
                    Image(systemName: viewModel.isRelayActive ? "stop.circle.fill" : "play.circle.fill")
                        .font(.title2)
                    Text(viewModel.isRelayActive ? "Stop Tracking" : "Start Tracking")
                        .font(.headline)
                }
                .frame(maxWidth: .infinity)
                .padding()
                .background(viewModel.isRelayActive ? Color.red : Color.green)
                .foregroundColor(.white)
                .cornerRadius(12)
            }

            Text(viewModel.statusMessage)
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
        }
    }

    // MARK: - Tracking Modes

    private var trackingModeSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Tracking Mode")
                .font(.headline)

            ForEach(TrackingMode.allCases, id: \.self) { mode in
                Button {
                    viewModel.trackingMode = mode
                } label: {
                    HStack {
                        Image(systemName: viewModel.trackingMode == mode ? "checkmark.circle.fill" : "circle")
                            .foregroundColor(viewModel.trackingMode == mode ? .green : .gray)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(modeDisplayName(mode))
                                .font(.subheadline)
                                .fontWeight(.medium)
                            Text(mode.configuration.description)
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                        Spacer()
                        Text(String(format: "~%.0f%%/hr", mode.configuration.estimatedBatteryUsePerHour))
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    .padding(12)
                    .background(viewModel.trackingMode == mode ? Color.green.opacity(0.15) : Color.clear)
                    .cornerRadius(10)
                }
                .buttonStyle(.plain)
                .disabled(viewModel.isRelayActive)
            }

            if viewModel.isRelayActive {
                Text("Stop tracking to change mode.")
                    .font(.caption)
                    .foregroundColor(.orange)
            }
        }
        .padding()
        .background(Color(.systemGray6))
        .cornerRadius(12)
    }

    // MARK: - WebSocket Configuration

    private var webSocketConfigSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("WebSocket")
                .font(.headline)

            Toggle(isOn: $viewModel.webSocketEnabled) {
                Text("Enable WebSocket connection")
                    .font(.subheadline)
            }
            .tint(.blue)
            .disabled(viewModel.isRelayActive)

            if viewModel.webSocketEnabled {
                HStack {
                    Image(systemName: "network")
                        .foregroundColor(.blue)
                    TextField("wss://ws.stonezone.net", text: $viewModel.webSocketURL)
                        .textFieldStyle(.roundedBorder)
                        .autocapitalization(.none)
                        .keyboardType(.URL)
                        .disabled(viewModel.isRelayActive)
                }

                Toggle(isOn: $viewModel.allowInsecureConnections) {
                    Text("Allow insecure ws:// connections")
                        .font(.subheadline)
                }
                .tint(.orange)
                .disabled(viewModel.isRelayActive)

                Text("Use wss:// for production. ws:// is intended for local testing on trusted networks.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            } else {
                Text("WebSocket disabled. Relay will track GPS without sending to external server.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            if viewModel.isRelayActive {
                Text("Stop relay to edit connection settings.")
                    .font(.caption)
                    .foregroundColor(.orange)
            }
        }
        .padding()
        .background(Color(.systemGray6))
        .cornerRadius(12)
    }

    // MARK: - Connection Status

    private var connectionStatusSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Connection Status")
                .font(.headline)
            HStack {
                Image(systemName: connectionIconName)
                    .foregroundColor(connectionStatusColor)
                Text(connectionStatusText)
                    .font(.subheadline)
                Spacer()
            }
        }
        .padding()
        .background(Color(.systemGray6))
        .cornerRadius(12)
    }

    // MARK: - Base Station Fix

    private var baseStationSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Image(systemName: "antenna.radiowaves.left.and.right")
                    .foregroundColor(.orange)
                Text("Base Station (iPhone)")
                    .font(.headline)
                Spacer()
                if let timestamp = viewModel.lastBaseTimestamp {
                    Text(formatTimestamp(timestamp))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }

            if let fix = viewModel.baseFix {
                VStack(spacing: 8) {
                    fixDetailRow(label: "Latitude", value: String(format: "%.6f°", fix.coordinate.latitude))
                    fixDetailRow(label: "Longitude", value: String(format: "%.6f°", fix.coordinate.longitude))
                    fixDetailRow(label: "Heading", value: fix.headingDegrees.map { String(format: "%.0f°", $0) } ?? "—")
                    fixDetailRow(label: "Battery", value: String(format: "%.0f%%", fix.batteryFraction * 100))
                }
            } else {
                Text("No base station fix yet.")
                    .font(.subheadline)
                    .foregroundColor(.secondary)
                    .frame(maxWidth: .infinity, alignment: .center)
                    .padding()
            }
        }
        .padding()
        .background(Color(.systemGray6))
        .cornerRadius(12)
    }

    // MARK: - Remote Tracker Fix

    private var remoteTrackerSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Image(systemName: "location.fill")
                    .foregroundColor(.blue)
                Text("Remote Tracker (Watch)")
                    .font(.headline)
                Spacer()
                if let timestamp = viewModel.lastRemoteTimestamp {
                    Text(formatTimestamp(timestamp))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }

            if let fix = viewModel.remoteFix {
                VStack(spacing: 8) {
                    fixDetailRow(label: "Latitude", value: String(format: "%.6f°", fix.coordinate.latitude))
                    fixDetailRow(label: "Longitude", value: String(format: "%.6f°", fix.coordinate.longitude))
                    fixDetailRow(label: "Accuracy", value: String(format: "±%.1f m", fix.horizontalAccuracyMeters))
                    if let altitude = fix.altitudeMeters {
                        fixDetailRow(label: "Altitude", value: String(format: "%.1f m", altitude))
                    }
                    fixDetailRow(label: "Speed", value: String(format: "%.1f m/s", fix.speedMetersPerSecond))
                    fixDetailRow(label: "Battery", value: String(format: "%.0f%%", fix.batteryFraction * 100))
                }
            } else {
                Text("No remote tracker fix yet.")
                    .font(.subheadline)
                    .foregroundColor(.secondary)
                    .frame(maxWidth: .infinity, alignment: .center)
                    .padding()
            }
        }
        .padding()
        .background(Color(.systemGray6))
        .cornerRadius(12)
    }

    // MARK: - Relay Health

    private var relayHealthSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Watch GPS Stream")
                .font(.headline)
            HStack {
                Circle()
                    .fill(healthStatusColor)
                    .frame(width: 12, height: 12)
                Text(healthStatusText)
                    .font(.subheadline)
                Spacer()
            }
        }
        .padding()
        .background(Color(.systemGray6))
        .cornerRadius(12)
    }

    // MARK: - Watch Connection

    private var watchConnectionSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("watchTracker Connectivity")
                .font(.headline)

            HStack {
                Image(systemName: viewModel.isWatchConnected ? "applewatch" : "applewatch.slash")
                    .foregroundColor(viewModel.isWatchConnected ? .green : .gray)
                Text(viewModel.isWatchConnected ? "Connected" : "Awaiting watch")
                    .font(.subheadline)
                Spacer()
            }
        }
        .padding()
        .background(Color(.systemGray6))
        .cornerRadius(12)
    }

    // MARK: - Debug Controls

    private var debugControlSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Toggle(isOn: $showDebugFeatures) {
                Text("Show Debug / Watch Relay Features")
                    .font(.subheadline)
            }
            .tint(.gray)

            if showDebugFeatures {
                Divider()
                Toggle(isOn: $viewModel.isWatchRelayEnabled) {
                    VStack(alignment: .leading) {
                        Text("Relay Watch via Bluetooth")
                            .font(.subheadline)
                        Text("Enable if Watch Direct LTE is unavailable")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }
                .tint(.blue)
            }
        }
        .padding()
        .background(Color(.systemGray6))
        .cornerRadius(12)
    }

    // MARK: - Helper Views

    private func fixDetailRow(label: String, value: String) -> some View {
        HStack {
            Text(label)
                .font(.subheadline)
                .foregroundColor(.secondary)
            Spacer()
            Text(value)
                .font(.subheadline)
                .fontWeight(.medium)
        }
    }

    private func formatTimestamp(_ date: Date) -> String {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .abbreviated
        return formatter.localizedString(for: date, relativeTo: Date())
    }

    private var healthStatusColor: Color {
        switch viewModel.relayHealth {
        case .idle:
            return .gray
        case .streaming:
            return .green
        case .degraded:
            return .orange
        }
    }

    private var healthStatusText: String {
        switch viewModel.relayHealth {
        case .idle:
            return "Idle"
        case .streaming:
            return "Streaming"
        case .degraded(let reason):
            return "Degraded – \(reason)"
        }
    }

    private var connectionStatusColor: Color {
        switch viewModel.connectionState {
        case .connected:
            return .green
        case .connecting, .reconnecting:
            return .orange
        case .failed:
            return .red
        case .disconnected:
            return .gray
        }
    }

    private var connectionStatusText: String {
        switch viewModel.connectionState {
        case .connected:
            return "Connected"
        case .connecting:
            return "Connecting…"
        case .reconnecting:
            return "Reconnecting…"
        case .failed:
            return "Failed"
        case .disconnected:
            return "Disconnected"
        }
    }

    private var connectionIconName: String {
        switch viewModel.connectionState {
        case .connected:
            return "checkmark.seal.fill"
        case .connecting, .reconnecting:
            return "arrow.triangle.2.circlepath"
        case .failed:
            return "exclamationmark.triangle.fill"
        case .disconnected:
            return "xmark.seal"
        }
    }

    private func modeDisplayName(_ mode: TrackingMode) -> String {
        switch mode {
        case .realtime: return "Real-Time"
        case .balanced: return "Balanced"
        case .powersaver: return "Power Saver"
        case .minimal: return "Minimal"
        }
    }
}
