import Foundation

/// Thread-safe sequence number generator for tracking GPS fixes
/// Uses session-scoped IDs combined with atomic counter to prevent collisions
/// Issue #3: Fixes collision risk from timestamp-based sequence generation
public final class AtomicSequenceGenerator: @unchecked Sendable {
    
    /// Shared instance for convenience (each app should typically use one generator)
    public static let shared = AtomicSequenceGenerator()
    
    private var counter: UInt64 = 0
    private let lock = NSLock()
    
    /// Unique session identifier (changes on each app launch)
    private let sessionID: UInt16
    
    public init() {
        // Generate random session ID on init (16 bits = 65536 possible values)
        self.sessionID = UInt16.random(in: 0..<UInt16.max)
    }
    
    /// Generate the next sequence number
    /// Format: [16-bit session ID][48-bit counter]
    /// This ensures:
    /// - No collisions within a session (counter is monotonic)
    /// - Very low collision probability across app restarts (random session ID)
    /// - Detectable session boundaries on receiver side
    public func next() -> Int {
        lock.lock()
        defer { lock.unlock() }
        
        counter += 1
        
        // Combine session ID (top 16 bits) with counter (bottom 48 bits)
        // This gives us 281 trillion unique sequences per session
        let combined = (UInt64(sessionID) << 48) | (counter & 0x0000FFFFFFFFFFFF)
        
        // Convert to Int (will fit in 64-bit signed int)
        return Int(combined % UInt64(Int.max))
    }
    
    /// Reset the generator (typically only for testing)
    public func reset() {
        lock.lock()
        defer { lock.unlock() }
        counter = 0
    }
    
    /// Get current session ID (useful for debugging)
    public var currentSessionID: UInt16 {
        sessionID
    }
    
    /// Get current counter value (useful for debugging)
    public var currentCount: UInt64 {
        lock.lock()
        defer { lock.unlock() }
        return counter
    }
}
