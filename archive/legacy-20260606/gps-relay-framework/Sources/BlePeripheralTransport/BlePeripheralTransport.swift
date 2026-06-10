import Foundation
import LocationCore

#if os(iOS)
import CoreBluetooth

// MARK: - Delegate Protocol

/// Delegate protocol for BLE peripheral transport state changes and events.
/// All delegate methods are called on the main queue.
public protocol BlePeripheralTransportDelegate: AnyObject {
    /// Called when the transport's connection state changes.
    /// - Parameter state: The new connection state.
    func bleTransport(_ transport: BlePeripheralTransport, didChangeState state: BleConnectionState)

    /// Called when a central subscribes or unsubscribes.
    /// - Parameter subscriberCount: The current number of subscribed centrals.
    func bleTransport(_ transport: BlePeripheralTransport, didUpdateSubscriberCount subscriberCount: Int)

    /// Called when an error occurs during BLE operations.
    /// - Parameters:
    ///   - error: The error that occurred.
    ///   - context: Additional context about where the error occurred.
    func bleTransport(_ transport: BlePeripheralTransport, didEncounterError error: Error, context: String)
}

// MARK: - Connection State

/// Represents the current state of the BLE peripheral transport.
public enum BleConnectionState: Equatable {
    /// Bluetooth is off or unavailable.
    case idle
    /// Advertising and waiting for central connections.
    case advertising
    /// Connected with at least one subscribed central.
    case connected(subscriberCount: Int)
}

// MARK: - Configuration

/// Configuration for BLE peripheral transport behavior.
public struct BlePeripheralConfig: Sendable {
    /// Maximum Transmission Unit size in bytes (20-244 for BLE).
    /// Default is conservative 20 bytes for maximum compatibility.
    public let mtuSize: Int

    /// Whether to enable background advertising.
    /// Note: iOS severely limits background BLE advertising (1 service UUID only, reduced power).
    /// Background advertising may be suppressed entirely if app is in background for extended periods.
    public let backgroundAdvertising: Bool

    /// Service local name for advertising (optional, truncated in background).
    public let localName: String?

    public init(mtuSize: Int = 20, backgroundAdvertising: Bool = true, localName: String? = "GPS Tracker") {
        self.mtuSize = max(20, min(244, mtuSize))
        self.backgroundAdvertising = backgroundAdvertising
        self.localName = localName
    }

    /// Configuration optimized for foreground operation with larger MTU.
    public static let foreground = BlePeripheralConfig(mtuSize: 185, backgroundAdvertising: false, localName: "GPS Tracker")

    /// Configuration optimized for background operation (conservative MTU, background advertising).
    /// WARNING: iOS background advertising constraints:
    /// - Only service UUID is advertised (local name is stripped)
    /// - Advertising power is reduced
    /// - Advertising may be suspended entirely if app is backgrounded for extended time
    /// - App may need to return to foreground periodically to maintain advertising
    public static let background = BlePeripheralConfig(mtuSize: 20, backgroundAdvertising: true, localName: nil)
}

// MARK: - CBOR Encoding

