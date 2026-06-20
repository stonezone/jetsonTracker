import SwiftUI

/// Supervisor panel: deterministic health plus on-demand diagnostics. No camera authority.
struct AgentView: View {
    @Environment(WaveCamClient.self) private var client
    @State private var requestState: AgentRequestState = .idle
    @State private var provider: AgentProvider = .claudeCode
    @State private var report: WCAgentReport?
    @State private var config: WCConfig?
    @State private var logLines: [WCLogLine] = []
    @State private var logLevel: LogLevelFilter = .all
    @State private var logsLoading = false
    @State private var showChat = false

    private var agentSupported: Bool { config?.supported?.agent == true || client.mode == .mock }

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                AgentStatusHeader(status: client.status)
                SupervisorHealthCard(services: serviceRows)
                AgentAuthorityCard()
                if agentSupported {
                    AskClaudeCard(unread: client.agentChatLog.count,
                                  armed: client.agentArmed && !client.killed,
                                  onOpen: { showChat = true })
                }
                AgentRequestCard(state: requestState, provider: $provider,
                                 onSummon: summonDiagnostics)
                if let r = report {
                    AgentReportCard(report: r)
                }
                if config?.supported?.logs == true || client.mode == .mock {
                    AgentLogsCard(
                        lines: filteredLines,
                        level: $logLevel,
                        isLoading: logsLoading,
                        onRefresh: { Task { await loadLogs() } }
                    )
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 6)
            .padding(.bottom, 96)
        }
        .background(WC.bg.ignoresSafeArea())
        .scrollIndicators(.hidden)
        .task {
            await client.refresh()
            config = await client.config()
            if config?.supported?.logs == true || client.mode == .mock {
                await loadLogs()
            }
        }
        .onChange(of: logLevel) {
            Task { await loadLogs() }
        }
        .fullScreenCover(isPresented: $showChat) {
            AgentChatView(providers: config?.supported?.agentProviders)
                .environment(client)
        }
    }

    private var filteredLines: [WCLogLine] {
        guard logLevel != .all else { return logLines }
        return logLines.filter { $0.level.uppercased() == logLevel.rawValue }
    }

    @MainActor
    private func loadLogs() async {
        logsLoading = true
        let levelParam = logLevel == .all ? nil : logLevel.rawValue
        logLines = await client.logs(level: levelParam, limit: 200) ?? []
        logsLoading = false
    }

    private var serviceRows: [SupervisorService] {
        let services = client.status?.services ?? [:]
        let preferred = ["wavecam", "supervisor"]
        let known = preferred.map { name in
            SupervisorService(name: name, state: services[name] ?? "unknown")
        }
        let extras = services
            .filter { !preferred.contains($0.key) }
            .map { SupervisorService(name: $0.key, state: $0.value) }
            .sorted { $0.name < $1.name }
        return known + extras
    }

    private func summonDiagnostics() {
        Task { await summonDiagnosticsRequest() }
    }

    @MainActor
    private func summonDiagnosticsRequest() async {
        requestState = .requesting
        report = nil
        let ok = await client.summonAgent(provider: provider.rawValue)
        guard ok else {
            requestState = .failed(client.lastCommandError ?? "Summon failed.")
            return
        }
        requestState = .requested
        // Poll the advisor until the consultation lands (LLM round-trips
        // take 5-30s; cap at 90s).
        for _ in 0..<45 {
            try? await Task.sleep(for: .seconds(2))
            guard let r = await client.agentReport() else { continue }
            report = r
            if r.status == "done" || r.status == "error" {
                requestState = r.status == "done" ? .idle
                    : .failed(r.error ?? "Consultation failed.")
                return
            }
        }
        requestState = .failed("Timed out waiting for the consultation.")
    }
}

// MARK: - Ask Claude entry card (chat lives full-screen in AgentChatView)

private struct AskClaudeCard: View {
    let unread: Int
    let armed: Bool
    let onOpen: () -> Void

    var body: some View {
        Button(action: onOpen) {
            OperatorCard {
                HStack {
                    VStack(alignment: .leading, spacing: 4) {
                        OperatorSectionLabel("Ask Claude")
                        Text(armed ? "ARMED — Claude can act on the rig"
                                   : (unread > 0 ? "\(unread) message\(unread == 1 ? "" : "s") in the thread"
                                                 : "Chat about status, calibration, setup"))
                            .font(.system(size: 12))
                            .foregroundStyle(armed ? WC.warn : WC.muted)
                    }
                    Spacer()
                    Image(systemName: "bubble.left.and.bubble.right.fill")
                        .font(.system(size: 18))
                        .foregroundStyle(WC.accent)
                }
            }
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Open the Claude chat")
    }
}

enum AgentProvider: String, CaseIterable {
    case claudeCode = "claude_code"
    case claude
    case codex
    case deepseek

    var label: String {
        switch self {
        case .claudeCode: "Claude"
        case .claude: "Claude (API)"
        case .codex: "Codex"
        case .deepseek: "DeepSeek"
        }
    }
}

private struct AgentReportCard: View {
    let report: WCAgentReport

