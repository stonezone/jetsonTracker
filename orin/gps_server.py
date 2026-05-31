#!/usr/bin/env python3
"""
GPS WebSocket Server for Robot Cameraman

Receives GPS fixes from iPhone/Watch via Cloudflare Tunnel.
Matches the Swift RelayUpdate/LocationFix data structures exactly.

Usage:
    python3 gps_server.py

The server listens on 0.0.0.0:8765 and accepts WebSocket connections
from the Cloudflare Tunnel (ws.stonezone.net).
"""

import asyncio
import os
import websockets
import json
import logging
import time
import struct
from dataclasses import dataclass, field
from typing import Optional, Callable, List
from enum import Enum

# Configuration (env-overridable for tests / non-standard ports)
HOST = os.environ.get("GPS_HOST", "0.0.0.0")
PORT = int(os.environ.get("GPS_PORT", "8765"))

# Reliability tuning.
# Stale GPS is worse than missing GPS: a late fix yanks the camera backward.
MAX_FIX_AGE_MS = int(os.environ.get("GPS_MAX_FIX_AGE_MS", "3000"))
# An incoming seq this far below the last accepted seq is treated as a device
# restart (accept + re-baseline) rather than an out-of-order straggler.
SEQ_RESET_THRESHOLD = int(os.environ.get("GPS_SEQ_RESET_THRESHOLD", "100"))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [GPS-SERVER] - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


class CBORDecodeError(ValueError):
    """Raised when Swift GPS CBOR payloads cannot be decoded."""


class _GPSCBORReader:
    """Minimal CBOR reader for LocationCore.GPSCBOREncoder payloads."""

    def __init__(self, payload: bytes):
        self.payload = payload
        self.offset = 0

    def _read_byte(self) -> int:
        if self.offset >= len(self.payload):
            raise CBORDecodeError("Unexpected end of CBOR payload")
        value = self.payload[self.offset]
        self.offset += 1
        return value

    def _read_uint_payload(self, additional_info: int) -> int:
        if additional_info <= 23:
            return additional_info
        if additional_info == 24:
            return self._read_byte()
        if additional_info == 25:
            return int.from_bytes(self._read_exact(2), "big")
        if additional_info == 26:
            return int.from_bytes(self._read_exact(4), "big")
        if additional_info == 27:
            return int.from_bytes(self._read_exact(8), "big")
        raise CBORDecodeError(f"Unsupported uint additional info: {additional_info}")

    def _read_exact(self, count: int) -> bytes:
        end = self.offset + count
        if end > len(self.payload):
            raise CBORDecodeError("Unexpected end of CBOR payload")
        data = self.payload[self.offset:end]
        self.offset = end
        return data

    def peek(self) -> int:
        if self.offset >= len(self.payload):
            raise CBORDecodeError("Unexpected end of CBOR payload")
        return self.payload[self.offset]

    def read_uint(self) -> int:
        initial = self._read_byte()
        major_type = initial & 0xE0
        additional_info = initial & 0x1F
        if major_type != 0x00:
            raise CBORDecodeError(f"Expected unsigned int, got 0x{initial:02x}")
        return self._read_uint_payload(additional_info)

    def read_map_length(self) -> int:
        initial = self._read_byte()
        major_type = initial & 0xE0
        additional_info = initial & 0x1F
        if major_type != 0xA0:
            raise CBORDecodeError(f"Expected map, got 0x{initial:02x}")
        return self._read_uint_payload(additional_info)

    def read_double(self) -> float:
        initial = self._read_byte()
        if initial != 0xFB:
            raise CBORDecodeError(f"Expected float64, got 0x{initial:02x}")
        return struct.unpack(">d", self._read_exact(8))[0]

    def read_optional_double(self) -> Optional[float]:
        if self.peek() == 0xF6:
            self.offset += 1
            return None
        return self.read_double()

    def skip_value(self):
        initial = self._read_byte()
        major_type = initial & 0xE0
        additional_info = initial & 0x1F

        if major_type == 0x00:
            self._read_uint_payload(additional_info)
        elif major_type == 0x60:
            self._read_exact(self._read_uint_payload(additional_info))
        elif major_type == 0xA0:
            count = self._read_uint_payload(additional_info)
            for _ in range(count):
                self.skip_value()
                self.skip_value()
        elif initial == 0xF6:
            return
        elif initial == 0xFB:
            self._read_exact(8)
        else:
            raise CBORDecodeError(f"Unsupported CBOR value 0x{initial:02x}")


