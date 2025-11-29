# GPS Data Structures

WebSocket message formats for GPS telemetry between Apple Watch, iPhone, and Jetson Orin.

---

## Overview

The GPS relay system uses JSON over WebSocket (WSS) to transmit location data:

```
Watch GPS → iPhone → wss://ws.stonezone.net → Cloudflare → Orin:8765
```

### Message Types

1. **RelayUpdate** - GPS data from Watch/Phone (Watch → iPhone → Orin)
2. **Ping** - Application-level heartbeat (bidirectional)
3. **Pong** - Heartbeat response (bidirectional)

---

## RelayUpdate Message

Primary message containing GPS data from both the Watch (remote/subject) and iPhone (base/gimbal).

### Format

```json
{
  "remote": {
    "ts_unix_ms": 1732868400000,
    "source": "watchOS",
    "lat": 37.7749,
    "lon": -122.4194,
    "alt_m": 10.5,
    "h_accuracy_m": 5.0,
    "v_accuracy_m": 8.0,
    "speed_mps": 1.2,
    "course_deg": 45.0,
    "heading_deg": 90.0,
    "battery_pct": 0.85,
    "seq": 123
  },
  "base": {
    "ts_unix_ms": 1732868400050,
    "source": "iOS",
    "lat": 37.7748,
    "lon": -122.4195,
    "alt_m": 10.0,
    "h_accuracy_m": 10.0,
    "v_accuracy_m": 15.0,
    "speed_mps": 0.0,
    "course_deg": null,
    "heading_deg": 0.0,
    "battery_pct": 0.92,
    "seq": 456
  },
  "latency": {
    "gpsToRelayMs": 50.0,
    "totalMs": 150.0
  }
}
```

### Field Definitions

#### Remote Object (Watch GPS - Subject)

| Field | Type | Unit | Description | Required |
|-------|------|------|-------------|----------|
| `ts_unix_ms` | Integer | Milliseconds | Unix timestamp when GPS fix obtained | Yes |
| `source` | String | - | Device type ("watchOS", "iOS") | Yes |
| `lat` | Float | Degrees | Latitude (-90 to +90) | Yes |
| `lon` | Float | Degrees | Longitude (-180 to +180) | Yes |
| `alt_m` | Float | Meters | Altitude above sea level | No |
| `h_accuracy_m` | Float | Meters | Horizontal accuracy radius | Yes |
| `v_accuracy_m` | Float | Meters | Vertical accuracy | No |
| `speed_mps` | Float | m/s | Speed over ground | No |
| `course_deg` | Float | Degrees | Direction of travel (0-360, 0=North) | No |
| `heading_deg` | Float | Degrees | Device heading (0-360, 0=North) | No |
| `battery_pct` | Float | 0.0-1.0 | Battery level (0.85 = 85%) | No |
| `seq` | Integer | - | Monotonic sequence number | Yes |

**Notes:**
- `heading_deg` on Watch may be unreliable (magnetometer accuracy varies)
- `course_deg` is only valid when `speed_mps > ~0.5 m/s`
- `null` values indicate data not available

#### Base Object (iPhone GPS - Gimbal Reference)

Same structure as `remote`, but typically from iPhone near/mounted on gimbal.

**Key Differences:**
- `source` will be "iOS"
- `heading_deg` from iPhone compass is more reliable (used for gimbal orientation)
- `speed_mps` typically 0.0 (gimbal is stationary)

#### Latency Object

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| `gpsToRelayMs` | Float | ms | Time from GPS fix to relay transmission |
| `totalMs` | Float | ms | Estimated total latency (GPS → Orin) |

**Purpose:** Performance monitoring and fusion weighting

---

## Ping/Pong Heartbeat

Application-level keepalive to detect dead connections (separate from WebSocket protocol pings).

### Ping Message (Client → Server OR Server → Client)

```json
{
  "type": "ping",
  "id": "abc123",
  "ts": 1732868400.123
}
```

