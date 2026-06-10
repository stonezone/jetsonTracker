import Foundation

// MARK: - CBOR Encoding

/// Minimal CBOR encoder for GPS relay data.
/// Implements subset of RFC 8949 sufficient for compact GPS data transmission.
///
/// CBOR provides ~40% size reduction over JSON by:
/// - Using integer keys instead of string field names
/// - Binary encoding of numbers
/// - Compact length encoding
///
/// This is particularly important for cellular data budgets and latency.
public struct GPSCBOREncoder {

    public init() {}

    // MARK: - LocationFix Encoding

    /// Encode LocationFix to CBOR format.
    ///
    /// Format: Map with integer keys:
    /// - 0: timestamp (uint64 - milliseconds since epoch)
    /// - 1: source (uint8 - 0=watchOS, 1=iOS)
    /// - 2: latitude (float64)
    /// - 3: longitude (float64)
    /// - 4: altitude_m (float64 or null)
    /// - 5: h_accuracy_m (float64)
    /// - 6: v_accuracy_m (float64)
    /// - 7: speed_mps (float64)
    /// - 8: course_deg (float64)
    /// - 9: heading_deg (float64 or null)
    /// - 10: battery (float64)
    /// - 11: sequence (uint32)
    ///
    /// - Returns: CBOR-encoded binary data (~80-90 bytes vs ~200+ bytes JSON)
    public func encode(_ fix: LocationFix) throws -> Data {
        var data = Data()

        // Map with 12 entries (0xAC = major type 5, count 12)
        data.append(0xAC)

        // 0: timestamp (milliseconds since epoch)
        data.append(0x00)
        let timestamp = UInt64(fix.timestamp.timeIntervalSince1970 * 1000)
        encodeUInt64(&data, timestamp)

        // 1: source (0=watchOS, 1=iOS)
        data.append(0x01)
        data.append(fix.source == .watchOS ? 0x00 : 0x01)

        // 2: latitude
        data.append(0x02)
        encodeDouble(&data, fix.coordinate.latitude)

        // 3: longitude
        data.append(0x03)
        encodeDouble(&data, fix.coordinate.longitude)

        // 4: altitude (nullable)
        data.append(0x04)
        if let alt = fix.altitudeMeters {
            encodeDouble(&data, alt)
        } else {
            data.append(0xF6) // CBOR null
        }

        // 5: horizontal accuracy
        data.append(0x05)
        encodeDouble(&data, fix.horizontalAccuracyMeters)

        // 6: vertical accuracy
        data.append(0x06)
        encodeDouble(&data, fix.verticalAccuracyMeters)

        // 7: speed
        data.append(0x07)
        encodeDouble(&data, fix.speedMetersPerSecond)

        // 8: course
        data.append(0x08)
        encodeDouble(&data, fix.courseDegrees)

        // 9: heading (nullable)
        data.append(0x09)
        if let heading = fix.headingDegrees {
            encodeDouble(&data, heading)
        } else {
            data.append(0xF6) // CBOR null
        }

        // 10: battery
        data.append(0x0A)
        encodeDouble(&data, fix.batteryFraction)

        // 11: sequence
        data.append(0x0B)
        encodeUInt64(&data, UInt64(fix.sequence))

        return data
    }

    // MARK: - RelayUpdate Encoding

    /// Encode RelayUpdate to CBOR format.
    ///
    /// Format: Map with integer keys:
    /// - 0: base (LocationFix or null)
    /// - 1: remote (LocationFix or null)
    /// - 2: fused (LocationFix or null)
    /// - 3: latency (LatencyInfo or null)
    ///
    /// - Returns: CBOR-encoded binary data
    public func encode(_ update: RelayUpdate) throws -> Data {
        var data = Data()

        // Count non-nil fields
        var fieldCount: UInt8 = 0
        if update.base != nil { fieldCount += 1 }
        if update.remote != nil { fieldCount += 1 }
        if update.fused != nil { fieldCount += 1 }
        if update.latency != nil { fieldCount += 1 }

        // Map header
        data.append(0xA0 | fieldCount)

        // 0: base
        if let base = update.base {
            data.append(0x00)
            let fixData = try encode(base)
            data.append(fixData)
        }

        // 1: remote
        if let remote = update.remote {
            data.append(0x01)
            let fixData = try encode(remote)
            data.append(fixData)
        }

        // 2: fused
        if let fused = update.fused {
            data.append(0x02)
            let fixData = try encode(fused)
            data.append(fixData)
        }

        // 3: latency info
        if let latency = update.latency {
            data.append(0x03)
            try encodeLatencyInfo(&data, latency)
        }

        return data
    }

