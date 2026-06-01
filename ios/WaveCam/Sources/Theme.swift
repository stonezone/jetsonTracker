import SwiftUI

/// WaveCam instrument palette. Orange = the tracked subject (Zack's rashguard) and
/// primary actions; teal = healthy telemetry; red = emergency-stop ONLY.
enum WC {
    static let bg     = Color(hex: 0x070B0F)
    static let ink    = Color(hex: 0x0D141B)
    static let panel  = Color(hex: 0x121B24)
    static let panel2 = Color(hex: 0x16212C)
    static let line   = Color.white.opacity(0.08)
    static let txt    = Color(hex: 0xE9EFF4)
    static let muted  = Color(hex: 0x8A99A6)
    static let faint  = Color(hex: 0x5B6873)
    static let brand  = Color(hex: 0xFF6A1F)   // subject / primary
    static let ok     = Color(hex: 0x37D6C2)   // healthy telemetry
    static let warn   = Color(hex: 0xFFB020)
    static let kill   = Color(hex: 0xFF3B30)   // emergency-stop only
}

extension Color {
    init(hex: UInt, alpha: Double = 1) {
        self.init(
            .sRGB,
            red:   Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue:  Double(hex & 0xFF) / 255,
            opacity: alpha
        )
    }
}