def _decode_cbor_location_fix(reader: _GPSCBORReader) -> dict:
    fields = {}
    for _ in range(reader.read_map_length()):
        key = reader.read_uint()
        if key == 0:
            fields["ts_unix_ms"] = reader.read_uint()
        elif key == 1:
            fields["source"] = "watchOS" if reader.read_uint() == 0 else "iOS"
        elif key == 2:
            fields["lat"] = reader.read_double()
        elif key == 3:
            fields["lon"] = reader.read_double()
        elif key == 4:
            fields["alt_m"] = reader.read_optional_double()
        elif key == 5:
            fields["h_accuracy_m"] = reader.read_double()
        elif key == 6:
            fields["v_accuracy_m"] = reader.read_double()
        elif key == 7:
            fields["speed_mps"] = reader.read_double()
        elif key == 8:
            fields["course_deg"] = reader.read_double()
        elif key == 9:
            fields["heading_deg"] = reader.read_optional_double()
        elif key == 10:
            fields["battery_pct"] = reader.read_double()
        elif key == 11:
            fields["seq"] = reader.read_uint()
        else:
            reader.skip_value()
    return fields


def _decode_cbor_latency(reader: _GPSCBORReader) -> dict:
    field_names = {
        0: "gpsToRelayMs",
        1: "relayToTransportMs",
        2: "transportRttMs",
        3: "totalMs",
    }
    fields = {}
    for _ in range(reader.read_map_length()):
        key = reader.read_uint()
        name = field_names.get(key)
        value = reader.read_optional_double()
        if name:
            fields[name] = value
    return fields


def decode_cbor_relay_update(payload: bytes) -> dict:
    """Decode Swift LocationCore.GPSCBOREncoder RelayUpdate payloads."""
    reader = _GPSCBORReader(payload)
    update = {}
    for _ in range(reader.read_map_length()):
        key = reader.read_uint()
        if key == 0:
            update["base"] = _decode_cbor_location_fix(reader)
        elif key == 1:
            update["remote"] = _decode_cbor_location_fix(reader)
        elif key == 2:
            update["fused"] = _decode_cbor_location_fix(reader)
        elif key == 3:
            update["latency"] = _decode_cbor_latency(reader)
        else:
            reader.skip_value()
    return update


class LocationSource(Enum):
    WATCH = "watchOS"
    IOS = "iOS"


@dataclass
class LocationFix:
    """Matches Swift LocationFix structure exactly."""
    lat: float
    lon: float
    timestamp_ms: int
    source: LocationSource
    horizontal_accuracy: float
    vertical_accuracy: float
    speed_mps: float
    course_deg: float
    battery_pct: float
    sequence: int
    altitude_m: Optional[float] = None
    heading_deg: Optional[float] = None

    @classmethod
    def from_dict(cls, data: dict) -> 'LocationFix':
        """Parse from Swift JSON format."""
        source_str = data.get('source', 'watchOS')
        source = LocationSource.WATCH if source_str == 'watchOS' else LocationSource.IOS

        return cls(
            lat=data.get('lat', 0.0),
            lon=data.get('lon', 0.0),
            timestamp_ms=data.get('ts_unix_ms', 0),
            source=source,
            horizontal_accuracy=data.get('h_accuracy_m', 0.0),
            vertical_accuracy=data.get('v_accuracy_m', 0.0),
            speed_mps=data.get('speed_mps', 0.0),
            course_deg=data.get('course_deg', 0.0),
            battery_pct=data.get('battery_pct', 0.0),
            sequence=data.get('seq', 0),
            altitude_m=data.get('alt_m'),
            heading_deg=data.get('heading_deg')
        )

    def age_ms(self) -> int:
        """Calculate age of this fix in milliseconds."""
        now_ms = int(time.time() * 1000)
        return now_ms - self.timestamp_ms