    // MARK: - Private Encoding Helpers

    private func encodeUInt64(_ data: inout Data, _ value: UInt64) {
        if value <= 23 {
            data.append(UInt8(value))
        } else if value <= 0xFF {
            data.append(0x18)
            data.append(UInt8(value))
        } else if value <= 0xFFFF {
            data.append(0x19)
            var be = value.bigEndian
            data.append(contentsOf: withUnsafeBytes(of: &be) { Array($0.suffix(2)) })
        } else if value <= 0xFFFFFFFF {
            data.append(0x1A)
            var be = value.bigEndian
            data.append(contentsOf: withUnsafeBytes(of: &be) { Array($0.suffix(4)) })
        } else {
            data.append(0x1B)
            var be = value.bigEndian
            data.append(contentsOf: withUnsafeBytes(of: &be) { Array($0) })
        }
    }

    private func encodeDouble(_ data: inout Data, _ value: Double) {
        data.append(0xFB) // CBOR float64
        var be = value.bitPattern.bigEndian
        data.append(contentsOf: withUnsafeBytes(of: &be) { Array($0) })
    }

    private func encodeString(_ data: inout Data, _ value: String) {
        let utf8 = value.utf8
        let count = utf8.count

        if count <= 23 {
            data.append(0x60 | UInt8(count))
        } else if count <= 0xFF {
            data.append(0x78)
            data.append(UInt8(count))
        } else {
            data.append(0x79)
            var be = UInt16(count).bigEndian
            data.append(contentsOf: withUnsafeBytes(of: &be) { Array($0) })
        }
        data.append(contentsOf: utf8)
    }

    private func encodeLatencyInfo(_ data: inout Data, _ info: LatencyInfo) throws {
        // Map with 4 fields (all nullable)
        data.append(0xA4)

        // 0: gpsToRelayMs
        data.append(0x00)
        if let val = info.gpsToRelayMs {
            encodeDouble(&data, val)
        } else {
            data.append(0xF6) // CBOR null
        }

        // 1: relayToTransportMs
        data.append(0x01)
        if let val = info.relayToTransportMs {
            encodeDouble(&data, val)
        } else {
            data.append(0xF6)
        }

        // 2: transportRttMs
        data.append(0x02)
        if let val = info.transportRttMs {
            encodeDouble(&data, val)
        } else {
            data.append(0xF6)
        }

        // 3: totalMs
        data.append(0x03)
        if let val = info.totalMs {
            encodeDouble(&data, val)
        } else {
            data.append(0xF6)
        }
    }
}

// MARK: - CBOR Decoder

/// Minimal CBOR decoder for GPS relay data.
public struct GPSCBORDecoder {

    public init() {}

