import SwiftUI

struct WatchStatusView: View {
    @Environment(WatchClient.self) private var client

    /// Long-press hold state for resume gesture (~1.2 s)
    @State private var holdProgress: Double = 0
    @State private var holdTimer: Timer?
    private let holdDuration: Double = 1.2

    private var snap: WatchSnapshot { client.snapshot }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            if !client.online {
                VStack(spacing: 8) {
                    if client.stopNotConfirmed {
                        stopNotConfirmedBanner
                    }
                    offlineView
                }
                .padding(.horizontal, 4)
                .padding(.vertical, 6)
            } else {
                VStack(spacing: 6) {
                    stateRow
                    Divider().overlay(Color.gray.opacity(0.4))
                    recRow
                    if snap.targetAgeSec != nil {
                        gpsRow
                    }
                    Spacer(minLength: 2)
                    if client.stopNotConfirmed {
                        stopNotConfirmedBanner
                    }
                    if snap.killed {
                        resumeButton
                    } else {
                        stopButton
                    }
                }
                .padding(.horizontal, 4)
                .padding(.vertical, 6)
            }
        }
        .onAppear { client.startPolling() }
        .onDisappear { client.stopPolling() }
    }

    // MARK: - Subviews

    private var offlineView: some View {
        VStack(spacing: 8) {
            Image(systemName: "wifi.slash")
                .font(.system(size: 28))
                .foregroundStyle(.gray)
            Text("OFFLINE")
                .font(.system(size: 15, weight: .bold, design: .monospaced))
                .foregroundStyle(.gray)
            Text("out of range?")
                .font(.system(size: 11))
                .foregroundStyle(.gray.opacity(0.7))
        }
    }

    private var stateRow: some View {
        HStack(spacing: 6) {
            Text(snap.killed ? "KILLED" : snap.sessionState)
                .font(.system(size: 14, weight: .bold, design: .monospaced))
                .foregroundStyle(snap.killed ? .red : stateColor)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            Spacer()
            if snap.locked && !snap.killed {
                Label("LOCKED", systemImage: "lock.fill")
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundStyle(.green)
                    .labelStyle(.iconOnly)
            }
        }
    }

    private var recRow: some View {
        HStack(spacing: 6) {
            // REC indicator
            HStack(spacing: 3) {
                Circle()
                    .fill(snap.recording ? Color.red : Color.gray.opacity(0.4))
                    .frame(width: 7, height: 7)
                Text(snap.recording ? "REC" : "STOP")
                    .font(.system(size: 10, weight: .semibold, design: .monospaced))
                    .foregroundStyle(snap.recording ? .red : .gray)
            }
            Spacer()
            // Record toggle button
            Button {
                Task { await client.toggleRecording() }
            } label: {
                Text(snap.recording ? "Stop" : "Record")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(snap.recording ? .orange : .white)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(
                        RoundedRectangle(cornerRadius: 6)
                            .fill(snap.recording ? Color.orange.opacity(0.2) : Color.white.opacity(0.12))
                    )
            }
            .buttonStyle(.plain)
        }
    }

    private var gpsRow: some View {
        HStack(spacing: 4) {
            Image(systemName: "location.fill")
                .font(.system(size: 9))
                .foregroundStyle(gpsColor)
            if let age = snap.targetAgeSec {
                Text(String(format: "%.0fs", age))
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(gpsColor)
            }
            if snap.gpsStale == true {
                Text("STALE")
                    .font(.system(size: 9, weight: .bold, design: .monospaced))
                    .foregroundStyle(.orange)
            }
            Spacer()
            // Base fix indicator
            if let alive = snap.readerAlive {
                Text(alive ? "BASE OK" : "BASE–")
                    .font(.system(size: 9, weight: .semibold, design: .monospaced))
                    .foregroundStyle(alive ? .green : .orange)
            }
        }
    }

    /// H13: shown when the last STOP could not be confirmed by the rig — the camera
    /// may still be moving. Cleared when a poll confirms killed or a resume succeeds.
    private var stopNotConfirmedBanner: some View {
        Text("STOP NOT CONFIRMED")
            .font(.system(size: 10, weight: .black, design: .monospaced))
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 4)
            .background(Color.red.opacity(0.85))
            .clipShape(RoundedRectangle(cornerRadius: 5))
            .lineLimit(1)
            .minimumScaleFactor(0.7)
    }

    /// Large red Emergency Stop button
    private var stopButton: some View {
        Button {
            Task { await client.kill() }
        } label: {
            Text("STOP")
                .font(.system(size: 16, weight: .black, design: .monospaced))
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 10)
                .background(Color.red)
                .clipShape(RoundedRectangle(cornerRadius: 10))
        }
        .buttonStyle(.plain)
    }

    /// Hold-to-resume button shown only when killed
    private var resumeButton: some View {
        ZStack(alignment: .leading) {
            RoundedRectangle(cornerRadius: 10)
                .fill(Color.gray.opacity(0.25))
            GeometryReader { geo in
                RoundedRectangle(cornerRadius: 10)
                    .fill(Color.green.opacity(0.6))
                    .frame(width: max(0, min(1, holdProgress)) * geo.size.width)
                    .animation(.linear(duration: 0.05), value: holdProgress)
            }
            Text(holdProgress > 0 ? "HOLD..." : "Hold to Resume")
                .font(.system(size: 13, weight: .bold, design: .monospaced))
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 10)
        }
        .frame(height: 42)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .gesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in startHold() }
                .onEnded   { _ in cancelHold() }
        )
    }

    // MARK: - Hold gesture helpers

    private func startHold() {
        guard holdTimer == nil else { return }
        let step = 0.05
        holdTimer = Timer.scheduledTimer(withTimeInterval: step, repeats: true) { _ in
            Task { @MainActor in
                holdProgress += step / holdDuration
                if holdProgress >= 1.0 {
                    cancelHold()
                    Task { await client.resume() }
                }
            }
        }
    }

    private func cancelHold() {
        holdTimer?.invalidate()
        holdTimer = nil
        holdProgress = 0
    }

    // MARK: - Derived colors

    private var stateColor: Color {
        switch snap.sessionState {
        case "TRACKING": return .green
        case "SEARCHING": return .yellow
        case "KILLED":   return .red
        default:         return .white
        }
    }

    private var gpsColor: Color {
        guard let age = snap.targetAgeSec else { return .gray }
        if snap.gpsStale == true { return .orange }
        if age < 5 { return .green }
        if age < 15 { return .yellow }
        return .orange
    }
}