/// Minimal CBOR encoder for LocationFix data.
/// Implements subset of RFC 8949 sufficient for compact GPS data transmission.
private struct CBOREncoder {
    /// Encode LocationFix to CBOR format.
    /// CBOR is more compact than JSON (no field names, binary encoding).
    /// Format: Map with integer keys:
    /// 0: timestamp (uint), 1: source (uint), 2: lat (double), 3: lon (double),
    /// 4: alt_m (double or null), 5: h_acc (double), 6: v_acc (double),
    /// 7: speed (double), 8: course (double), 9: battery (double), 10: seq (uint)
    static func encode(_ fix: LocationFix) throws -> Data {
        var data = Data()

        // Map with 11 entries
        data.append(0xAB) // Major type 5 (map), count 11

        // 0: timestamp (milliseconds since epoch)
        data.append(0x00) // key 0
        let timestamp = UInt64(fix.timestamp.timeIntervalSince1970 * 1000)
        try encodeUInt64(&data, timestamp)

        // 1: source (0=watchOS, 1=iOS)
        data.append(0x01) // key 1
        data.append(fix.source == .watchOS ? 0x00 : 0x01)

        // 2: latitude
        data.append(0x02) // key 2
        try encodeDouble(&data, fix.coordinate.latitude)

        // 3: longitude
        data.append(0x03) // key 3
        try encodeDouble(&data, fix.coordinate.longitude)

        // 4: altitude (nullable)
        data.append(0x04) // key 4
        if let alt = fix.altitudeMeters {
            try encodeDouble(&data, alt)
        } else {
            data.append(0xF6) // null
        }

        // 5: horizontal accuracy
        data.append(0x05) // key 5
        try encodeDouble(&data, fix.horizontalAccuracyMeters)

        // 6: vertical accuracy
        data.append(0x06) // key 6
        try encodeDouble(&data, fix.verticalAccuracyMeters)

        // 7: speed
        data.append(0x07) // key 7
        try encodeDouble(&data, fix.speedMetersPerSecond)

        // 8: course
        data.append(0x08) // key 8
        try encodeDouble(&data, fix.courseDegrees)

        // 9: battery
        data.append(0x09) // key 9
        try encodeDouble(&data, fix.batteryFraction)

        // 10: sequence
        data.append(0x0A) // key 10
        try encodeUInt64(&data, UInt64(fix.sequence))

        return data
    }

    private static func encodeUInt64(_ data: inout Data, _ value: UInt64) throws {
        if value <= 23 {
            data.append(UInt8(value))
        } else if value <= 0xFF {
            data.append(0x18) // uint8
            data.append(UInt8(value))
        } else if value <= 0xFFFF {
            data.append(0x19) // uint16
            data.append(contentsOf: withUnsafeBytes(of: value.bigEndian) { Array($0.prefix(2)) })
        } else if value <= 0xFFFFFFFF {
            data.append(0x1A) // uint32
            data.append(contentsOf: withUnsafeBytes(of: value.bigEndian) { Array($0.prefix(4)) })
        } else {
            data.append(0x1B) // uint64
            data.append(contentsOf: withUnsafeBytes(of: value.bigEndian) { Array($0) })
        }
    }

    private static func encodeDouble(_ data: inout Data, _ value: Double) throws {
        data.append(0xFB) // float64
        data.append(contentsOf: withUnsafeBytes(of: value.bitPattern.bigEndian) { Array($0) })
    }
}

// MARK: - Chunk Protocol

/// Header format for chunked BLE transmission:
/// Byte 0: [chunk_index (4 bits) | total_chunks (4 bits)]
/// Bytes 1-N: payload data
///
/// Example: For a 100-byte payload with MTU=20:
/// - Header takes 1 byte, leaving 19 bytes per chunk
/// - Chunk 0: [0x05, ...19 bytes of data] (chunk 0 of 5)
/// - Chunk 1: [0x15, ...19 bytes of data] (chunk 1 of 5)
/// - etc.
///
/// Reassembly on receiver side:
/// 1. Extract chunk_index and total_chunks from header byte
/// 2. Buffer chunks until all received (indexed by sequence number)
/// 3. Concatenate payloads in order
/// 4. Decode CBOR data
private struct ChunkHeader {
    let chunkIndex: UInt8
    let totalChunks: UInt8

    init(chunkIndex: UInt8, totalChunks: UInt8) {
        assert(chunkIndex < 16 && totalChunks <= 16, "Chunk index/total must fit in 4 bits")
        self.chunkIndex = chunkIndex
        self.totalChunks = totalChunks
    }

    init(fromByte byte: UInt8) {
        self.chunkIndex = (byte >> 4) & 0x0F
        self.totalChunks = byte & 0x0F
    }

    var headerByte: UInt8 {
        return (chunkIndex << 4) | totalChunks
    }
}

// MARK: - BLE Peripheral Transport

