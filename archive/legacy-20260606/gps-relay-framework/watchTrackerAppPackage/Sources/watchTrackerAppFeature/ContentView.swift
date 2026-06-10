import SwiftUI
import LocationCore
import WatchLocationProvider

public struct ContentView: View {
    @StateObject private var viewModel = WatchLocationViewModel()

    public var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    // MARK: - Workout Control Section
                    workoutControlSection

                    // MARK: - Relay Path Section
                    relayPathSection

                    // MARK: - Connectivity Section
                    if let metrics = viewModel.transportMetrics {
                        connectivitySection(metrics: metrics)
                    }

                    // MARK: - Status Section
                    statusSection

                    // MARK: - Current GPS Fix Section
                    if let fix = viewModel.currentFix {
                        currentFixSection(fix: fix)
                    }

                    Spacer()

                    // MARK: - Version Footer
                    Text("v\(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0.4")")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                        .padding(.bottom, 4)
                }
                .padding()
            }
            .navigationTitle("GPS Tracker")
        }
    }

    private var relayPathSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: "iphone.radiowaves.left.and.right")
                    .foregroundColor(.blue)
                Text("Relay Path")
                    .font(.headline)
            }

            Text("Watch -> iPhone -> Orin")
                .font(.subheadline)
                .fontWeight(.semibold)

            Text("Keep the iPhone relay app open and streaming.")
                .font(.caption2)
                .foregroundColor(.secondary)
                .lineLimit(2)
        }
        .padding(12)
        .background(Color.gray.opacity(0.2))
        .cornerRadius(8)
    }

    // MARK: - VERSION UPDATE NOTE
    // When making changes to the app, update the version number above:
    // - Patch (x.x.X): Bug fixes, minor tweaks
    // - Minor (x.X.x): New features, UI changes
    // - Major (X.x.x): Breaking changes, major refactors

    // MARK: - Workout Control Section
    private var workoutControlSection: some View {
        VStack(spacing: 12) {
            Button(action: {
                if viewModel.isTracking {
                    viewModel.stopTracking()
                } else {
                    viewModel.startTracking()
                }
            }) {
                HStack {
                    Image(systemName: viewModel.isTracking ? "stop.circle.fill" : "play.circle.fill")
                        .font(.title3)
                    Text(viewModel.isTracking ? "Stop" : "Start")
                        .font(.headline)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 12)
                .background(viewModel.isTracking ? Color.red : Color.green)
                .foregroundColor(.white)
                .cornerRadius(8)
            }
            .buttonStyle(PlainButtonStyle())

            Text(viewModel.statusMessage)
                .font(.caption)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
        }
    }

    // MARK: - Connectivity Section
    private func connectivitySection(metrics: WatchDirectTransport.Metrics) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: "antenna.radiowaves.left.and.right")
                    .foregroundColor(connectionColor(metrics.connectionState))
                Text("Direct Link")
                    .font(.headline)
                Spacer()
                Text(metrics.connectionState.rawValue.capitalized)
                    .font(.caption)
                    .foregroundColor(connectionColor(metrics.connectionState))
            }

            HStack(spacing: 12) {
                metricColumn(label: "RTT", value: String(format: "%.0fms", metrics.rttMs))

                let ackAge = metrics.lastAckAt.map { Date().timeIntervalSince($0) }
                metricColumn(label: "Ack", value: ackAge.map { String(format: "%.1fs", $0) } ?? "—")

                metricColumn(label: "Queue", value: "\(metrics.queueDepth)")
            }

            HStack(spacing: 12) {
                metricColumn(label: "Rate", value: String(format: "%.1fHz", metrics.sendRateHz))
                metricColumn(label: "Drops", value: "\(metrics.totalDropped)")
            }

            HStack(spacing: 12) {
                metricColumn(label: "Try", value: "\(metrics.connectAttemptCount)")
                metricColumn(label: "Open", value: "\(metrics.didOpenCount)")

                let stateAge = metrics.lastStateChangedAt.map { Date().timeIntervalSince($0) }
                metricColumn(label: "State", value: stateAge.map { String(format: "%.0fs", $0) } ?? "—")
            }

            Text(
                "Path \(metrics.networkPathStatus) " +
                "wifi:\(metrics.networkUsesWiFi ? "yes" : "no") " +
                "cell:\(metrics.networkUsesCellular ? "yes" : "no") " +
                "exp:\(metrics.networkIsExpensive ? "yes" : "no") " +
                "low:\(metrics.networkIsConstrained ? "yes" : "no")"
            )
            .font(.caption2)
            .foregroundColor(metrics.networkPathStatus == "satisfied" ? .secondary : .red)
            .lineLimit(2)

            if let closeCode = metrics.lastCloseCode {
                Text("Close \(closeCode): \(metrics.lastCloseReason ?? "no reason")")
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .lineLimit(2)
            }

            if metrics.lastHTTPProbeAt != nil || metrics.lastHTTPProbeStatus != nil || metrics.lastHTTPProbeError != nil {
                let probeAge = metrics.lastHTTPProbeAt.map { Date().timeIntervalSince($0) }
                let status = metrics.lastHTTPProbeStatus.map { "HTTP \($0)" } ?? "HTTP —"
                Text("Probe \(status)\(probeAge.map { String(format: " %.0fs", $0) } ?? "")")
                    .font(.caption2)
                    .foregroundColor(metrics.lastHTTPProbeError == nil ? .secondary : .red)
                    .lineLimit(2)

                if let probeError = metrics.lastHTTPProbeError, !probeError.isEmpty {
                    Text(probeError)
                        .font(.caption2)
                        .foregroundColor(.red)
                        .lineLimit(3)
                }
            }

            if let error = metrics.lastErrorMessage, !error.isEmpty {
                Text(error)
                    .font(.caption2)
                    .foregroundColor(.red)
                    .lineLimit(4)
            }
        }
        .padding(12)
        .background(Color.gray.opacity(0.2))
        .cornerRadius(8)
    }

    private func connectionColor(_ state: WatchDirectTransport.ConnectionState) -> Color {
        switch state {
        case .connected: return .green
        case .connecting, .reconnecting: return .yellow
        case .disconnected, .failed: return .red
        }
    }

    private func metricColumn(label: String, value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.caption2).foregroundColor(.secondary)
            Text(value).font(.caption).fontWeight(.bold)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - Status Section
    private var statusSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: "figure.run")
                    .foregroundColor(.blue)
                Text("Workout Status")
                    .font(.headline)
            }

            HStack {
                Circle()
                    .fill(viewModel.isTracking ? Color.green : Color.gray)
                    .frame(width: 8, height: 8)
                Text(viewModel.workoutState)
                    .font(.subheadline)
                Spacer()
            }

            HStack {
                Text("GPS fixes:")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Spacer()
                Text("\(viewModel.fixCount)")
                    .font(.caption)
                    .fontWeight(.medium)
            }
        }
        .padding(12)
        .background(Color.gray.opacity(0.2))
        .cornerRadius(8)
    }

    // MARK: - Current GPS Fix Section
    private func currentFixSection(fix: LocationFix) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: "location.fill")
                    .foregroundColor(.blue)
                Text("Last Fix")
                    .font(.headline)
            }

            VStack(spacing: 6) {
                fixDetailRow(label: "Lat", value: String(format: "%.4f°", fix.coordinate.latitude))
                fixDetailRow(label: "Lon", value: String(format: "%.4f°", fix.coordinate.longitude))
                fixDetailRow(label: "Acc", value: String(format: "±%.0fm", fix.horizontalAccuracyMeters))
                if let altitude = fix.altitudeMeters {
                    fixDetailRow(label: "Alt", value: String(format: "%.0fm", altitude))
                }
            }

            if let timestamp = viewModel.lastFixTimestamp {
                Text(formatTimestamp(timestamp))
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
        }
        .padding(12)
        .background(Color.gray.opacity(0.2))
        .cornerRadius(8)
    }

    // MARK: - Helper Views
    private func fixDetailRow(label: String, value: String) -> some View {
        HStack {
            Text(label)
                .font(.caption)
                .foregroundColor(.secondary)
            Spacer()
            Text(value)
                .font(.caption)
                .fontWeight(.medium)
        }
    }

    // MARK: - Helper Methods
    private func formatTimestamp(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm:ss"
        return formatter.string(from: date)
    }

    public init() {}
}
