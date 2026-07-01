import SwiftUI

// MARK: - Command state

/// Finite states for the manual PTZ command path.
/// Pending states (stopping / startingAuto) suppress backend sync so an in-flight
/// round-trip cannot be clobbered by the 1Hz status poll. Extracted here as the single
/// source of truth for the manual PTZ controller used by MergedLiveView.
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
    /// H12: minimum spacing between drag-driven sends. Every DragGesture tick used to
    /// spawn a POST — a 2 s stick drag fired 100+ concurrent, unordered requests.
    private let sendMinInterval: TimeInterval = 0.1

    private var velocityRepeatTask: Task<Void, Never>?
    private var zoomRepeatTask: Task<Void, Never>?
    private var lastVelocitySendAt = Date.distantPast
    private var pendingVelocityFlushTask: Task<Void, Never>?
    private var lastZoomSendAt = Date.distantPast
    private var pendingZoomFlushTask: Task<Void, Never>?

    // MARK: joystick

    func sendVelocity(pan: Double, tilt: Double, client: WaveCamClient) {
        self.pan = pan
        self.tilt = tilt
        let isActive = pan != 0 || tilt != 0
        if isActive { refusalText = nil }
        commandState = isActive ? .manual : .idle
        if !isActive {
            // Zero/stop ALWAYS bypasses the throttle — a release-stop must never wait
            // behind a coalescing window.
            cancelPendingVelocityFlush()
            lastVelocitySendAt = Date()
            Task { await client.ptzVelocity(pan: 0, tilt: 0) }
            stopVelocityRepeat()
            return
        }
        throttledVelocitySend(client: client)
        startVelocityRepeat(client: client)
    }

    /// H12: send now if >=100 ms since the last send; otherwise remember the latest
    /// pan/tilt (already stored on self) and flush it when the interval elapses.
    private func throttledVelocitySend(client: WaveCamClient) {
        let now = Date()
        let elapsed = now.timeIntervalSince(lastVelocitySendAt)
        if elapsed >= sendMinInterval {
            cancelPendingVelocityFlush()
            lastVelocitySendAt = now
            let (p, t) = (pan, tilt)
            Task { await client.ptzVelocity(pan: p, tilt: t) }
            return
        }
        guard pendingVelocityFlushTask == nil else { return }   // latest value flushes anyway
        let delayNs = UInt64(max(0, sendMinInterval - elapsed) * 1_000_000_000)
        pendingVelocityFlushTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: delayNs)
            guard let self, !Task.isCancelled else { return }
            self.pendingVelocityFlushTask = nil
            // A release (pan/tilt zero) already sent its own immediate stop.
            guard self.pan != 0 || self.tilt != 0 else { return }
            self.lastVelocitySendAt = Date()
            await client.ptzVelocity(pan: self.pan, tilt: self.tilt)
        }
    }

    private func cancelPendingVelocityFlush() {
        pendingVelocityFlushTask?.cancel()
        pendingVelocityFlushTask = nil
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
        // Stop must land even over lossy Wi-Fi: retry until the POST is accepted so a
        // dropped release-stop doesn't coast until the ~800ms backend deadman. Abort the
        // moment the operator re-grabs the stick (pan/tilt != 0) so we don't fight a new
        // move command. (review C3)
        Task { [weak self] in
            for attempt in 0..<3 {
                guard let self, self.pan == 0, self.tilt == 0 else { return }
                if await client.ptzStop(hold: false) { return }
                if attempt < 2 { try? await Task.sleep(nanoseconds: 120_000_000) }
            }
        }
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
        if value == 0 {
            // Zero/stop ALWAYS bypasses the throttle (see sendVelocity).
            cancelPendingZoomFlush()
            lastZoomSendAt = Date()
            Task { await client.zoom(0) }
            return
        }
        throttledZoomSend(client: client)
        zoomRepeatTask = Task { @MainActor [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: self.zoomRepeatIntervalNs)
                guard !Task.isCancelled else { return }
                self.lastZoomSendAt = Date()
                await client.zoom(self.zoomCommand)
            }
        }
    }

    /// H12: throttle slider-driven zoom sends exactly like joystick velocity.
    private func throttledZoomSend(client: WaveCamClient) {
        let now = Date()
        let elapsed = now.timeIntervalSince(lastZoomSendAt)
        if elapsed >= sendMinInterval {
            cancelPendingZoomFlush()
            lastZoomSendAt = now
            let z = zoomCommand
            Task { await client.zoom(z) }
            return
        }
        guard pendingZoomFlushTask == nil else { return }   // latest value flushes anyway
        let delayNs = UInt64(max(0, sendMinInterval - elapsed) * 1_000_000_000)
        pendingZoomFlushTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: delayNs)
            guard let self, !Task.isCancelled else { return }
            self.pendingZoomFlushTask = nil
            // A zoom stop already sent its own immediate zero.
            guard self.zoomCommand != 0 else { return }
            self.lastZoomSendAt = Date()
            await client.zoom(self.zoomCommand)
        }
    }

    private func cancelPendingZoomFlush() {
        pendingZoomFlushTask?.cancel()
        pendingZoomFlushTask = nil
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

    private func backendHeldStop(client: WaveCamClient) -> Bool {
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
                self.lastVelocitySendAt = Date()
                await client.ptzVelocity(pan: self.pan, tilt: self.tilt)
            }
        }
    }

    private func stopVelocityRepeat() {
        velocityRepeatTask?.cancel()
        velocityRepeatTask = nil
        // A queued coalesced send must not fire after the stick is released/stopped.
        cancelPendingVelocityFlush()
    }

    private func resetZoomCommand(sendStop: Bool, client: WaveCamClient) {
        zoomRepeatTask?.cancel()
        zoomRepeatTask = nil
        cancelPendingZoomFlush()
        zoomCommand = 0
        if sendStop {
            // Stop bypasses the throttle — sent immediately.
            lastZoomSendAt = Date()
            Task { await client.zoom(0) }
        }
    }
}