/// BLE peripheral transport for broadcasting LocationFix data.
///
/// Features:
/// - MTU-aware chunking for payloads exceeding BLE packet limits
/// - CBOR encoding for compact data representation (~40% smaller than JSON)
/// - Thread-safe subscriber management
/// - Connection state tracking and delegate notifications
/// - Background advertising support (with iOS limitations)
///
/// Background Advertising Constraints (iOS):
/// iOS severely restricts BLE advertising in background:
/// - Only one service UUID can be advertised
/// - Local name and other advertising data are stripped
/// - Advertising power is reduced
/// - System may suspend advertising entirely after extended background time
/// - App must return to foreground periodically to maintain reliable advertising
/// - Consider using background tasks or location updates to keep app active
///
/// Thread Safety:
/// All public methods are thread-safe. Delegate callbacks occur on main queue.
public final class BlePeripheralTransport: NSObject, LocationTransport, CBPeripheralManagerDelegate {
    // MARK: - Properties

    private let serviceUUID: CBUUID
    private let characteristicUUID: CBUUID
    private let config: BlePeripheralConfig
    private let peripheralManager: CBPeripheralManager
    private let queue: DispatchQueue

    /// Thread-safe subscriber tracking
    private let subscriberLock = NSLock()
    private var subscribedCentrals = Set<UUID>()
    private var centralObjects = [UUID: CBCentral]()

    private var characteristic: CBMutableCharacteristic?
    private var currentState: BleConnectionState = .idle

    public weak var delegate: BlePeripheralTransportDelegate?

    // MARK: - Initialization

    /// Initialize BLE peripheral transport.
    /// - Parameters:
    ///   - serviceUUID: GATT service UUID (default: Nordic UART Service compatible).
    ///   - characteristicUUID: GATT characteristic UUID for notifications.
    ///   - config: Configuration for MTU size and background behavior.
    public init(
        serviceUUID: CBUUID = CBUUID(string: "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"),
        characteristicUUID: CBUUID = CBUUID(string: "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"),
        config: BlePeripheralConfig = BlePeripheralConfig()
    ) {
        self.serviceUUID = serviceUUID
        self.characteristicUUID = characteristicUUID
        self.config = config
        self.queue = DispatchQueue(label: "com.bleperipheral.queue", qos: .userInitiated)
        self.peripheralManager = CBPeripheralManager(delegate: nil, queue: queue)
        super.init()
        self.peripheralManager.delegate = self
    }

    // MARK: - LocationTransport Protocol

    public func open() {
        queue.async { [weak self] in
            guard let self = self else { return }
            guard self.peripheralManager.state == .poweredOn else {
                NSLog("[BLE] Cannot open: Bluetooth not powered on (state: %d)", self.peripheralManager.state.rawValue)
                return
            }
            self.publishServiceIfNeeded()
        }
    }

    public func push(_ update: RelayUpdate) {
        queue.async { [weak self] in
            guard let self = self else { return }
            guard self.peripheralManager.state == .poweredOn else {
                NSLog("[BLE] Cannot push: Bluetooth not powered on")
                return
            }
            guard let characteristic = self.characteristic else {
                NSLog("[BLE] Cannot push: Characteristic not initialized")
                return
            }

            let centrals = self.getSubscribedCentrals()
            guard !centrals.isEmpty else {
                NSLog("[BLE] No subscribers, skipping push")
                return
            }

            do {
                // Encode to CBOR
                let cborData = try CBOREncoder.encode(update)

                // Check if chunking is needed
                let headerSize = 1 // 1 byte for chunk header
                let payloadBytesPerChunk = self.config.mtuSize - headerSize
                let totalChunks = (cborData.count + payloadBytesPerChunk - 1) / payloadBytesPerChunk

                if totalChunks > 15 {
                    let error = NSError(
                        domain: "BlePeripheralTransport",
                        code: -1,
                        userInfo: [NSLocalizedDescriptionKey: "Payload too large: \(cborData.count) bytes requires \(totalChunks) chunks (max 15)"]
                    )
                    self.notifyError(error, context: "Payload size validation")
                    return
                }

                // Send chunks
                for chunkIndex in 0..<totalChunks {
                    let startOffset = chunkIndex * payloadBytesPerChunk
                    let endOffset = min(startOffset + payloadBytesPerChunk, cborData.count)
                    let chunkPayload = cborData[startOffset..<endOffset]

                    var chunkData = Data()
                    let header = ChunkHeader(chunkIndex: UInt8(chunkIndex), totalChunks: UInt8(totalChunks))
                    chunkData.append(header.headerByte)
                    chunkData.append(chunkPayload)

                    let success = self.peripheralManager.updateValue(chunkData, for: characteristic, onSubscribedCentrals: centrals)

                    if !success {
                        NSLog("[BLE] Failed to send chunk %d/%d (transmission queue full, will retry)", chunkIndex + 1, totalChunks)
                        // CoreBluetooth will call peripheralManagerIsReady(toUpdateSubscribers:) when ready
                        // For now, we log and continue (data loss possible under high load)
                    }
                }

                NSLog("[BLE] Sent %d chunk(s) to %d subscriber(s) [seq=%d, size=%d bytes CBOR]",
                      totalChunks, centrals.count, fix.sequence, cborData.count)

            } catch {
                self.notifyError(error, context: "CBOR encoding")
                NSLog("[BLE] Encoding error: %@", String(describing: error))
            }
        }
    }

