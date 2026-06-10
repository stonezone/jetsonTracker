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
        case icon
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
            .frame(maxWidth: .infinity, minHeight: 44)
            .padding(.vertical, compact ? 13 : 16)
            .background(WC.kill, in: .rect(cornerRadius: 16))
            .shadow(color: WC.kill.opacity(0.25), radius: 18, y: 8)
        case .chip:
            // Solid red + white text like every other variant — the faint red-on-red
            // version washed out in sunlight, and this chip is the always-visible KILL.
            HStack(spacing: 6) {
                Image(systemName: "stop.fill").font(.system(size: 11, weight: .black))
                Text("KILL").font(.system(size: 12, weight: .black)).tracking(1)
            }
            .foregroundStyle(.white)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(WC.kill, in: RoundedRectangle(cornerRadius: 10))
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        case .icon:
            // Icon-only — the Live dock. Solid red = the one safety control, distinct from
            // teal interactive buttons. No truncating label.
            Image(systemName: "stop.fill")
                .font(.system(size: 18, weight: .black))
                .foregroundStyle(.white)
                .frame(width: 44, height: 44)
                .background(WC.kill, in: .rect(cornerRadius: WCRadius.xs))
                .overlay(RoundedRectangle(cornerRadius: WCRadius.xs).stroke(Color.white.opacity(0.25)))
                .shadow(color: WC.kill.opacity(0.4), radius: 8, y: 2)
        }
    }
}
