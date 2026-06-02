import SwiftUI

/// The single emergency-stop control. Every variant triggers `client.kill()`;
/// `style` only changes presentation — a prominent action button, its compact
/// form, or the always-visible top-bar chip. Consolidates the former
/// EmergencyStopButton, PTZEmergencyStopButton, and TopBar KILL chip (review #10).
struct EmergencyStopButton: View {
    enum Style {
        case prominent
        case compact
        case chip
    }

    @Environment(WaveCamClient.self) private var client
    var style: Style = .prominent

    var body: some View {
        Button {
            Task { await client.kill() }
        } label: {
            label
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Emergency stop")
    }

    @ViewBuilder
    private var label: some View {
        switch style {
        case .prominent, .compact:
            let compact = style == .compact
            HStack(spacing: 9) {
                Image(systemName: "stop.fill")
                    .font(.system(size: 13, weight: .black))
                Text("Emergency Stop")
                    .font(.system(size: compact ? 13 : 16, weight: .black))
            }
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity)
            .padding(.vertical, compact ? 13 : 16)
            .background(WC.kill, in: .rect(cornerRadius: 16))
            .shadow(color: WC.kill.opacity(0.25), radius: 18, y: 8)
        case .chip:
            HStack(spacing: 6) {
                RoundedRectangle(cornerRadius: 2).fill(WC.kill).frame(width: 9, height: 9)
                Text("KILL").font(.system(size: 12, weight: .bold)).tracking(1)
            }
            .foregroundStyle(WC.kill)
            .padding(.horizontal, 12)
            .padding(.vertical, 7)
            .background(WC.kill.opacity(0.16), in: RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(WC.kill.opacity(0.4)))
        }
    }
}