@dataclass
class LatencyInfo:
    """Matches Swift LatencyInfo structure."""
    gps_to_relay_ms: Optional[float] = None
    relay_to_transport_ms: Optional[float] = None
    transport_rtt_ms: Optional[float] = None
    total_ms: Optional[float] = None

    @classmethod
    def from_dict(cls, data: dict) -> 'LatencyInfo':
        return cls(
            gps_to_relay_ms=data.get('gpsToRelayMs'),
            relay_to_transport_ms=data.get('relayToTransportMs'),
            transport_rtt_ms=data.get('transportRttMs'),
            total_ms=data.get('totalMs')
        )


@dataclass
class RelayUpdate:
    """Matches Swift RelayUpdate structure - the main payload from iPhone."""
    base: Optional[LocationFix] = None      # iPhone location
    remote: Optional[LocationFix] = None    # Watch location (tracking target)
    fused: Optional[LocationFix] = None     # Fused location
    latency: Optional[LatencyInfo] = None
    relay_timestamp_ms: Optional[int] = None

    @classmethod
    def from_dict(cls, data: dict) -> 'RelayUpdate':
        base = LocationFix.from_dict(data['base']) if data.get('base') else None
        remote = LocationFix.from_dict(data['remote']) if data.get('remote') else None
        fused = LocationFix.from_dict(data['fused']) if data.get('fused') else None
        latency = LatencyInfo.from_dict(data['latency']) if data.get('latency') else None

        return cls(
            base=base,
            remote=remote,
            fused=fused,
            latency=latency,
            relay_timestamp_ms=data.get('relayTimestamp')
        )


# Type alias for fix callbacks
FixCallback = Callable[[LocationFix], None]