| Field | Type | Description |
|-------|------|-------------|
| `type` | String | Always "ping" |
| `id` | String | Unique identifier for matching response |
| `ts` | Float | Unix timestamp in seconds (with fractional) |

### Pong Message (Response)

```json
{
  "type": "pong",
  "id": "abc123"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `type` | String | Always "pong" |
| `id` | String | Must match the `id` from ping |

**Timing:**
- iPhone sends ping every 5 seconds
- Expects pong within 10 seconds
- Disconnects if no pong received within 15 seconds

**CRITICAL:** Orin `gps_server.py` MUST respond to pings or connection will drop!

---

## Message Examples

### Typical Watch Update (Subject Walking)

```json
{
  "remote": {
    "ts_unix_ms": 1732900000000,
    "source": "watchOS",
    "lat": 37.774929,
    "lon": -122.419416,
    "alt_m": 15.3,
    "h_accuracy_m": 8.0,
    "v_accuracy_m": 12.0,
    "speed_mps": 1.5,
    "course_deg": 135.0,
    "heading_deg": 140.0,
    "battery_pct": 0.78,
    "seq": 42
  },
  "base": {
    "ts_unix_ms": 1732900000100,
    "source": "iOS",
    "lat": 37.774800,
    "lon": -122.419300,
    "alt_m": 12.0,
    "h_accuracy_m": 5.0,
    "v_accuracy_m": 10.0,
    "speed_mps": 0.0,
    "course_deg": null,
    "heading_deg": 90.0,
    "battery_pct": 0.95,
    "seq": 78
  },
  "latency": {
    "gpsToRelayMs": 100.0,
    "totalMs": 180.0
  }
}
```

### Watch on LTE (Subject Far from Gimbal)

```json
{
  "remote": {
    "ts_unix_ms": 1732900060000,
    "source": "watchOS",
    "lat": 37.780000,
    "lon": -122.425000,
    "alt_m": 20.0,
    "h_accuracy_m": 15.0,
    "v_accuracy_m": 25.0,
    "speed_mps": 3.2,
    "course_deg": 270.0,
    "heading_deg": 275.0,
    "battery_pct": 0.65,
    "seq": 120
  },
  "base": {
    "ts_unix_ms": 1732900060200,
    "source": "iOS",
    "lat": 37.774800,
    "lon": -122.419300,
    "alt_m": 12.0,
    "h_accuracy_m": 5.0,
    "v_accuracy_m": 10.0,
    "speed_mps": 0.0,
    "course_deg": null,
    "heading_deg": 90.0,
    "battery_pct": 0.94,
    "seq": 156
  },
  "latency": {
    "gpsToRelayMs": 200.0,
    "totalMs": 350.0
  }
}
```

**Note:** Higher latency, lower accuracy on cellular connection.

---

## Coordinate Systems

### Geographic Coordinates (WGS84)

- **Latitude:** -90° (South Pole) to +90° (North Pole)
- **Longitude:** -180° (West) to +180° (East)
- **Altitude:** Meters above mean sea level (can be negative)

### Heading/Course (True North Reference)

- **0°:** True North
- **90°:** East
- **180°:** South
- **270°:** West
- **Clockwise:** Increasing angle

### Conversion to Local Coordinates

The Orin fusion engine converts WGS84 to local East-North-Up (ENU) frame:

```python
# Pseudo-code
base_lat, base_lon = base['lat'], base['lon']
remote_lat, remote_lon = remote['lat'], remote['lon']

# Haversine distance
distance_m = haversine(base_lat, base_lon, remote_lat, remote_lon)

# Bearing from base to remote
bearing_deg = calculate_bearing(base_lat, base_lon, remote_lat, remote_lon)

# Convert to gimbal pan angle (accounting for base heading)
pan_angle = bearing_deg - base['heading_deg']

