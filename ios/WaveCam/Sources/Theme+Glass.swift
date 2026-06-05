import SwiftUI

// MARK: - Type scale

/// WaveCam canonical font tokens.
///
/// Usage rationale:
/// - `.title` / `.heading` — screen-level labels, section headers
/// - `.body` — primary readable content
/// - `.caption` / `.captionMono` — secondary HUD values
/// - `.mono` — telemetry values, numeric readouts (uses .monospaced design)
/// - `.label` — compact UI labels, tracking numbers, spaced caps
enum WCFont {
    static let title      = Font.system(size: 20, weight: .bold)
    static let heading    = Font.system(size: 15, weight: .semibold)
    static let body       = Font.system(size: 13, weight: .regular)
    static let bodyBold   = Font.system(size: 13, weight: .semibold)
    static let caption    = Font.system(size: 11, weight: .medium)
    static let captionMono = Font.system(size: 11, weight: .medium, design: .monospaced)
    static let mono       = Font.system(size: 13, weight: .semibold, design: .monospaced)
    static let label      = Font.system(size: 10, weight: .semibold)
}

// MARK: - Spacing scale

/// Consistent spatial tokens — use these instead of magic numbers.
enum WCSpace {
    static let xs: CGFloat  = 4
    static let sm: CGFloat  = 8
    static let md: CGFloat  = 12
    static let lg: CGFloat  = 16
    static let xl: CGFloat  = 24
}

// MARK: - Corner radii

enum WCRadius {
    static let xs: CGFloat  = 8
    static let sm: CGFloat  = 12
    static let md: CGFloat  = 16
    static let lg: CGFloat  = 20
    static let pill: CGFloat = 100
}

// MARK: - Interactive accent

extension WC {
    /// Teal interactive accent — used for controls, active states, and selected indicators.
    ///
    /// Rationale: `WC.brand` (orange 0xFF6A1F) is the subject-tracking cue visible in the
    /// live feed. Using orange for UI accents creates a false visual signal — the eye
    /// associates orange with "subject locked". Teal is perceptually distinct and legible
    /// against both dark glass and bright sky backgrounds. Orange is reserved for brand
    /// identity (logo) and subject confidence indicators; red (`WC.kill`) for STOP/KILL only.
    static let accent = Color(hex: 0x36D1C4)   // teal interactive accent
}

// MARK: - GlassSurface

/// Core glass wrapper. On iOS 26+ uses the native `.glassEffect` API (Liquid Glass);
/// on earlier OS falls back to `.ultraThinMaterial` with a hairline white stroke.
///
/// Sun-legibility: the fallback uses an explicit dark overlay so content stays readable
/// against a bright-sky or ocean background. The glass tint on iOS 26 is left at default
/// (system decides rendering against whatever is behind it); callers can pass `tinted: true`
/// to nudge toward a darker treatment.
struct GlassSurface<Content: View>: View {
    var cornerRadius: CGFloat = WCRadius.md
    var tinted: Bool = false
    @ViewBuilder let content: () -> Content

    var body: some View {
        if #available(iOS 26, *) {
            content()
                .glassEffect(
                    tinted
                        ? .regular.tint(Color.black.opacity(0.18))
                        : .regular,
                    in: .rect(cornerRadius: cornerRadius)
                )
        } else {
            content()
                .background {
                    // Dark overlay ensures legibility over bright backgrounds
                    RoundedRectangle(cornerRadius: cornerRadius)
                        .fill(Color.black.opacity(0.52))
                    RoundedRectangle(cornerRadius: cornerRadius)
                        .fill(Color.white.opacity(0.06))
                }
                .overlay(
                    RoundedRectangle(cornerRadius: cornerRadius)
                        .stroke(Color.white.opacity(0.16), lineWidth: 0.5)
                )
        }
    }
}

// MARK: - GlassCard

/// A padded glass surface — the standard card container.
struct GlassCard<Content: View>: View {
    var cornerRadius: CGFloat = WCRadius.md
    var padding: CGFloat = WCSpace.md
    var tinted: Bool = false
    @ViewBuilder let content: () -> Content

    var body: some View {
        GlassSurface(cornerRadius: cornerRadius, tinted: tinted) {
            content()
                .padding(padding)
        }
    }
}

// MARK: - OperatorCard