    var body: some View {
        OperatorCard {
            HStack {
                OperatorSectionLabel("Supervisor report — \(report.provider ?? "?")")
                Spacer()
                if let d = report.durationSec {
                    Text(String(format: "%.0fs", d))
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(WC.muted)
                }
            }
            if report.status == "running" {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("Consulting…").font(.system(size: 13)).foregroundStyle(WC.muted)
                }
            } else if let err = report.error {
                Text(err)
                    .font(.system(size: 13, design: .monospaced))
                    .foregroundStyle(WC.warn)
                    .textSelection(.enabled)
            } else if let text = report.text {
                Text(text)
                    .font(.system(size: 13))
                    .foregroundStyle(WC.txt)
                    .textSelection(.enabled)
            }
        }
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
        case .requested: WC.accent
        case .failed: WC.kill
        }
    }

    func buttonTitle(provider: AgentProvider) -> String {
        switch self {
        case .requesting: "Requesting..."
        default: "Summon \(provider.label)"
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
            OperatorMetric(label: "SESSION", value: status?.session.state ?? "READY", tint: WC.ok)
            OperatorMetric(label: "SAFETY", value: status?.safety.killed == true ? "KILLED" : "CLEAR", tint: status?.safety.killed == true ? WC.kill : WC.ok)
            OperatorMetric(label: "REV", value: "\(status?.revision ?? 0)", tint: WC.accent)
        }
    }
}

private struct SupervisorHealthCard: View {
    let services: [SupervisorService]

    var body: some View {
        OperatorCard(title: "Supervisor - deterministic health") {
            VStack(spacing: 0) {
                ForEach(services) { service in
                    SupervisorServiceRow(service: service)
                    if service.id != services.last?.id {
                        OperatorDivider()
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
        OperatorCard(title: "Codex - on-demand assistant") {
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
    }
}

private struct AgentRequestCard: View {
    let state: AgentRequestState
    @Binding var provider: AgentProvider
    let onSummon: () -> Void

    var body: some View {
        OperatorCard {
            Picker("Provider", selection: $provider) {
                ForEach(AgentProvider.allCases, id: \.self) { p in
                    Text(p.label).tag(p)
                }
            }
            .pickerStyle(.segmented)

            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    OperatorSectionLabel("Diagnostic request")
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
                Label(state.buttonTitle(provider: provider), systemImage: state.buttonIcon)
                    .font(.system(size: 13, weight: .black))
                    .tracking(1.2)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
            }
            .buttonStyle(.plain)
            .disabled(state.isRequesting)
            .opacity(state.isRequesting ? 0.72 : 1)
            .foregroundStyle(WC.accent)
            .background(WC.accent.opacity(0.1), in: .rect(cornerRadius: 14))
            .overlay(
                RoundedRectangle(cornerRadius: 14)
                    .stroke(WC.accent.opacity(0.55), style: StrokeStyle(lineWidth: 1, dash: [5, 4]))
            )
            .accessibilityLabel("Summon \(provider.label) diagnostics")
        }
    }
}

// MARK: - Log viewer

/// Level-filter values for the log viewer segmented picker.
private enum LogLevelFilter: String, CaseIterable, Hashable {
    case all   = "ALL"
    case debug = "DEBUG"
    case info  = "INFO"
    case warn  = "WARN"
    case error = "ERROR"

    var label: String { rawValue }
}

private struct AgentLogsCard: View {
    let lines: [WCLogLine]
    @Binding var level: LogLevelFilter
    let isLoading: Bool
    let onRefresh: () -> Void

    var body: some View {
        OperatorCard {
            HStack {
                OperatorSectionLabel("Logs")
                Spacer()
                if isLoading {
                    ProgressView()
                        .scaleEffect(0.7)
                } else {
                    Button {
                        onRefresh()
                    } label: {
                        Image(systemName: "arrow.clockwise")
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(WC.accent)
                    }
                    .accessibilityLabel("Refresh logs")
                }
            }

            Picker("Level", selection: $level) {
                ForEach(LogLevelFilter.allCases, id: \.self) { filter in
                    Text(filter.label).tag(filter)
                }
            }
            .pickerStyle(.segmented)

            if lines.isEmpty && !isLoading {
                Text("No log lines.")
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(WC.muted)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, 8)
            } else {
                // Solid background with high contrast for sun readability.
                VStack(spacing: 0) {
                    ForEach(lines.reversed()) { line in
                        LogLineRow(line: line)
                        if line.id != lines.first?.id {
                            Divider()
                                .background(WC.line)
                        }
                    }
                }
                .background(WC.ink, in: .rect(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(WC.line))
            }
        }
    }
}

private struct LogLineRow: View {
    let line: WCLogLine

    private static let timeFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return f
    }()

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Text(Self.timeFormatter.string(from: line.timestamp))
                .font(.system(size: 10, weight: .regular, design: .monospaced))
                .foregroundStyle(WC.muted)
                .lineLimit(1)
                .fixedSize()

            Text(line.level)
                .font(.system(size: 9, weight: .black))
                .tracking(0.5)
                .foregroundStyle(levelFg)
                .padding(.horizontal, 5)
                .padding(.vertical, 2)
                .background(levelBg, in: .rect(cornerRadius: 4))
                .fixedSize()

            Text(line.message)
                .font(.system(size: 11, weight: .regular, design: .monospaced))
                .foregroundStyle(WC.txt)
                .lineLimit(3)
                .multilineTextAlignment(.leading)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
    }

    private var levelFg: Color {
        switch line.level.uppercased() {
        case "DEBUG": return WC.faint
        case "INFO":  return WC.txt
        case "WARN":  return WC.warn
        case "ERROR": return WC.kill
        default:      return WC.muted
        }
    }

    // Solid badge background: keeps level readable in bright sunlight.
    private var levelBg: Color {
        switch line.level.uppercased() {
        case "DEBUG": return WC.faint.opacity(0.18)
        case "INFO":  return WC.txt.opacity(0.12)
        case "WARN":  return WC.warn.opacity(0.20)
        case "ERROR": return WC.kill.opacity(0.20)
        default:      return WC.muted.opacity(0.15)
        }
    }
}