# Elevation angle
alt_diff = remote['alt_m'] - base['alt_m']
tilt_angle = atan2(alt_diff, distance_m) * 180 / π
```

See `orin/gps_fusion/geo_calc.py` for implementation.

---

## Update Frequency

### Expected Rates

| Device | Typical Rate | Notes |
|--------|--------------|-------|
| Watch GPS | 1 Hz (1/sec) | Standard Core Location rate |
| iPhone GPS | 1 Hz | Same as Watch |
| WebSocket Relay | 1 Hz | Matches GPS rate |

### Burst Handling

- iPhone buffers up to 10 updates if connection temporarily drops
- Sequence numbers allow detecting missed messages
- Old data (>2 seconds) should be deprioritized in fusion

---

## Error Conditions

### Missing Fields

If optional fields are absent:

```python
course_deg = data['remote'].get('course_deg')
if course_deg is None:
    # Cannot use course for prediction, fall back to position-only
    ...
```

### Accuracy Thresholds

Recommended filtering:

```python
if data['remote']['h_accuracy_m'] > 50.0:
    # GPS quality poor, weight vision higher in fusion
    ...
```

### Sequence Gaps

```python
last_seq = state['last_remote_seq']
current_seq = data['remote']['seq']

if current_seq != last_seq + 1:
    missed_count = current_seq - last_seq - 1
    logger.warning(f"Missed {missed_count} GPS updates")
```

---

## Python Parsing Example

```python
import json

def parse_relay_update(ws_message: str) -> dict:
    """Parse RelayUpdate JSON message."""
    data = json.loads(ws_message)

    # Validate structure
    assert 'remote' in data and 'base' in data
    assert 'lat' in data['remote'] and 'lon' in data['remote']

    return data

def handle_message(ws_message: str):
    """Dispatch message by type."""
    data = json.loads(ws_message)

    msg_type = data.get('type')

    if msg_type == 'ping':
        # Respond with pong
        pong = json.dumps({'type': 'pong', 'id': data['id']})
        return pong

    elif msg_type == 'pong':
        # Log heartbeat response
        print(f"Pong received for {data['id']}")
        return None

    else:
        # Assume RelayUpdate
        relay_data = parse_relay_update(ws_message)
        # Process GPS data...
        return None
```

---

## Swift Encoding Example

```swift
struct RelayUpdate: Codable {
    let remote: GPSSnapshot
    let base: GPSSnapshot
    let latency: LatencyInfo
}

struct GPSSnapshot: Codable {
    let ts_unix_ms: Int64
    let source: String
    let lat: Double
    let lon: Double
    let alt_m: Double?
    let h_accuracy_m: Double
    let v_accuracy_m: Double?
    let speed_mps: Double?
    let course_deg: Double?
    let heading_deg: Double?
    let battery_pct: Double?
    let seq: Int
}

struct LatencyInfo: Codable {
    let gpsToRelayMs: Double
    let totalMs: Double
}

// Encode and send
let update = RelayUpdate(remote: watchGPS, base: phoneGPS, latency: timing)
let json = try JSONEncoder().encode(update)
websocket.send(json)
```

---

## WebSocket Connection Details

### Endpoint

- **Production:** `wss://ws.stonezone.net`
- **Local Testing:** `ws://192.168.1.155:8765`

### Headers

```
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Key: [generated]
Sec-WebSocket-Version: 13
```

### Subprotocols

None (plain JSON messages)

### Reconnection

- iPhone app auto-reconnects on disconnect
- Exponential backoff: 1s, 2s, 4s, 8s (max)
- Resets sequence numbers on reconnect

---

## Security Considerations

### Data Privacy

- GPS coordinates are **not encrypted** beyond WSS transport layer
- Consider implications of exposing subject location
- Cloudflare Tunnel provides HTTPS/WSS encryption in transit

### Authentication

- Currently **no authentication** on WebSocket endpoint
- Future: Add token-based auth or client certificates
- Tunnel is public but service is not advertised

### Rate Limiting

- No rate limiting implemented
- Could add max messages/sec to prevent abuse
- Cloudflare provides DDoS protection at edge

---

**Protocol Version:** 1.0
**Last Updated:** November 28, 2025
**Compatible Orin Server:** gps_server.py v1.0+
**Compatible iOS App:** gps-relay-framework v1.0+
