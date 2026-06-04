import SwiftUI

// MARK: - Command state

/// Finite states for the manual PTZ command path.
/// Pending states (stopping / startingAuto) suppress backend sync so an in-flight
/// round-trip cannot be clobbered by the 1Hz status poll. This mirrors the
/// previous PTZView-local enum; extracting it here makes it the single source of
/// truth shared by PTZView and MergedLiveView.
enum PTZCommandState {
    case idle
    case manual
    case held
    case auto
    case stopping
    case startingAuto

    var isPending: Bool { self == .stopping || self == .startingAuto }
    var isAutoActive: Bool { self == .auto || self == .startingAuto }
    var isStopActive: Bool { self == .held || self == .stopping }
}

// MARK: - Controller

/// Owns manual-PTZ command state, velocity-repeat timer, and zoom-repeat timer.
/// Views instantiate this with `@State` so its lifetime is tied to the view tree.
/// The controller calls into WaveCamClient but never owns the client — the client
/// is injected per-call so it always reads the current environment value.
@MainActor
@Observable
final class PTZManualController {
    // Joystick position (normalised -1…+1)
    private(set) var pan: Double = 0
    private(set) var tilt: Double = 0
    // Knob visual offset — written by the joystick view, read back for rendering
    var knobOffset: CGSize = .zero

    private(set) var zoomCommand: Double = 0
    private(set) var commandState = PTZCommandState.idle
    private(set) var refusalText: String?

    private let velocityRepeatIntervalNs: UInt64 = 300_000_000
    private let zoomRepeatIntervalNs: UInt64 = 300_000_000

    private var velocityRepeatTask: Task<Void, Never>?
    private var zoomRepeatTask: Task<Void, Never>?

    // MARK: joystick

    func sendVelocity(pan: Double, tilt: Double, client: WaveCamClient) {
        self.pan = pan
        self.tilt = tilt
        let isActive = pan != 0 || tilt != 0
        if isActive { refusalText = nil }
        commandState = isActive ? .manual : .idle
        Task { await client.ptzVelocity(pan: pan, tilt: tilt) }
        if isActive {
            startVelocityRepeat(client: client)
        } else {
            stopVelocityRepeat()
        }
    }

    func releaseManualPTZ(client: WaveCamClient) {
        stopVelocityRepeat()
        resetZoomCommand(sendStop: false, client: client)
        pan = 0
        tilt = 0
        knobOffset = .zero
        if commandState != .held {
            commandState = .idle
        }
        Task { await client.ptzStop(hold: false) }
    }

    func holdPTZ(client: WaveCamClient) {
        stopVelocityRepeat()
        pan = 0
        tilt = 0
        knobOffset = .zero
        resetZoomCommand(sendStop: false, client: client)
        refusalText = nil
        commandState = .stopping
        Task { @MainActor [weak self] in
            guard let self else { return }
            let accepted = await client.ptzStop(hold: true)
            commandState = accepted ? .held : .idle
            if !accepted { refusalText = "Stop PTZ not confirmed by the camera." }
            syncCommandState(with: client)
        }
    }

    func startAutoPTZ(client: WaveCamClient) {
        stopVelocityRepeat()
        pan = 0
        tilt = 0
        knobOffset = .zero
        resetZoomCommand(sendStop: false, client: client)
        refusalText = nil
        commandState = .startingAuto
        Task { @MainActor [weak self] in
            guard let self else { return }
            let accepted = await client.ptzStartAuto()
            commandState = accepted ? .auto : .idle
            if !accepted {
                refusalText = client.killed
                    ? "Resume first — camera is stopped (Emergency Stop latched)."
                    : "Start Auto refused — PTZ is busy or unavailable."
            }
            syncCommandState(with: client)
        }
    }

    /// Call this when the home gesture fires. Returns false if home is not confirmed.
    func ptzHome(client: WaveCamClient) {
        stopVelocityRepeat()
        pan = 0
        tilt = 0
        knobOffset = .zero
        resetZoomCommand(sendStop: false, client: client)
        refusalText = nil
        Task { @MainActor [weak self] in
            guard let self else { return }
            let accepted = await client.ptzHome()
            if !accepted {
                refusalText = client.killed
                    ? "Resume first — camera is stopped (Emergency Stop latched)."
                    : "Home not confirmed by the camera."
            }
        }
    }

    // MARK: zoom

    func updateZoom(_ value: Double, client: WaveCamClient) {
        zoomRepeatTask?.cancel()
        zoomRepeatTask = nil
        zoomCommand = value
        if value != 0 { commandState = .manual }
        Task { await client.zoom(value) }
        guard value != 0 else { return }
        zoomRepeatTask = Task { @MainActor [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: self.zoomRepeatIntervalNs)
                guard !Task.isCancelled else { return }
                await client.zoom(self.zoomCommand)
            }
        }
    }

    func stopZoomCommand(client: WaveCamClient) {
        resetZoomCommand(sendStop: true, client: client)
    }

    // MARK: sync

    /// Backend status poll fired; reconcile local command state. Safe to call from
    /// .onChange(of: client.status?.revision).
    func syncCommandState(with client: WaveCamClient) {
        guard !commandState.isPending else { return }
        if client.owner.isAutonomousPTZOwner {
            commandState = .auto
        } else if backendHeldStop(client: client) {
            commandState = .held
        } else if commandState == .auto || commandState == .held {
            commandState = .idle
        }
    }

    func backendHeldStop(client: WaveCamClient) -> Bool {
        guard let ptz = client.status?.ptz else { return false }
        return ptz.owner == "manual" && ptz.panTiltCmd?.lowercased() == "stop"
    }

    // MARK: cleanup

    func cleanup(client: WaveCamClient) {
        stopVelocityRepeat()
        stopZoomCommand(client: client)
    }

    // MARK: private

    private func startVelocityRepeat(client: WaveCamClient) {
        guard velocityRepeatTask == nil else { return }
        velocityRepeatTask = Task { @MainActor [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: self.velocityRepeatIntervalNs)
                guard !Task.isCancelled else { return }
                guard self.pan != 0 || self.tilt != 0 else {
                    self.velocityRepeatTask = nil
                    return
                }
                await client.ptzVelocity(pan: self.pan, tilt: self.tilt)
            }
        }
    }

    private func stopVelocityRepeat() {
        velocityRepeatTask?.cancel()
        velocityRepeatTask = nil
    }

    private func resetZoomCommand(sendStop: Bool, client: WaveCamClient) {
        zoomRepeatTask?.cancel()
        zoomRepeatTask = nil
        zoomCommand = 0
        if sendStop {
            Task { await client.zoom(0) }
        }
    }
}