/// Solid high-contrast panel for dense operator forms and readouts.
///
/// Use this for Tune/Connect/Agent-style surfaces where outdoor legibility matters
/// more than translucency. It shares the type/spacing/radius tokens with glass
/// surfaces without making text-heavy controls transparent.
struct OperatorCard<Content: View>: View {
    var title: String? = nil
    var cornerRadius: CGFloat = WCRadius.md
    var padding: CGFloat = WCSpace.md
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: WCSpace.md) {
            if let title {
                OperatorSectionLabel(title)
            }
            content()
        }
        .padding(padding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(WC.panel, in: .rect(cornerRadius: cornerRadius))
        .overlay(
            RoundedRectangle(cornerRadius: cornerRadius)
                .stroke(WC.line)
        )
    }
}

struct OperatorSectionLabel: View {
    let text: String

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        Text(text.uppercased())
            .font(WCFont.label)
            .tracking(1.4)
            .foregroundStyle(WC.muted)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct OperatorDivider: View {
    var body: some View {
        Divider().overlay(WC.line)
    }
}

struct OperatorNotice: View {
    let text: String
    let tint: Color

    init(_ text: String, tint: Color) {
        self.text = text
        self.tint = tint
    }

    var body: some View {
        Text(text)
            .font(WCFont.caption)
            .foregroundStyle(tint)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(WCSpace.md)
            .background(tint.opacity(0.12), in: .rect(cornerRadius: WCRadius.sm))
            .overlay(
                RoundedRectangle(cornerRadius: WCRadius.sm)
                    .stroke(tint.opacity(0.28))
            )
    }
}

// MARK: - GlassButton

/// A full-width glass button with role-specific styling.
///
/// - `.normal` — teal accent, standard interactive control
/// - `.active` — teal filled, indicates "on" / selected state
/// - `.danger` — red (`WC.kill`), STOP / destructive actions only
struct GlassButton: View {
    enum Role { case normal, active, danger }

    let label: String
    var icon: String? = nil
    var role: Role = .normal
    var disabled: Bool = false
    let action: () -> Void

    private var tint: Color {
        switch role {
        case .normal:  return WC.accent
        case .active:  return WC.accent
        case .danger:  return WC.kill
        }
    }

    private var filled: Bool { role == .active }

    var body: some View {
        Button(action: action) {
            HStack(spacing: WCSpace.xs) {
                if let icon {
                    Image(systemName: icon)
                        .font(.system(size: 13, weight: .semibold))
                }
                Text(label)
                    .font(WCFont.bodyBold)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
            }
            .foregroundStyle(filled ? .black : tint)
            .frame(maxWidth: .infinity, minHeight: 44)
            .padding(.vertical, WCSpace.sm)
            .background(filled ? tint : tint.opacity(0.15), in: .rect(cornerRadius: WCRadius.sm))
            .overlay(
                RoundedRectangle(cornerRadius: WCRadius.sm)
                    .stroke(filled ? tint.opacity(0.7) : tint.opacity(0.35))
            )
        }
        .buttonStyle(.plain)
        .disabled(disabled)
        .opacity(disabled ? 0.45 : 1)
    }
}

// MARK: - GlassIconButton

/// A compact square glass icon button — used in the Live Rail.
///
/// `state` affects rendering:
/// - `.normal` — default, teal icon
/// - `.active` — teal-filled background, indicates "on"
/// - `.danger` — red, STOP / emergency use only
struct GlassIconButton: View {
    enum State { case normal, active, danger }

    let systemImage: String
    var state: State = .normal
    var size: CGFloat = 44
    var disabled: Bool = false
    let action: () -> Void

    private var tint: Color {
        switch state {
        case .normal: return WC.accent
        case .active: return WC.accent
        case .danger: return WC.kill
        }
    }

    var body: some View {
        Button(action: action) {
            Image(systemName: systemImage)
                .font(.system(size: size * 0.36, weight: .semibold))
                .foregroundStyle(state == .active ? .black : tint)
                .frame(width: size, height: size)
                .background(
                    state == .active ? tint : tint.opacity(0.15),
                    in: .rect(cornerRadius: WCRadius.xs)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: WCRadius.xs)
                        .stroke(tint.opacity(state == .active ? 0.7 : 0.35))
                )
        }
        .buttonStyle(.plain)
        .disabled(disabled)
        .opacity(disabled ? 0.45 : 1)
    }
}

// MARK: - GlassChip

/// A small glass pill for status / lock indicators.
struct GlassChip: View {
    let text: String
    var color: Color = WC.accent
    var dot: Bool = false
    var icon: String? = nil

