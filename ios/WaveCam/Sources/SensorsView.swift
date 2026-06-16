import SwiftUI

/// Read-only phone-on-tripod diagnostic (Stage 1). Shows the phone sensors AS THE
/// RIG RECEIVED THEM (validates the whole pipeline) vs the Wio base, gated by the
/// at-rig co-location check. No corrective use.
struct SensorsView: View {
    @Environment(WaveCamClient.self) private var client
    private var sensors: WCStatus.Sensors? { client.status?.sensors }

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                mountBadge
                OperatorCard(title: "HEADING") {
                    row("Phone (true)", fmtHeading(sensors?.phone?.trueHeadingDeg,
                                                   acc: sensors?.phone?.headingAcc))
                    row("Base", "— (no compass)")
                }
                OperatorCard(title: "HEADING BIAS (phone − calibrated)") {
                    row("Offset", fmtBias(sensors?.headingBiasDeg))
                }
                OperatorCard(title: "POSITION") {
                    row("Phone", fmtLatLon(sensors?.phone?.lat, sensors?.phone?.lon,
                                           acc: sensors?.phone?.hAcc, accUnit: "m"))
                    row("Base", fmtLatLon(sensors?.base?.lat, sensors?.base?.lon))
                    row("Phone↔base", fmtMeters(sensors?.coLocation?.phoneBaseDistM))
                }
                OperatorCard(title: "ALTITUDE") {
                    row("Phone GPS", fmtMeters(sensors?.phone?.altM, acc: sensors?.phone?.altAcc))
                    row("Phone baro (rel)", fmtMeters(sensors?.phone?.baroRelM))
                    row("Base", fmtMeters(sensors?.base?.altM))
                }
                OperatorCard(title: "FRESHNESS") {
                    row("Phone age", fmtSec(sensors?.phone?.ageSec))
                }
            }
            .padding(.horizontal, 16).padding(.vertical, 12)
        }
        .background(WC.bg.ignoresSafeArea())
    }

    @ViewBuilder private var mountBadge: some View {
        let at = sensors?.coLocation?.atRig
        let (txt, tint): (String, Color) =
            at == true ? ("PHONE MOUNTED ON RIG", WC.ok)
          : at == false ? ("PHONE NOT AT RIG — NOT A TRIPOD REFERENCE", WC.warn)
          : ("MOUNT UNCONFIRMED (no base fix)", WC.muted)
        Text(txt).font(WCFont.label).tracking(1.2).foregroundStyle(tint)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder private func row(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label).font(WCFont.body).foregroundStyle(WC.txt)
            Spacer()
            Text(value).font(WCFont.captionMono).foregroundStyle(WC.muted)
                .lineLimit(1).truncationMode(.middle)
        }
    }

    private func fmtHeading(_ d: Double?, acc: Double?) -> String {
        guard let d else { return "—" }
        let a: String
        if let acc, acc >= 0 { a = String(format: " ±%.0f°", acc) } else { a = " (invalid)" }
        return String(format: "%.1f°%@", d, a)
    }
    private func fmtBias(_ d: Double?) -> String {
        guard let d else { return "— (needs at-rig + a heading lock)" }
        return String(format: "%+.1f°", d)
    }
    private func fmtLatLon(_ la: Double?, _ lo: Double?, acc: Double? = nil, accUnit: String = "") -> String {
        guard let la, let lo else { return "—" }
        let a = acc.map { String(format: " ±%.0f%@", $0, accUnit) } ?? ""
        return String(format: "%.5f, %.5f%@", la, lo, a)
    }
    private func fmtMeters(_ m: Double?, acc: Double? = nil) -> String {
        guard let m else { return "—" }
        let a = acc.map { String(format: " ±%.0f", $0) } ?? ""
        return String(format: "%.1f m%@", m, a)
    }
    private func fmtSec(_ s: Double?) -> String { s.map { String(format: "%.1f s", $0) } ?? "—" }
}

#Preview {
    SensorsView()
        .environment(WaveCamClient(mode: .mock))
        .preferredColorScheme(.dark)
}