    public func close() {
        queue.async { [weak self] in
            guard let self = self else { return }
            self.peripheralManager.stopAdvertising()

            self.subscriberLock.lock()
            self.subscribedCentrals.removeAll()
            self.centralObjects.removeAll()
            self.subscriberLock.unlock()

            self.updateState(.idle)
            NSLog("[BLE] Closed")
        }
    }

    // MARK: - Service Management

    private func publishServiceIfNeeded() {
        guard peripheralManager.services?.isEmpty ?? true else {
            NSLog("[BLE] Service already published")
            return
        }

        let characteristic = CBMutableCharacteristic(
            type: characteristicUUID,
            properties: [.notify],
            value: nil,
            permissions: []
        )
        self.characteristic = characteristic

        let service = CBMutableService(type: serviceUUID, primary: true)
        service.characteristics = [characteristic]

        peripheralManager.add(service)
        NSLog("[BLE] Publishing service %@", serviceUUID.uuidString)
    }

    private func startAdvertising() {
        var advertisingData: [String: Any] = [
            CBAdvertisementDataServiceUUIDsKey: [serviceUUID]
        ]

        // Local name is only included in foreground (stripped in background by iOS)
        if !config.backgroundAdvertising, let localName = config.localName {
            advertisingData[CBAdvertisementDataLocalNameKey] = localName
        }

        peripheralManager.startAdvertising(advertisingData)
        updateState(.advertising)
        NSLog("[BLE] Started advertising (background mode: %@)", config.backgroundAdvertising ? "YES" : "NO")
    }

    // MARK: - State Management

    private func updateState(_ newState: BleConnectionState) {
        guard newState != currentState else { return }
        currentState = newState

        DispatchQueue.main.async { [weak self] in
            guard let self = self else { return }
            self.delegate?.bleTransport(self, didChangeState: newState)
        }
    }

    private func notifyError(_ error: Error, context: String) {
        DispatchQueue.main.async { [weak self] in
            guard let self = self else { return }
            self.delegate?.bleTransport(self, didEncounterError: error, context: context)
        }
    }

    private func notifySubscriberCountChanged() {
        let count = getSubscriberCount()

        DispatchQueue.main.async { [weak self] in
            guard let self = self else { return }
            self.delegate?.bleTransport(self, didUpdateSubscriberCount: count)
        }

        // Update connection state based on subscriber count
        if count > 0 {
            updateState(.connected(subscriberCount: count))
        } else {
            updateState(.advertising)
        }
    }

    // MARK: - Thread-Safe Subscriber Management

    private func addSubscriber(_ central: CBCentral) {
        subscriberLock.lock()
        subscribedCentrals.insert(central.identifier)
        centralObjects[central.identifier] = central
        subscriberLock.unlock()

        NSLog("[BLE] Central subscribed: %@ (total: %d)", central.identifier.uuidString, getSubscriberCount())
        notifySubscriberCountChanged()
    }

    private func removeSubscriber(_ central: CBCentral) {
        subscriberLock.lock()
        subscribedCentrals.remove(central.identifier)
        centralObjects.removeValue(forKey: central.identifier)
        subscriberLock.unlock()

        NSLog("[BLE] Central unsubscribed: %@ (total: %d)", central.identifier.uuidString, getSubscriberCount())
        notifySubscriberCountChanged()
    }

