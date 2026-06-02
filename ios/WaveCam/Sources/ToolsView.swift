import SwiftUI

/// Secondary tools that should stay available without pushing connection setup
/// behind iOS's automatic More tab.
struct ToolsView: View {
    private enum Tool: String, CaseIterable, Hashable {
        case tune = "Tune"
        case agent = "Agent"
        case dashboard = "Dash"
    }

    @State private var selectedTool = Tool.tune

    var body: some View {
        VStack(spacing: 0) {
            Picker("Tool", selection: $selectedTool) {
                ForEach(Tool.allCases, id: \.self) { tool in
                    Text(tool.rawValue).tag(tool)
                }
            }
            .pickerStyle(.segmented)
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(WC.ink)

            switch selectedTool {
            case .tune:
                TuneView()
            case .agent:
                AgentView()
            case .dashboard:
                DashView()
            }
        }
        .background(WC.bg.ignoresSafeArea())
    }
}

#Preview {
    ToolsView()
        .environment(WaveCamClient(mode: .mock))
        .preferredColorScheme(.dark)
}
