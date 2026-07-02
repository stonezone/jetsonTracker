import SwiftUI

/// Live event ring viewer: pipeline state transitions (lock, owner, gps, kill) in
/// reverse-chronological order. 5s polling with a `since` cursor so only new events
/// are fetched. Matches AgentView's log list styling.
struct SessionLogView: View {
    @Environment(WaveCamClient.self) private var client

    @State private var events: [WCEvent] = []
    @State private var sinceCursor: Double = 0
    @State private var isLoading = false
    @State private var pollTimer: Timer? = nil

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                EventListCard(events: events, isLoading: isLoading) {
                    Task { await fullRefresh() }
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 6)
            .padding(.bottom, 96)
        }
        .background(WC.bg.ignoresSafeArea())
        .scrollIndicators(.hidden)
        .task {
            await fullRefresh()
            pollTimer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { _ in
                Task { await pollNew() }
            }
        }
        .onDisappear {
            pollTimer?.invalidate()
            pollTimer = nil
        }
    }

    // MARK: - Data

    /// L15: hard cap on the local ring — pollNew appends forever at 5 s cadence.
    private static let maxEvents = 500

    @MainActor
    private func fullRefresh() async {
        isLoading = true
        if let fetched = await client.events(since: 0) {
            events = Array(fetched.suffix(Self.maxEvents))
            sinceCursor = fetched.last?.t ?? 0
        }
        isLoading = false
    }

    @MainActor
    private func pollNew() async {
        guard let fetched = await client.events(since: sinceCursor), !fetched.isEmpty else { return }
        // R21: the backend `since` filter is EXCLUSIVE (events.py: `e["t"] > ts`), not
        // inclusive as an earlier comment here claimed — the cursor event should not repeat
        // server-side. This client-side filter is kept as a defensive guard against
        // duplicate/equal timestamps re-appearing and duplicating WCEvent.id ("t-kind")
        // entries in the ForEach.
        let fresh = fetched.filter { ($0.t ?? 0) > sinceCursor }
        guard !fresh.isEmpty else { return }
        events.append(contentsOf: fresh)
        if events.count > Self.maxEvents {
            events.removeFirst(events.count - Self.maxEvents)   // drop oldest
        }
        sinceCursor = fresh.last?.t ?? sinceCursor
    }
}

// MARK: - Event list card

private struct EventListCard: View {
    let events: [WCEvent]
    let isLoading: Bool
    let onRefresh: () -> Void

    var body: some View {
        OperatorCard {
            VStack(alignment: .leading, spacing: WCSpace.sm) {
                HStack {
                    OperatorSectionLabel("Session events")
                    Spacer()
                    Button(action: onRefresh) {
                        Image(systemName: "arrow.clockwise")
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(WC.accent)
                    }
                    .accessibilityLabel("Refresh events")
                }

                if events.isEmpty && !isLoading {
                    Text("No events yet.")
                        .font(.system(size: 12, design: .monospaced))
                        .foregroundStyle(WC.muted)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, WCSpace.sm)
                } else {
                    VStack(spacing: 0) {
                        ForEach(events.reversed()) { event in
                            EventRow(event: event)
                            if event.id != events.first?.id {
                                Divider().background(WC.line)
                            }
                        }
                    }
                    .background(WC.ink, in: .rect(cornerRadius: 12))
                    .overlay(RoundedRectangle(cornerRadius: 12).stroke(WC.line))
                }
            }
        }
    }
}

// MARK: - Event row

private struct EventRow: View {
    let event: WCEvent

    private static let timeFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return f
    }()

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Text(Self.timeFormatter.string(from: event.timestamp))
                .font(.system(size: 10, weight: .regular, design: .monospaced))
                .foregroundStyle(WC.muted)
                .lineLimit(1)
                .fixedSize()

            kindChip

            Text(detailText)
                .font(.system(size: 11, weight: .regular, design: .monospaced))
                .foregroundStyle(WC.txt)
                .lineLimit(3)
                .multilineTextAlignment(.leading)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
    }

    /// Human-readable detail string for the row.
    /// Shadow events format the structured ShadowDetail into a compact field scan.
    private var detailText: String {
        if event.kind?.lowercased() == "shadow", let sd = event.shadowDetail {
            var parts: [String] = []
            if let b = sd.bearingDeg  { parts.append(String(format: "b=%.1f°", b)) }
            if let d = sd.distM       { parts.append(String(format: "d=%.0fm", d)) }
            if let s = sd.bearingStdDeg { parts.append(String(format: "std=%.1f°", s)) }
            if let g = sd.gpsUpdated, g { parts.append("gps✓") }
            if let v = sd.visionUpdated, v { parts.append("vis✓") }
            return parts.isEmpty ? "SHADOW" : parts.joined(separator: "  ")
        }
        return event.detail ?? ""
    }

    private var kindChip: some View {
        let label = (event.kind ?? "").uppercased()
        return Text(label)
            .font(.system(size: 9, weight: .black))
            .tracking(0.5)
            .foregroundStyle(kindFg)
            .padding(.horizontal, 5)
            .padding(.vertical, 2)
            .background(kindFg.opacity(0.18), in: .rect(cornerRadius: 4))
            .fixedSize()
    }

    private var kindFg: Color {
        switch event.kind?.lowercased() ?? "" {
        case "lock":              return WC.ok
        case "kill", "killed":    return WC.kill
        case "owner":             return WC.accent
        case "gps":               return WC.warn
        case "shadow":            return Color.purple.opacity(0.8)
        default:                  return WC.muted
        }
    }
}

#Preview {
    ToolsView()
        .environment(WaveCamClient(mode: .mock))
        .preferredColorScheme(.dark)
}