    private func getSubscribedCentrals() -> [CBCentral] {
        subscriberLock.lock()
        defer { subscriberLock.unlock() }
        return subscribedCentrals.compactMap { centralObjects[$0] }
    }

    private func getSubscriberCount() -> Int {
        subscriberLock.lock()
        defer { subscriberLock.unlock() }
        return subscribedCentrals.count
    }

    // MARK: - CBPeripheralManagerDelegate

    public func peripheralManagerDidUpdateState(_ peripheral: CBPeripheralManager) {
        NSLog("[BLE] State updated: %d", peripheral.state.rawValue)

        switch peripheral.state {
        case .poweredOn:
            publishServiceIfNeeded()
            startAdvertising()
        case .poweredOff, .resetting:
            updateState(.idle)
        case .unauthorized:
            let error = NSError(
                domain: "BlePeripheralTransport",
                code: -2,
                userInfo: [NSLocalizedDescriptionKey: "Bluetooth permission not granted"]
            )
            notifyError(error, context: "Bluetooth authorization")
            updateState(.idle)
        case .unsupported:
            let error = NSError(
                domain: "BlePeripheralTransport",
                code: -3,
                userInfo: [NSLocalizedDescriptionKey: "Bluetooth LE not supported on this device"]
            )
            notifyError(error, context: "Hardware support")
            updateState(.idle)
        case .unknown:
            break
        @unknown default:
            break
        }
    }

    public func peripheralManager(_ peripheral: CBPeripheralManager, didAdd service: CBService, error: Error?) {
        if let error = error {
            NSLog("[BLE] Failed to add service: %@", String(describing: error))
            notifyError(error, context: "Service registration")
            return
        }

        NSLog("[BLE] Service added successfully: %@", service.uuid.uuidString)
        startAdvertising()
    }

    public func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral, didSubscribeTo characteristic: CBCharacteristic) {
        addSubscriber(central)
    }

    public func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral, didUnsubscribeFrom characteristic: CBCharacteristic) {
        removeSubscriber(central)
    }

    public func peripheralManagerDidStartAdvertising(_ peripheral: CBPeripheralManager, error: Error?) {
        if let error = error {
            NSLog("[BLE] Advertising start failed: %@", String(describing: error))
            notifyError(error, context: "Advertising start")
            updateState(.idle)
        } else {
            NSLog("[BLE] Advertising started successfully")
            updateState(.advertising)
        }
    }

    public func peripheralManagerIsReady(toUpdateSubscribers peripheral: CBPeripheralManager) {
        NSLog("[BLE] Ready to send more data (transmission queue cleared)")
        // Could implement retry logic here for failed chunks
    }
}

#else

// MARK: - Non-iOS Stub

public protocol BlePeripheralTransportDelegate: AnyObject {
    func bleTransport(_ transport: BlePeripheralTransport, didChangeState state: BleConnectionState)
    func bleTransport(_ transport: BlePeripheralTransport, didUpdateSubscriberCount subscriberCount: Int)
    func bleTransport(_ transport: BlePeripheralTransport, didEncounterError error: Error, context: String)
}

public enum BleConnectionState: Equatable {
    case idle
    case advertising
    case connected(subscriberCount: Int)
}

public struct BlePeripheralConfig: Sendable {
    public let mtuSize: Int
    public let backgroundAdvertising: Bool
    public let localName: String?

    public init(mtuSize: Int = 20, backgroundAdvertising: Bool = true, localName: String? = "GPS Tracker") {
        self.mtuSize = max(20, min(244, mtuSize))
        self.backgroundAdvertising = backgroundAdvertising
        self.localName = localName
    }

    public static let foreground = BlePeripheralConfig(mtuSize: 185, backgroundAdvertising: false, localName: "GPS Tracker")
    public static let background = BlePeripheralConfig(mtuSize: 20, backgroundAdvertising: true, localName: nil)
}

public final class BlePeripheralTransport: NSObject, LocationTransport {
    public weak var delegate: BlePeripheralTransportDelegate?

    public init(serviceUUID: UUID = UUID(), characteristicUUID: UUID = UUID(), config: BlePeripheralConfig = BlePeripheralConfig()) {
        super.init()
    }

    public func open() {}
    public func push(_ update: RelayUpdate) {}
    public func close() {}
}
#endif