    /// Decode LocationFix from CBOR data
    public func decodeFix(_ data: Data) throws -> LocationFix {
        var offset = 0

        // Expect map
        guard data.count > 0, (data[offset] & 0xE0) == 0xA0 else {
            throw CBORError.invalidFormat("Expected CBOR map")
        }
        let mapSize = Int(data[offset] & 0x1F)
        offset += 1

        var timestamp: Date?
        var source: LocationFix.Source?
        var latitude: Double?
        var longitude: Double?
        var altitude: Double?
        var hAccuracy: Double?
        var vAccuracy: Double?
        var speed: Double?
        var course: Double?
        var heading: Double?
        var battery: Double?
        var sequence: Int?

        for _ in 0..<mapSize {
            guard offset < data.count else { throw CBORError.unexpectedEnd }

            let key = Int(data[offset])
            offset += 1

            switch key {
            case 0: // timestamp
                let (ts, newOffset) = try decodeUInt64(data, offset)
                timestamp = Date(timeIntervalSince1970: Double(ts) / 1000)
                offset = newOffset
            case 1: // source
                source = data[offset] == 0 ? .watchOS : .iOS
                offset += 1
            case 2: // latitude
                let (val, newOffset) = try decodeDouble(data, offset)
                latitude = val
                offset = newOffset
            case 3: // longitude
                let (val, newOffset) = try decodeDouble(data, offset)
                longitude = val
                offset = newOffset
            case 4: // altitude
                if data[offset] == 0xF6 {
                    offset += 1
                } else {
                    let (val, newOffset) = try decodeDouble(data, offset)
                    altitude = val
                    offset = newOffset
                }
            case 5: // h_accuracy
                let (val, newOffset) = try decodeDouble(data, offset)
                hAccuracy = val
                offset = newOffset
            case 6: // v_accuracy
                let (val, newOffset) = try decodeDouble(data, offset)
                vAccuracy = val
                offset = newOffset
            case 7: // speed
                let (val, newOffset) = try decodeDouble(data, offset)
                speed = val
                offset = newOffset
            case 8: // course
                let (val, newOffset) = try decodeDouble(data, offset)
                course = val
                offset = newOffset
            case 9: // heading
                if data[offset] == 0xF6 {
                    offset += 1
                } else {
                    let (val, newOffset) = try decodeDouble(data, offset)
                    heading = val
                    offset = newOffset
                }
            case 10: // battery
                let (val, newOffset) = try decodeDouble(data, offset)
                battery = val
                offset = newOffset
            case 11: // sequence
                let (val, newOffset) = try decodeUInt64(data, offset)
                sequence = Int(val)
                offset = newOffset
            default:
                throw CBORError.invalidFormat("Unknown key: \(key)")
            }
        }

        guard let ts = timestamp, let src = source,
              let lat = latitude, let lon = longitude,
              let hAcc = hAccuracy, let vAcc = vAccuracy,
              let spd = speed, let crs = course,
              let batt = battery, let seq = sequence else {
            throw CBORError.missingRequiredField
        }

        return LocationFix(
            timestamp: ts,
            source: src,
            coordinate: .init(latitude: lat, longitude: lon),
            altitudeMeters: altitude,
            horizontalAccuracyMeters: hAcc,
            verticalAccuracyMeters: vAcc,
            speedMetersPerSecond: spd,
            courseDegrees: crs,
            headingDegrees: heading,
            batteryFraction: batt,
            sequence: seq
        )
    }

    // MARK: - Private Decoding Helpers

    private func decodeUInt64(_ data: Data, _ offset: Int) throws -> (UInt64, Int) {
        guard offset < data.count else { throw CBORError.unexpectedEnd }

        let initial = data[offset]
        let majorType = initial & 0xE0
        let additionalInfo = initial & 0x1F

        guard majorType == 0x00 else {
            throw CBORError.invalidFormat("Expected unsigned int")
        }

        if additionalInfo <= 23 {
            return (UInt64(additionalInfo), offset + 1)
        } else if additionalInfo == 24 {
            guard offset + 1 < data.count else { throw CBORError.unexpectedEnd }
            return (UInt64(data[offset + 1]), offset + 2)
        } else if additionalInfo == 25 {
            guard offset + 2 < data.count else { throw CBORError.unexpectedEnd }
            let value = UInt16(bigEndian: data.subdata(in: (offset + 1)..<(offset + 3)).withUnsafeBytes { $0.load(as: UInt16.self) })
            return (UInt64(value), offset + 3)
        } else if additionalInfo == 26 {
            guard offset + 4 < data.count else { throw CBORError.unexpectedEnd }
            let value = UInt32(bigEndian: data.subdata(in: (offset + 1)..<(offset + 5)).withUnsafeBytes { $0.load(as: UInt32.self) })
            return (UInt64(value), offset + 5)
        } else if additionalInfo == 27 {
            guard offset + 8 < data.count else { throw CBORError.unexpectedEnd }
            let value = UInt64(bigEndian: data.subdata(in: (offset + 1)..<(offset + 9)).withUnsafeBytes { $0.load(as: UInt64.self) })
            return (value, offset + 9)
        }

        throw CBORError.invalidFormat("Invalid uint encoding")
    }

    private func decodeDouble(_ data: Data, _ offset: Int) throws -> (Double, Int) {
        guard offset < data.count else { throw CBORError.unexpectedEnd }
        guard data[offset] == 0xFB else {
            throw CBORError.invalidFormat("Expected float64")
        }
        guard offset + 8 < data.count else { throw CBORError.unexpectedEnd }

        let bits = UInt64(bigEndian: data.subdata(in: (offset + 1)..<(offset + 9)).withUnsafeBytes { $0.load(as: UInt64.self) })
        return (Double(bitPattern: bits), offset + 9)
    }
}

// MARK: - Errors

public enum CBORError: Error {
    case invalidFormat(String)
    case unexpectedEnd
    case missingRequiredField
}
