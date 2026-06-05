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
        OperatorCard {
            VStack(alignment: .leading, spacing: WCSpace.md) {
                HStack {
                    VStack(alignment: .leading, spacing: WCSpace.xs + 1) {
                        OperatorSectionLabel("Orin control")
                        Text(stateText)
                            .font(.system(size: 24, weight: .black, design: .monospaced))
                            .foregroundStyle(stateColor)
                    }
                    Spacer()
                    VStack(alignment: .trailing, spacing: WCSpace.sm - 2) {
                        Image(systemName: connected ? "network" : "network.slash")
                            .font(.system(size: 24, weight: .bold))
                            .foregroundStyle(stateColor)
                        Text(activeRoute.label)
                            .font(WCFont.label)
                            .foregroundStyle(stateColor)
                    }
                }

                VStack(alignment: .leading, spacing: WCSpace.sm - 2) {
                    Text(baseURL.absoluteString)
                        .font(WCFont.captionMono)
                        .foregroundStyle(WC.txt)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    if let lastError {
                        Text(lastError)
                            .font(WCFont.caption)
                            .foregroundStyle(WC.warn)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
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
        OperatorCard {
            VStack(alignment: .leading, spacing: WCSpace.md) {
            Picker("Mode", selection: $selectedMode) {
                Text("Live").tag(WaveCamClient.Mode.live)
                Text("Mock").tag(WaveCamClient.Mode.mock)
            }
            .pickerStyle(.segmented)

            VStack(alignment: .leading, spacing: WCSpace.xs) {
                FieldLabel("USB TETHER API")
                TextField("http://172.20.10.8:8088/api/v1", text: $tetherURLText)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                    .font(WCFont.captionMono)
                    .foregroundStyle(WC.txt)
                    .padding(WCSpace.md)
                    .background(WC.ink, in: .rect(cornerRadius: WCRadius.sm))
                    .overlay(RoundedRectangle(cornerRadius: WCRadius.sm).stroke(WC.line))
            }

            VStack(alignment: .leading, spacing: WCSpace.xs) {
                FieldLabel("WI-FI / HOTSPOT API")
                TextField("http://192.168.1.155:8088/api/v1", text: $wifiURLText)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                    .font(WCFont.captionMono)
                    .foregroundStyle(WC.txt)
                    .padding(WCSpace.md)
                    .background(WC.ink, in: .rect(cornerRadius: WCRadius.sm))
                    .overlay(RoundedRectangle(cornerRadius: WCRadius.sm).stroke(WC.line))
            }

            VStack(alignment: .leading, spacing: WCSpace.xs) {
                FieldLabel("AUTH TOKEN")
                SecureField("optional", text: $tokenText)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .font(WCFont.captionMono)
                    .foregroundStyle(WC.txt)
                    .padding(WCSpace.md)
                    .background(WC.ink, in: .rect(cornerRadius: WCRadius.sm))
                    .overlay(RoundedRectangle(cornerRadius: WCRadius.sm).stroke(WC.line))
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
            .tint(WC.accent)

            if let validationError {
                OperatorNotice(validationError, tint: WC.kill)
            }

            HStack(spacing: WCSpace.sm) {
                GlassButton(
                    label: "Apply",
                    icon: "checkmark.circle.fill",
                    role: .active,
                    action: onApply
                )
                GlassIconButton(
                    systemImage: "arrow.clockwise",
                    state: .normal,
                    action: onRefresh
                )
                .accessibilityLabel("Refresh status")
                GlassIconButton(
                    systemImage: "arrow.uturn.backward",
                    state: .normal,
                    action: onUseDefault
                )
                .accessibilityLabel("Use default connection")
            }
            }
        }
    }
}

private struct FieldLabel: View {
    let text: String

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        Text(text)
            .font(WCFont.label)
            .tracking(1.3)
            .foregroundStyle(WC.muted)
    }
}

#Preview {
    ConnectionView()
        .environment(WaveCamClient(mode: .mock))
        .preferredColorScheme(.dark)
}
