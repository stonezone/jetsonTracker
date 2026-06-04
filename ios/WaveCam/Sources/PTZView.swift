import SwiftUI

// MARK: - JoystickPad

/// Velocity joystick that overlays the feed. The center nub supports tap + long-press
/// for the home gesture (feature-detected; pass `onHome` only when supported).
/// `semiTransparent` reduces opacity for overlay use over the live feed.
struct JoystickPad: View {
    @Binding var knobOffset: CGSize

    let diameter: CGFloat
    let onCommand: (Double, Double) -> Void
    let onStop: () -> Void
    /// When set, tapping or long-pressing the center nub fires this handler.
    var onHome: (() -> Void)? = nil
    /// When true, the pad background uses reduced-opacity gradients for feed overlay use.
    var semiTransparent: Bool = false

    private let deadzone: Double = 0.05
    private var commandRadius: CGFloat { diameter * 0.3565 }
    private var nubSize: CGFloat { diameter * 0.32 }

    var body: some View {
        ZStack {
            Circle()
                .fill(
                    RadialGradient(
                        colors: semiTransparent
                            ? [Color(hex: 0x1A2730, alpha: 0.75), Color(hex: 0x0E161D, alpha: 0.75)]
                            : [Color(hex: 0x1A2730), Color(hex: 0x0E161D)],
                        center: .center,
                        startRadius: 8,
                        endRadius: 118
                    )
                )
            Circle()
                .stroke(Color.white.opacity(0.08), lineWidth: 1)
            Circle()
                .stroke(style: StrokeStyle(lineWidth: 1, dash: [5, 5]))
                .foregroundStyle(Color.white.opacity(0.1))
                .padding(diameter * 0.13)
            Rectangle()
                .fill(Color.white.opacity(0.08))
                .frame(width: 1, height: diameter * 0.72)
            Rectangle()
                .fill(Color.white.opacity(0.08))
                .frame(width: diameter * 0.72, height: 1)
            JoystickLabels(diameter: diameter)
            JoystickNub(size: nubSize, onHome: onHome)
                .offset(knobOffset)
        }
        .frame(width: diameter, height: diameter)
        .contentShape(Circle())
        .gesture(
            DragGesture(minimumDistance: onHome != nil ? 4 : 0)
                .onChanged { value in update(location: value.location) }
                .onEnded { _ in reset() }
        )
    }

    private func update(location: CGPoint) {
        let center = CGPoint(x: diameter / 2, y: diameter / 2)
        let raw = CGSize(width: location.x - center.x, height: location.y - center.y)
        let clamped = raw.clamped(to: commandRadius)
        knobOffset = clamped

        let nextPan = Double(clamped.width / commandRadius).zeroed(deadzone: deadzone)
        let nextTilt = Double(-clamped.height / commandRadius).zeroed(deadzone: deadzone)
        onCommand(nextPan, nextTilt)
    }

    private func reset() {
        knobOffset = .zero
        onStop()
    }
}

// MARK: - JoystickLabels

private struct JoystickLabels: View {
    let diameter: CGFloat

    var body: some View {
        ZStack {
            Text("TILT +")
                .offset(y: -diameter * 0.44)
            Text("TILT -")
                .offset(y: diameter * 0.44)
            Text("PAN -")
                .offset(x: -diameter * 0.43)
            Text("PAN +")
                .offset(x: diameter * 0.43)
        }
        .font(.system(size: 9, weight: .semibold))
        .tracking(1.3)
        .foregroundStyle(WC.faint)
    }
}

// MARK: - JoystickNub

private struct JoystickNub: View {
    let size: CGFloat
    var onHome: (() -> Void)? = nil

    var body: some View {
        ZStack {
            Circle()
                .fill(
                    RadialGradient(
                        colors: [Color(hex: 0xFF8A4D), Color(hex: 0xE2540F)],
                        center: .top,
                        startRadius: 4,
                        endRadius: 42
                    )
                )
            Circle()
                .stroke(Color.white.opacity(0.18), lineWidth: 1)
            if onHome != nil {
                Circle()
                    .stroke(Color.white.opacity(0.55), lineWidth: 1.5)
                    .frame(width: size * 0.26, height: size * 0.26)
                Image(systemName: "house")
                    .font(.system(size: size * 0.18, weight: .semibold))
                    .foregroundStyle(Color.white.opacity(0.72))
            } else {
                Circle()
                    .stroke(Color.white.opacity(0.38), lineWidth: 1)
                    .frame(width: size * 0.19, height: size * 0.19)
            }
        }
        .frame(width: size, height: size)
        .shadow(color: WC.brand.opacity(0.45), radius: 18, y: 8)
        .onTapGesture { onHome?() }
        .onLongPressGesture(minimumDuration: 0.4) { onHome?() }
        .accessibilityLabel(onHome != nil ? "Home camera" : "Joystick nub")
        .accessibilityHint(onHome != nil ? "Tap or hold to return camera to home position" : "")
    }
}

// MARK: - Extensions (used by JoystickPad)

extension CGSize {
    func clamped(to radius: CGFloat) -> CGSize {
        let distance = sqrt(width * width + height * height)
        guard distance > radius, distance > 0 else { return self }
        let scale = radius / distance
        return CGSize(width: width * scale, height: height * scale)
    }
}

extension Double {
    func zeroed(deadzone: Double) -> Double {
        abs(self) < deadzone ? 0 : self
    }
}