    var body: some View {
        HStack(spacing: WCSpace.xs) {
            if dot {
                Circle().fill(color).frame(width: 6, height: 6)
            }
            if let icon {
                Image(systemName: icon)
                    .font(.system(size: 9, weight: .semibold))
            }
            Text(text)
                .font(WCFont.label)
                .tracking(0.6)
                .lineLimit(1)
        }
        .foregroundStyle(color)
        .padding(.horizontal, WCSpace.sm)
        .padding(.vertical, WCSpace.xs + 1)
        .background(color.opacity(0.16), in: .rect(cornerRadius: WCRadius.xs))
        .overlay(
            RoundedRectangle(cornerRadius: WCRadius.xs)
                .stroke(color.opacity(0.40))
        )
    }
}

// MARK: - GlassToast

/// Transient glass error/status overlay. Auto-dismisses after `duration` seconds.
/// Bound to an optional string — present when non-nil, automatically nil'd after dismiss.
struct GlassToast: View {
    @Binding var message: String?
    var duration: TimeInterval = 3.0
    var color: Color = WC.warn

    var body: some View {
        if let text = message {
            VStack {
                Spacer()
                HStack(spacing: WCSpace.sm) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 13, weight: .bold))
                    Text(text)
                        .font(WCFont.bodyBold)
                        .lineLimit(2)
                        .minimumScaleFactor(0.8)
                    Spacer()
                }
                .foregroundStyle(color)
                .padding(.horizontal, WCSpace.md)
                .padding(.vertical, WCSpace.sm + WCSpace.xs)
                .background {
                    GlassSurface(cornerRadius: WCRadius.sm, tinted: true) {
                        Color.clear
                    }
                }
                .overlay(
                    RoundedRectangle(cornerRadius: WCRadius.sm)
                        .stroke(color.opacity(0.4))
                )
                .padding(.horizontal, WCSpace.lg)
                .padding(.bottom, WCSpace.xl)
                .transition(.move(edge: .bottom).combined(with: .opacity))
            }
            .task(id: text) {
                try? await Task.sleep(for: .seconds(duration))
                withAnimation(.easeOut(duration: 0.3)) {
                    message = nil
                }
            }
        }
    }
}

// MARK: - Previews

#Preview("GlassSurface / GlassCard") {
    ZStack {
        LinearGradient(
            colors: [Color(hex: 0x16344A), Color(hex: 0x8BB4C8)],
            startPoint: .top, endPoint: .bottom
        )
        .ignoresSafeArea()
        VStack(spacing: 16) {
            GlassCard {
                Text("Glass Card")
                    .font(WCFont.heading)
                    .foregroundStyle(WC.txt)
            }
        }
        .padding()
    }
    .preferredColorScheme(.dark)
}

#Preview("GlassButton variants") {
    ZStack {
        Color(hex: 0x16344A).ignoresSafeArea()
        VStack(spacing: 12) {
            GlassButton(label: "Normal", icon: "play.fill", role: .normal) {}
            GlassButton(label: "Active / On", icon: "record.circle", role: .active) {}
            GlassButton(label: "Stop / Danger", icon: "stop.fill", role: .danger) {}
            GlassButton(label: "Disabled", icon: "pause.fill", disabled: true) {}
        }
        .padding()
    }
    .preferredColorScheme(.dark)
}

#Preview("GlassIconButton states") {
    ZStack {
        Color(hex: 0x16344A).ignoresSafeArea()
        HStack(spacing: 12) {
            GlassIconButton(systemImage: "viewfinder", state: .normal) {}
            GlassIconButton(systemImage: "record.circle", state: .active) {}
            GlassIconButton(systemImage: "stop.fill", state: .danger) {}
        }
        .padding()
    }
    .preferredColorScheme(.dark)
}

#Preview("GlassChip") {
    ZStack {
        Color(hex: 0x16344A).ignoresSafeArea()
        VStack(spacing: 8) {
            GlassChip(text: "LOCKED", color: WC.ok, dot: true)
            GlassChip(text: "SEARCH", color: WC.warn)
            GlassChip(text: "AUTO", color: WC.accent, icon: "viewfinder")
        }
    }
    .preferredColorScheme(.dark)
}

#Preview("GlassToast") {
    @Previewable @State var msg: String? = "PTZ refused — camera is busy"
    ZStack {
        Color(hex: 0x16344A).ignoresSafeArea()
        GlassToast(message: $msg)
    }
    .preferredColorScheme(.dark)
}
