import SwiftUI

/// Supervisor panel: deterministic health plus on-demand diagnostics. No camera authority.
struct AgentView: View {
    @Environment(WaveCamClient.self) private var client
    @State private var requestState: AgentRequestState = .idle

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                AgentStatusHeader(status: client.status)
                SupervisorHealthCard(services: serviceRows)
                AgentAuthorityCard()
                AgentRequestCard(state: requestState, onSummon: summonDiagnostics)
            }
            .padding(.horizontal, 16)
            .padding(.top, 6)
            .padding(.bottom, 96)
        }
        .background(WC.bg.ignoresSafeArea())
        .scrollIndicators(.hidden)
        .task { await client.refresh() }
    }

    private var serviceRows: [SupervisorService] {
        let services = client.status?.services ?? [:]
        let preferred = ["wavecam", "supervisor", "gps_server", "cloudflared"]
        let known = preferred.map { name in
            SupervisorService(name: name, state: services[name] ?? fallbackState(for: name))
        }
        let extras = services
            .filter { !preferred.contains($0.key) }
            .map { SupervisorService(name: $0.key, state: $0.value) }
            .sorted { $0.name < $1.name }
        return known + extras
    }

    private func fallbackState(for service: String) -> String {
        "unknown"
    }

    private func summonDiagnostics() {
        Task { await summonDiagnosticsRequest() }
    }

    @MainActor
    private func summonDiagnosticsRequest() async {
        guard client.mode == .live else {
            requestState = .requested
            return
        }

        requestState = .requesting
        do {
            var request = URLRequest(url: client.baseURL.appending(path: "agent/summon"))
            request.httpMethod = "POST"
            request.timeoutInterval = 5
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            if let token = client.token, !token.isEmpty {
                request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            }
            request.httpBody = try JSONSerialization.data(withJSONObject: [
                "source": "ios_native",
                "reason": "operator_diagnostics"
            ])

            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                requestState = .failed("No HTTP response from supervisor.")
                return
            }
            guard (200..<300).contains(http.statusCode) else {
                let message = Self.errorMessage(statusCode: http.statusCode, data: data)
                requestState = .failed(message)
                return
            }
            requestState = .requested
        } catch {
            requestState = .failed(error.localizedDescription)
        }
        await client.refresh()
    }

    private static func errorMessage(statusCode: Int, data: Data) -> String {
        guard
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let message = object["message"] as? String
        else {
            return "HTTP \(statusCode)"
        }
        return "HTTP \(statusCode): \(message)"
    }
}

private enum AgentRequestState {
    case idle
    case requesting
    case requested
    case failed(String)

    var title: String {
        switch self {
        case .idle: "STANDBY"
        case .requesting: "REQUESTING"
        case .requested: "REQUESTED"
        case .failed: "FAILED"
        }
    }

    var detail: String {
        switch self {
        case .idle: "Ready for diagnostics, logs, config review, and implementation help."
        case .requesting: "Contacting the supervisor endpoint."
        case .requested: "Diagnostics request accepted by the supervisor lane."
        case .failed(let message): message
        }
    }

    var tint: Color {
        switch self {
        case .idle: WC.ok
        case .requesting: WC.warn
        case .requested: WC.brand
        case .failed: WC.kill
        }
    }

    var buttonTitle: String {
        switch self {
        case .requesting: "Requesting..."
        default: "Summon Codex"
        }
    }

    var buttonIcon: String {
        switch self {
        case .requesting: "hourglass"
        case .failed: "exclamationmark.triangle.fill"
        default: "terminal.fill"
        }
    }

    var isRequesting: Bool {
        if case .requesting = self { return true }
        return false
    }
}

private struct SupervisorService: Identifiable {
    var id: String { name }
    let name: String
    let state: String

    var displayName: String {
        switch name {
        case "wavecam": "wavecam.service"
        case "supervisor": "wavecam-supervisor"
        case "gps_server": "gps-server"
        case "cloudflared": "cloudflared"
        default: name.replacingOccurrences(of: "_", with: "-")
        }
    }

    var meta: String {
        switch normalizedState {
        case "running": "up"
        case "degraded": "degraded"
        case "stopped": "stopped"
        case "unknown": "unknown"
        default: state
        }
    }

    var normalizedState: String {
        state.lowercased()
    }

    var tone: ServiceTone {
        if ["running", "up", "ok", "healthy"].contains(normalizedState) { return .up }
        if ["degraded", "warning", "warn"].contains(normalizedState) { return .warn }
        if ["stopped", "failed", "down", "error"].contains(normalizedState) { return .down }
        return .unknown
    }
}

private enum ServiceTone {
    case up
    case warn
    case down
    case unknown

    var color: Color {
        switch self {
        case .up: WC.ok
        case .warn: WC.warn
        case .down: WC.kill
        case .unknown: WC.faint
        }
    }
}

private struct AgentStatusHeader: View {
    let status: WCStatus?

