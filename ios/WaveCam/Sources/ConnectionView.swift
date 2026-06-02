import SwiftUI

/// Connection settings for switching between the live Orin API and offline mock
/// data without rebuilding the phone app.
struct ConnectionView: View {
    @Environment(WaveCamClient.self) private var client

    @AppStorage(WaveCamDefaults.modeKey) private var storedMode = WaveCamClient.Mode.live.rawValue
    @AppStorage(WaveCamDefaults.baseURLKey) private var storedLegacyBaseURL = WaveCamDefaults.baseURLString
    @AppStorage(WaveCamDefaults.tetherBaseURLKey) private var storedTetherBaseURL = WaveCamDefaults.tetherBaseURLString
    @AppStorage(WaveCamDefaults.wifiBaseURLKey) private var storedWifiBaseURL = WaveCamDefaults.wifiBaseURLString
    @AppStorage(WaveCamDefaults.mockFallbackKey) private var storedMockFallback = false

    @State private var selectedMode = WaveCamClient.Mode.live
    @State private var tetherURLText = WaveCamDefaults.tetherBaseURLString
    @State private var wifiURLText = WaveCamDefaults.wifiBaseURLString
    @State private var tokenText = ""
    @State private var mockFallbackEnabled = false
    @State private var validationError: String?

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                ConnectionStatusCard(
                    mode: client.mode,
                    connected: client.connected,
                    activeRoute: client.activeRoute,
                    baseURL: client.baseURL,
                    lastError: client.lastError
                )
                ConnectionFormCard(
                    selectedMode: $selectedMode,
                    tetherURLText: $tetherURLText,
                    wifiURLText: $wifiURLText,
                    tokenText: $tokenText,
                    mockFallbackEnabled: $mockFallbackEnabled,
                    validationError: validationError,
                    onApply: applySettings,
                    onUseDefault: useDefault,
                    onRefresh: { Task { await client.refresh() } }
                )
            }
            .padding(.horizontal, 16)
            .padding(.top, 6)
            .padding(.bottom, 22)
        }
        .background(WC.bg.ignoresSafeArea())
        .scrollIndicators(.hidden)
        .task { loadStoredSettings() }
    }

    private func loadStoredSettings() {
        selectedMode = WaveCamClient.Mode(rawValue: storedMode) ?? .live
        let routeTexts = storedRouteTexts()
        tetherURLText = routeTexts.tether
        wifiURLText = routeTexts.wifi
        tokenText = KeychainStore.load(account: KeychainStore.tokenAccount) ?? ""
        mockFallbackEnabled = storedMockFallback
        validationError = nil
    }

    private func applySettings() {
        guard let tetherBaseURL = URL(string: tetherURLText) else {
            validationError = "USB tether API URL is not valid."
            return
        }
        guard let wifiBaseURL = URL(string: wifiURLText) else {
            validationError = "Wi-Fi API URL is not valid."
            return
        }

        validationError = nil
        storedMode = selectedMode.rawValue
        storedLegacyBaseURL = tetherBaseURL.absoluteString
        storedTetherBaseURL = tetherBaseURL.absoluteString
        storedWifiBaseURL = wifiBaseURL.absoluteString
        if tokenText.isEmpty {
            KeychainStore.delete(account: KeychainStore.tokenAccount)
        } else {
            KeychainStore.save(tokenText, account: KeychainStore.tokenAccount)
        }
        storedMockFallback = mockFallbackEnabled
        client.configure(
            mode: selectedMode,
            tetherBaseURL: tetherBaseURL,
            wifiBaseURL: wifiBaseURL,
            token: tokenText,
            mockFallbackEnabled: mockFallbackEnabled
        )
        Task { await client.refresh() }
    }

    private func useDefault() {
        selectedMode = .live
        tetherURLText = WaveCamDefaults.tetherBaseURLString
        wifiURLText = WaveCamDefaults.wifiBaseURLString
        tokenText = ""
        mockFallbackEnabled = false
        applySettings()
    }

    private func storedRouteTexts() -> (tether: String, wifi: String) {
        var tether = storedTetherBaseURL
        var wifi = storedWifiBaseURL

        if storedTetherBaseURL == WaveCamDefaults.tetherBaseURLString,
           storedWifiBaseURL == WaveCamDefaults.wifiBaseURLString,
           storedLegacyBaseURL != WaveCamDefaults.baseURLString {
            if storedLegacyBaseURL == WaveCamDefaults.legacyLANBaseURLString ||
                storedLegacyBaseURL.contains("192.168.") {
                wifi = storedLegacyBaseURL
            } else {
                tether = storedLegacyBaseURL
            }
        }

        return (tether: tether, wifi: wifi)
    }
}

private struct ConnectionStatusCard: View {
    let mode: WaveCamClient.Mode
    let connected: Bool
    let activeRoute: WaveCamClient.ConnectionRoute
    let baseURL: URL
    let lastError: String?

    private var stateText: String {
        if mode == .mock { return "MOCK" }
        return connected ? "CONNECTED" : "OFFLINE"
    }

