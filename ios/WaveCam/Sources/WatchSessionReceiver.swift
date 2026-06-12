import Foundation
import WatchConnectivity

/// Receives JSONL session files transferred from WaveCamWatch and saves them
/// to the app's Documents folder (visible via Files app, UIFileSharingEnabled).
///
/// Activate once at app launch. Thread-safe: WCSessionDelegate callbacks arrive
/// on an arbitrary thread; file operations use the default POSIX FileManager.
final class WatchSessionReceiver: NSObject, WCSessionDelegate {

    static let shared = WatchSessionReceiver()

    private override init() {}

    func activate() {
        guard WCSession.isSupported() else { return }
        WCSession.default.delegate = self
        WCSession.default.activate()
    }

    // MARK: - WCSessionDelegate

    func session(_ session: WCSession,
                 activationDidCompleteWith activationState: WCSessionActivationState,
                 error: Error?) {}

    func sessionDidBecomeInactive(_ session: WCSession) {}
    func sessionDidDeactivate(_ session: WCSession) {
        // Re-activate after watch switch (iOS-required).
        WCSession.default.activate()
    }

    /// Called when a file transfer from the watch completes.
    func session(_ session: WCSession, didReceive file: WCSessionFile) {
        guard let metadata = file.metadata,
              (metadata["kind"] as? String) == "watch_session" else { return }

        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let dest = docs.appendingPathComponent(file.fileURL.lastPathComponent)

        // Remove stale copy if present (retried transfer).
        try? FileManager.default.removeItem(at: dest)
        do {
            try FileManager.default.copyItem(at: file.fileURL, to: dest)
        } catch {
            // Non-fatal; the temporary source file will be cleaned up by the system.
        }
    }
}