    var body: some View {
        HStack(spacing: 8) {
            AgentMetric(label: "SESSION", value: status?.session.state ?? "READY", tint: WC.ok)
            AgentMetric(label: "SAFETY", value: status?.safety.killed == true ? "KILLED" : "CLEAR", tint: status?.safety.killed == true ? WC.kill : WC.ok)
            AgentMetric(label: "REV", value: "\(status?.revision ?? 0)", tint: WC.brand)
        }
    }
}

private struct AgentMetric: View {
    let label: String
    let value: String
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(label)
                .font(.system(size: 9, weight: .semibold))
                .tracking(1.3)
                .foregroundStyle(WC.faint)
            Text(value.uppercased())
                .font(.system(size: 13, weight: .semibold, design: .monospaced))
                .foregroundStyle(tint)
                .lineLimit(1)
                .minimumScaleFactor(0.62)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(WC.panel, in: .rect(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(WC.line))
    }
}

private struct SupervisorHealthCard: View {
    let services: [SupervisorService]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            AgentSectionLabel("Supervisor - deterministic health")
            VStack(spacing: 0) {
                ForEach(services) { service in
                    SupervisorServiceRow(service: service)
                    if service.id != services.last?.id {
                        Divider().overlay(WC.line)
                    }
                }
            }
            .background(WC.ink, in: .rect(cornerRadius: 16))
            .overlay(RoundedRectangle(cornerRadius: 16).stroke(WC.line))
        }
    }
}

private struct SupervisorServiceRow: View {
    let service: SupervisorService

    var body: some View {
        HStack(spacing: 10) {
            Circle()
                .fill(service.tone.color)
                .frame(width: 9, height: 9)
                .shadow(color: service.tone.color.opacity(service.tone == .up ? 0.7 : 0), radius: 7)
            Text(service.displayName)
                .font(.system(size: 13, weight: .medium, design: .monospaced))
                .foregroundStyle(WC.txt)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            Spacer(minLength: 8)
            Text(service.meta.uppercased())
                .font(.system(size: 10, weight: .semibold, design: .monospaced))
                .foregroundStyle(service.tone.color)
                .lineLimit(1)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 12)
    }
}

private struct AgentAuthorityCard: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            AgentSectionLabel("Codex - on-demand assistant")
            HStack(alignment: .top, spacing: 12) {
                ZStack {
                    RoundedRectangle(cornerRadius: 14)
                        .fill(WC.ok.opacity(0.12))
                    Image(systemName: "eye.fill")
                        .font(.system(size: 18, weight: .bold))
                        .foregroundStyle(WC.ok)
                }
                .frame(width: 44, height: 44)

                VStack(alignment: .leading, spacing: 5) {
                    Text("SUPERVISE ONLY")
                        .font(.system(size: 12, weight: .black))
                        .tracking(1.2)
                        .foregroundStyle(WC.ok)
                    Text("The agent can inspect status, review logs, and propose fixes. Camera motion remains under deterministic services and operator controls.")
                        .font(.system(size: 12))
                        .foregroundStyle(WC.muted)
                        .lineSpacing(3)
                }
            }
        }
        .padding(14)
        .background(WC.panel, in: .rect(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).stroke(WC.line))
    }
}

private struct AgentRequestCard: View {
    let state: AgentRequestState
    let onSummon: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("DIAGNOSTIC REQUEST")
                        .font(.system(size: 10, weight: .semibold))
                        .tracking(1.4)
                        .foregroundStyle(WC.faint)
                    Text(state.title)
                        .font(.system(size: 20, weight: .bold, design: .monospaced))
                        .foregroundStyle(state.tint)
                }
                Spacer()
                Circle()
                    .fill(state.tint)
                    .frame(width: 10, height: 10)
                    .shadow(color: state.tint.opacity(0.7), radius: 8)
            }

            Text(state.detail)
                .font(.system(size: 12))
                .foregroundStyle(WC.muted)
                .lineSpacing(3)

            Button {
                onSummon()
            } label: {
                Label(state.buttonTitle, systemImage: state.buttonIcon)
                    .font(.system(size: 13, weight: .black))
                    .tracking(1.2)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
            }
            .buttonStyle(.plain)
            .disabled(state.isRequesting)
            .opacity(state.isRequesting ? 0.72 : 1)
            .foregroundStyle(WC.brand)
            .background(WC.brand.opacity(0.1), in: .rect(cornerRadius: 14))
            .overlay(
                RoundedRectangle(cornerRadius: 14)
                    .stroke(WC.brand.opacity(0.55), style: StrokeStyle(lineWidth: 1, dash: [5, 4]))
            )
            .accessibilityLabel("Summon Codex diagnostics")
        }
        .padding(14)
        .background(WC.panel, in: .rect(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).stroke(WC.line))
    }
}

private struct AgentSectionLabel: View {
    let text: String

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        Text(text.uppercased())
            .font(.system(size: 10, weight: .semibold))
            .tracking(1.5)
            .foregroundStyle(WC.muted)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}
