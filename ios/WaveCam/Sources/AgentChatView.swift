import SwiftUI

/// Full-screen agent chat. The conversation + in-flight turn live on WaveCamClient
/// (not here), so this view can be dismissed/re-presented without losing state.
/// Fixes the field issues from build 525: keyboard wouldn't dismiss and covered the
/// reply, switching tabs lost the thread, and the input was cramped under other cards.
struct AgentChatView: View {
    @Environment(WaveCamClient.self) private var client
    @Environment(\.dismiss) private var dismiss
    /// Providers the backend advertises (supported.agentProviders); nil → just the default.
    let providers: [String]?
    @FocusState private var inputFocused: Bool
    @State private var draft = ""

    private var killed: Bool { client.killed }
    private var armed: Bool { client.agentArmed && !killed }

    private var providerOptions: [AgentProvider] {
        guard let providers, !providers.isEmpty else { return [.claudeCode] }
        return AgentProvider.allCases.filter { providers.contains($0.rawValue) }
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            messages
            inputBar
        }
        .background(WC.bg.ignoresSafeArea())
    }

    // MARK: header — provider picker + always-visible KILL + done

    private var header: some View {
        VStack(spacing: 8) {
            HStack {
                Button { dismiss() } label: {
                    Image(systemName: "chevron.down").font(.system(size: 15, weight: .bold))
                        .foregroundStyle(WC.muted).frame(width: 40, height: 40)
                }
                Text("ASK CLAUDE").font(.system(size: 14, weight: .black, design: .monospaced))
                    .tracking(1.4).foregroundStyle(WC.txt)
                Spacer()
                Button { Task { await client.kill() } } label: {
                    Text("KILL").font(.system(size: 13, weight: .black))
                        .foregroundStyle(WC.kill)
                        .padding(.horizontal, 14).padding(.vertical, 8)
                        .overlay(RoundedRectangle(cornerRadius: 8).stroke(WC.kill, lineWidth: 1.5))
                }
                .accessibilityLabel("Emergency stop")
            }
            HStack(spacing: 10) {
                if providerOptions.count > 1 {
                    Picker("Provider", selection: Binding(
                        get: { client.agentChatProvider },
                        set: { client.agentChatProvider = $0 })) {
                        ForEach(providerOptions, id: \.self) { Text($0.label).tag($0) }
                    }
                    .pickerStyle(.menu).tint(WC.accent)
                }
                Spacer()
                Toggle(isOn: Binding(get: { armed }, set: { client.armAgent($0) })) {
                    Text(armed ? "ARMED" : "Can act")
                        .font(.system(size: 11, weight: .black, design: .monospaced)).tracking(1.0)
                }
                .labelsHidden().disabled(killed).tint(WC.accent)
                Text(killed ? "killed" : (armed ? "can act" : "read-only"))
                    .font(.system(size: 11)).foregroundStyle(killed ? WC.warn : WC.muted)
            }
        }
        .padding(.horizontal, 16).padding(.top, 12).padding(.bottom, 8)
        .background(WC.panel)
    }

    // MARK: messages — scrolls, auto-follows, tap to dismiss keyboard

    private var messages: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    if client.agentChatLog.isEmpty {
                        Text("Ask about status, calibration, or setup. Arm to let Claude act on the rig.")
                            .font(.system(size: 13)).foregroundStyle(WC.muted)
                            .frame(maxWidth: .infinity, alignment: .leading).padding(.top, 24)
                    }
                    ForEach(client.agentChatLog) { line in
                        AgentChatBubble(line: line).id(line.id)
                    }
                    if client.agentChatSending {
                        HStack(spacing: 6) {
                            ProgressView().tint(WC.muted)
                            Text("Claude is thinking…").font(.system(size: 12)).foregroundStyle(WC.muted)
                        }.id("sending")
                    }
                }
                .padding(16)
            }
            .scrollDismissesKeyboard(.interactively)
            .contentShape(Rectangle())
            .onTapGesture { inputFocused = false }
            .onChange(of: client.agentChatLog.count) {
                if let last = client.agentChatLog.last {
                    withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                }
            }
        }
        .frame(maxHeight: .infinity)
    }

    // MARK: input bar — pinned above the keyboard

    private var inputBar: some View {
        HStack(spacing: 8) {
            TextField("Message Claude…", text: $draft, axis: .vertical)
                .textFieldStyle(.plain).lineLimit(1...5).font(.system(size: 15))
                .foregroundStyle(WC.txt).focused($inputFocused)
                .padding(.horizontal, 12).padding(.vertical, 10)
                .background(WC.ink, in: .rect(cornerRadius: 12))
                .submitLabel(.send)
                .onSubmit(send)
            Button(action: send) {
                Image(systemName: client.agentChatSending ? "hourglass" : "paperplane.fill")
                    .font(.system(size: 17, weight: .bold)).foregroundStyle(WC.accent)
                    .frame(width: 46, height: 46)
                    .background(WC.accent.opacity(0.12), in: .rect(cornerRadius: 12))
            }
            .buttonStyle(.plain)
            .disabled(client.agentChatSending || draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            .accessibilityLabel("Send")
        }
        .padding(.horizontal, 12).padding(.vertical, 10)
        .background(WC.panel)
    }

    private func send() {
        let text = draft
        draft = ""
        client.sendAgentChatTurn(text)
    }
}

private struct AgentChatBubble: View {
    let line: WCAgentChatLine

    var body: some View {
        HStack(spacing: 0) {
            if line.role == .you { Spacer(minLength: 40) }
            Text(line.text)
                .font(.system(size: 14)).textSelection(.enabled)
                .foregroundStyle(line.role == .you ? WC.accent : WC.txt)
                .padding(.horizontal, 13).padding(.vertical, 9)
                .background(
                    line.role == .you ? WC.accent.opacity(0.12) : WC.panel2,
                    in: .rect(cornerRadius: 13))
                .frame(maxWidth: .infinity, alignment: line.role == .you ? .trailing : .leading)
            if line.role == .claude { Spacer(minLength: 40) }
        }
    }
}
