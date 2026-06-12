import SwiftUI

/// Full-screen "Record Session" view for T4.1 offline validation.
/// Presents start/stop controls + live sample counters.
struct RecordSessionView: View {
    @StateObject private var recorder = WatchSessionRecorder()

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            if recorder.isRecording {
                recordingView
            } else {
                idleView
            }
        }
        .navigationTitle("Record")
    }

    // MARK: - Idle

    private var idleView: some View {
        VStack(spacing: 10) {
            Image(systemName: "waveform.path.ecg")
                .font(.system(size: 28))
                .foregroundStyle(.cyan)

            Text("SESSION RECORDER")
                .font(.system(size: 12, weight: .bold, design: .monospaced))
                .foregroundStyle(.gray)

            if !recorder.statusMessage.isEmpty {
                Text(recorder.statusMessage)
                    .font(.system(size: 10))
                    .foregroundStyle(.orange)
                    .multilineTextAlignment(.center)
            }

            Button {
                recorder.startRecording()
            } label: {
                Text(recorder.startPending ? "Starting…" : "Start Recording")
                    .font(.system(size: 14, weight: .bold))
                    .foregroundStyle(.black)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
                    .background(Color.cyan)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
            }
            .buttonStyle(.plain)
            .disabled(recorder.startPending)
        }
        .padding(.horizontal, 6)
    }

    // MARK: - Active recording

    private var recordingView: some View {
        VStack(spacing: 8) {
            // REC indicator
            HStack(spacing: 5) {
                Circle()
                    .fill(Color.red)
                    .frame(width: 8, height: 8)
                Text("RECORDING")
                    .font(.system(size: 12, weight: .bold, design: .monospaced))
                    .foregroundStyle(.red)
            }

            Divider().overlay(Color.gray.opacity(0.4))

            // Sample counters
            VStack(alignment: .leading, spacing: 4) {
                counterRow(label: "GPS", count: recorder.gpsSampleCount, color: .green)
                counterRow(label: "IMU", count: recorder.motionSampleCount, color: .cyan)
            }

            Spacer(minLength: 4)

            // Stop button
            Button {
                recorder.stopRecording()
            } label: {
                Text("STOP & SEND")
                    .font(.system(size: 14, weight: .black, design: .monospaced))
                    .foregroundStyle(.white)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
                    .background(Color.red)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
            }
            .buttonStyle(.plain)

            if !recorder.statusMessage.isEmpty, recorder.statusMessage != "Recording" {
                Text(recorder.statusMessage)
                    .font(.system(size: 9))
                    .foregroundStyle(.orange)
            }
        }
        .padding(.horizontal, 4)
        .padding(.vertical, 6)
    }

    private func counterRow(label: String, count: Int, color: Color) -> some View {
        HStack {
            Text(label)
                .font(.system(size: 11, weight: .semibold, design: .monospaced))
                .foregroundStyle(color)
            Spacer()
            Text("\(count)")
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(.white)
        }
    }
}