    private var stateColor: Color {
        if mode == .mock { return WC.warn }
        return connected ? WC.ok : WC.kill
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 5) {
                    Text("ORIN CONTROL")
                        .font(.system(size: 10, weight: .semibold))
                        .tracking(1.5)
                        .foregroundStyle(WC.faint)
                    Text(stateText)
                        .font(.system(size: 24, weight: .black, design: .monospaced))
                        .foregroundStyle(stateColor)
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 6) {
                    Image(systemName: connected ? "network" : "network.slash")
                        .font(.system(size: 24, weight: .bold))
                        .foregroundStyle(stateColor)
                    Text(activeRoute.label)
                        .font(.system(size: 10, weight: .bold, design: .monospaced))
                        .foregroundStyle(stateColor)
                }
            }

            VStack(alignment: .leading, spacing: 6) {
                Text(baseURL.absoluteString)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(WC.txt)
                    .lineLimit(1)
                    .truncationMode(.middle)
                if let lastError {
                    Text(lastError)
                        .font(.system(size: 11))
                        .foregroundStyle(WC.warn)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .padding(14)
        .background(WC.panel, in: .rect(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).stroke(WC.line))
    }
}

private struct ConnectionFormCard: View {
    @Binding var selectedMode: WaveCamClient.Mode
    @Binding var tetherURLText: String
    @Binding var wifiURLText: String
    @Binding var tokenText: String
    @Binding var mockFallbackEnabled: Bool

    let validationError: String?
    let onApply: () -> Void
    let onUseDefault: () -> Void
    let onRefresh: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Picker("Mode", selection: $selectedMode) {
                Text("Live").tag(WaveCamClient.Mode.live)
                Text("Mock").tag(WaveCamClient.Mode.mock)
            }
            .pickerStyle(.segmented)

            VStack(alignment: .leading, spacing: 6) {
                FieldLabel("USB TETHER API")
                TextField("http://172.20.10.8:8088/api/v1", text: $tetherURLText)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                    .font(.system(size: 13, design: .monospaced))
                    .foregroundStyle(WC.txt)
                    .padding(12)
                    .background(WC.bg, in: .rect(cornerRadius: 12))
                    .overlay(RoundedRectangle(cornerRadius: 12).stroke(WC.line))
            }

            VStack(alignment: .leading, spacing: 6) {
                FieldLabel("WI-FI / HOTSPOT API")
                TextField("http://192.168.1.155:8088/api/v1", text: $wifiURLText)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                    .font(.system(size: 13, design: .monospaced))
                    .foregroundStyle(WC.txt)
                    .padding(12)
                    .background(WC.bg, in: .rect(cornerRadius: 12))
                    .overlay(RoundedRectangle(cornerRadius: 12).stroke(WC.line))
            }

            VStack(alignment: .leading, spacing: 6) {
                FieldLabel("AUTH TOKEN")
                SecureField("optional", text: $tokenText)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .font(.system(size: 13, design: .monospaced))
                    .foregroundStyle(WC.txt)
                    .padding(12)
                    .background(WC.bg, in: .rect(cornerRadius: 12))
                    .overlay(RoundedRectangle(cornerRadius: 12).stroke(WC.line))
            }

            Toggle(isOn: $mockFallbackEnabled) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Mock fallback")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(WC.txt)
                    Text("Show local mock telemetry when the live API is unreachable.")
                        .font(.system(size: 11))
                        .foregroundStyle(WC.muted)
                }
            }
            .tint(WC.ok)

            if let validationError {
                Text(validationError)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(WC.kill)
            }

            HStack(spacing: 10) {
                Button(action: onApply) {
                    Label("Apply", systemImage: "checkmark.circle.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(ConnectionButtonStyle(tint: WC.ok, filled: true))

                Button(action: onRefresh) {
                    Image(systemName: "arrow.clockwise")
                        .frame(width: 44)
                }
                .buttonStyle(ConnectionButtonStyle(tint: WC.brand, filled: false))
                .accessibilityLabel("Refresh status")

                Button(action: onUseDefault) {
                    Image(systemName: "arrow.uturn.backward")
                        .frame(width: 44)
                }
                .buttonStyle(ConnectionButtonStyle(tint: WC.muted, filled: false))
                .accessibilityLabel("Use default connection")
            }
        }
        .padding(14)
        .background(WC.panel, in: .rect(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).stroke(WC.line))
    }
}

private struct FieldLabel: View {
    let text: String

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        Text(text)
            .font(.system(size: 9, weight: .semibold))
            .tracking(1.3)
            .foregroundStyle(WC.faint)
    }
}

private struct ConnectionButtonStyle: ButtonStyle {
    let tint: Color
    let filled: Bool

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 13, weight: .bold))
            .foregroundStyle(filled ? Color.black : tint)
            .padding(.vertical, 12)
            .background((filled ? tint : WC.panel2).opacity(configuration.isPressed ? 0.72 : 1), in: .rect(cornerRadius: 13))
            .overlay(RoundedRectangle(cornerRadius: 13).stroke(tint.opacity(filled ? 0 : 0.7)))
            .frame(minHeight: 44)
    }
}

#Preview {
    ConnectionView()
        .environment(WaveCamClient(mode: .mock))
        .preferredColorScheme(.dark)
}