class RobotGpsServer:
    """
    WebSocket server that receives GPS updates from iPhone via Cloudflare Tunnel.

    The iPhone app sends RelayUpdate objects containing:
    - base: iPhone's own location
    - remote: Watch's location (the subject we're tracking)
    - fused: Combined/filtered location

    For robot tracking, we primarily care about 'remote' (the Watch location).
    """

    def __init__(self):
        self.connected_clients: set = set()
        self.fix_callbacks: List[FixCallback] = []
        self.last_watch_fix: Optional[LocationFix] = None
        self.last_iphone_fix: Optional[LocationFix] = None
        self.fixes_received: int = 0
        self.start_time: float = time.time()
        # Per-source last accepted sequence (out-of-order rejection).
        self._last_seq: dict = {}
        # Per-source best-case (recv - fix_ts) offset, to make staleness robust
        # to clock skew between the Orin and the Watch/iPhone.
        self._min_offset: dict = {}
        # Per-source accepted counts + reliability counters.
        self.fixes_by_source: dict = {
            LocationSource.WATCH.value: 0,
            LocationSource.IOS.value: 0,
        }
        self.dropped_stale: int = 0
        self.dropped_out_of_order: int = 0

    def on_watch_fix(self, callback: FixCallback):
        """Register a callback for when new Watch GPS fixes arrive."""
        self.fix_callbacks.append(callback)

    async def handle_client(self, websocket):
        """Handle a single inbound WebSocket connection."""
        client_id = id(websocket)
        remote = websocket.remote_address
        self.connected_clients.add(websocket)
        logger.info(f"Client connected: {remote} (ID: {client_id})")

        try:
            async for message in websocket:
                await self.process_message(websocket, message)
        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"Client disconnected: {remote} (code: {e.code})")
        except Exception as e:
            logger.error(f"Error handling client {remote}: {e}")
        finally:
            self.connected_clients.discard(websocket)

    @staticmethod
    def _decode_websocket_message(message) -> tuple[dict, str]:
        """Return decoded payload plus JSON text for rebroadcast.

        Swift clients send text JSON, binary UTF-8 JSON, or compact CBOR
        depending on transport configuration. Local tracker consumers expect
        JSON, so CBOR is normalized back to JSON after decoding.
        """
        if isinstance(message, str):
            return json.loads(message), message
        if isinstance(message, (bytes, bytearray, memoryview)):
            payload = bytes(message)
            if payload and (payload[0] & 0xE0) == 0xA0:
                data = decode_cbor_relay_update(payload)
                return data, json.dumps(data, separators=(',', ':'))
            text = payload.decode('utf-8')
            return json.loads(text), text
        raise TypeError(f"Unsupported websocket message type: {type(message)!r}")

    async def process_message(self, websocket, message):
        """Decode and process incoming messages from Swift app.

        Handles two message types:
        1. Application-level heartbeats: {"type": "ping", "id": "xxx"}
        2. GPS RelayUpdates: {"base": {...}, "remote": {...}, ...}
        """
        try:
            data, message_text = self._decode_websocket_message(message)

            # Handle application-level heartbeat (Swift sends these every 10s)
            # CRITICAL: Without this, Swift will disconnect after 15 seconds!
            if data.get('type') == 'ping':
                pong = {'type': 'pong', 'id': data.get('id')}
                await websocket.send(json.dumps(pong))
                logger.debug(f"Heartbeat pong sent (id: {data.get('id')})")
                return

            update = RelayUpdate.from_dict(data)
            self.fixes_received += 1

            accepted_remote = False
            accepted_base = False

            # Process Watch location (our tracking target)
            if update.remote and self._should_accept(update.remote):
                accepted_remote = True
                self.last_watch_fix = update.remote
                self.fixes_by_source[update.remote.source.value] += 1
                self._handle_watch_fix(update.remote)

            # Store iPhone location (camera / base-station position)
            if update.base and self._should_accept(update.base):
                accepted_base = True
                self.last_iphone_fix = update.base
                self.fixes_by_source[update.base.source.value] += 1

            # Ack receipt so the Watch UI can show connectivity / last-ack age.
            # Swift WatchDirectTransport.handleAck reads {"type":"ack","seq":<int>}.
            acked_fix = update.remote or update.base
            if acked_fix is not None:
                await self._safe_ack(websocket, acked_fix,
                                     accepted_remote or accepted_base)

            # Broadcast only accepted fixes to local clients (FusionEngine/pointing).
            # Consumers also guard against stale/out-of-order independently.
            if accepted_remote or accepted_base:
                await self._broadcast_to_local_clients(message_text, websocket)

        except UnicodeDecodeError as e:
            logger.warning(f"Invalid UTF-8 websocket payload: {e}")
        except CBORDecodeError as e:
            logger.warning(f"Invalid CBOR websocket payload: {e}")
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON received: {message[:100]}...")
        except KeyError as e:
            logger.warning(f"Missing key in payload: {e}")
        except Exception as e:
            logger.error(f"Processing error: {e}")

    def _should_accept(self, fix: LocationFix) -> bool:
        """Reject stale and out-of-order fixes before they reach trackers.

        Staleness is measured relative to the best-case (recv - fix_ts) offset
        seen for this source, so a constant clock skew between the Orin and the
        Watch/iPhone does not cause false drops; only anomalously delayed fixes
        are rejected. Out-of-order detection uses the monotonic sequence number
        and is clock-independent.
        """
        source = fix.source.value
        recv_ms = int(time.time() * 1000)

        # Clock-skew-robust staleness.
        raw_offset = recv_ms - fix.timestamp_ms
        base_offset = self._min_offset.get(source)
        if base_offset is None or raw_offset < base_offset:
            self._min_offset[source] = raw_offset
            base_offset = raw_offset
        effective_age = raw_offset - base_offset
        if effective_age > MAX_FIX_AGE_MS:
            self.dropped_stale += 1
            logger.warning(
                f"DROP stale {source} seq={fix.sequence} age~{effective_age}ms"
            )
            return False

        # Out-of-order (clock-independent).
        last = self._last_seq.get(source)
        if last is not None and fix.sequence <= last:
            if last - fix.sequence < SEQ_RESET_THRESHOLD:
                self.dropped_out_of_order += 1
                logger.warning(
                    f"DROP out-of-order {source} seq={fix.sequence} (last={last})"
                )
                return False
            logger.info(f"{source} sequence reset ({last} -> {fix.sequence})")

        self._last_seq[source] = fix.sequence
        return True

    async def _safe_ack(self, websocket, fix: LocationFix, accepted: bool) -> None:
        """Send a per-fix ack to the sender. Never raises into the read loop."""
        ack = {
            "type": "ack",
            "seq": fix.sequence,
            "source": fix.source.value,
            "received_ts": int(time.time() * 1000),
            "accepted": accepted,
        }
        try:
            await websocket.send(json.dumps(ack))
        except Exception as e:
            logger.debug(f"Ack send failed: {e}")

    def _handle_watch_fix(self, fix: LocationFix):
        """Process a new Watch GPS fix."""
        latency = fix.age_ms()

        # Log the fix with latency info
        logger.info(
            f"WATCH | Lat: {fix.lat:.6f}, Lon: {fix.lon:.6f} | "
            f"Spd: {fix.speed_mps:.1f}m/s | Course: {fix.course_deg:.0f} | "
            f"Acc: {fix.horizontal_accuracy:.1f}m | Latency: {latency}ms | "
            f"Seq: {fix.sequence}"
        )

        # Notify all registered callbacks
        for callback in self.fix_callbacks:
            try:
                callback(fix)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    async def _broadcast_to_local_clients(self, message: str, sender_ws):
        """Relay GPS update to all other connected clients.

        This enables local processes (like VisionTracker/FusionEngine) to receive
        GPS data by connecting as WebSocket clients to this server.

        Data flow: iPhone -> Cloudflare -> gps_server -> local clients -> FusionEngine
        """
        # Get all clients except the sender (don't echo back to iPhone)
        receivers = [ws for ws in self.connected_clients if ws != sender_ws]
        if not receivers:
            return

        # Broadcast to all local listeners
        send_tasks = [ws.send(message) for ws in receivers]
        results = await asyncio.gather(*send_tasks, return_exceptions=True)

        # Log any broadcast failures (but don't crash)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(f"Broadcast to client failed: {result}")

    def get_stats(self) -> dict:
        """Return server statistics."""
        uptime = time.time() - self.start_time
        rate = self.fixes_received / uptime if uptime > 0 else 0

        return {
            'uptime_sec': int(uptime),
            'fixes_received': self.fixes_received,
            'fix_rate_hz': round(rate, 2),
            'fixes_by_source': dict(self.fixes_by_source),
            'dropped_stale': self.dropped_stale,
            'dropped_out_of_order': self.dropped_out_of_order,
            'connected_clients': len(self.connected_clients),
            'last_watch_age_ms': self.last_watch_fix.age_ms() if self.last_watch_fix else None,
            'last_iphone_age_ms': self.last_iphone_fix.age_ms() if self.last_iphone_fix else None,
            'last_watch_fix': self.last_watch_fix,
            'last_iphone_fix': self.last_iphone_fix
        }

    async def start(self):
        """Start the WebSocket server."""
        logger.info(f"Starting GPS Server on {HOST}:{PORT}")
        logger.info("Waiting for iPhone connection via Cloudflare Tunnel...")

        async with websockets.serve(
            self.handle_client,
            HOST,
            PORT,
            ping_interval=20,
            ping_timeout=20
        ):
            # Print stats periodically
            while True:
                await asyncio.sleep(30)
                stats = self.get_stats()
                if stats['fixes_received'] > 0:
                    logger.info(
                        f"Stats | Uptime: {stats['uptime_sec']}s | "
                        f"Fixes: {stats['fixes_received']} "
                        f"(watch={stats['fixes_by_source'].get('watchOS', 0)} "
                        f"iphone={stats['fixes_by_source'].get('iOS', 0)}) | "
                        f"Rate: {stats['fix_rate_hz']} Hz | "
                        f"Dropped: stale={stats['dropped_stale']} "
                        f"ooo={stats['dropped_out_of_order']} | "
                        f"Clients: {stats['connected_clients']}"
                    )


# Global server instance for integration
_server: Optional[RobotGpsServer] = None


def get_server() -> RobotGpsServer:
    """Get the global server instance."""
    global _server
    if _server is None:
        _server = RobotGpsServer()
    return _server


async def main():
    """Main entry point."""
    server = get_server()

    # Example: Register a callback for testing
    def print_fix(fix: LocationFix):
        pass  # Already logged in _handle_watch_fix

    server.on_watch_fix(print_fix)

    await server.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
