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
import websockets
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Callable, List
from enum import Enum

# Configuration
HOST = "0.0.0.0"
PORT = 8765

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [GPS-SERVER] - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


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

    async def process_message(self, websocket, message: str):
        """Decode and process incoming messages from Swift app.

        Handles two message types:
        1. Application-level heartbeats: {"type": "ping", "id": "xxx"}
        2. GPS RelayUpdates: {"base": {...}, "remote": {...}, ...}
        """
        try:
            data = json.loads(message)

            # Handle application-level heartbeat (Swift sends these every 10s)
            # CRITICAL: Without this, Swift will disconnect after 15 seconds!
            if data.get('type') == 'ping':
                pong = {'type': 'pong', 'id': data.get('id')}
                await websocket.send(json.dumps(pong))
                logger.debug(f"Heartbeat pong sent (id: {data.get('id')})")
                return

            update = RelayUpdate.from_dict(data)
            self.fixes_received += 1

            # Process Watch location (our tracking target)
            if update.remote:
                self.last_watch_fix = update.remote
                self._handle_watch_fix(update.remote)

            # Store iPhone location for reference
            if update.base:
                self.last_iphone_fix = update.base

        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON received: {message[:100]}...")
        except KeyError as e:
            logger.warning(f"Missing key in payload: {e}")
        except Exception as e:
            logger.error(f"Processing error: {e}")

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

    def get_stats(self) -> dict:
        """Return server statistics."""
        uptime = time.time() - self.start_time
        rate = self.fixes_received / uptime if uptime > 0 else 0

        return {
            'uptime_sec': int(uptime),
            'fixes_received': self.fixes_received,
            'fix_rate_hz': round(rate, 2),
            'connected_clients': len(self.connected_clients),
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
                        f"Fixes: {stats['fixes_received']} | "
                        f"Rate: {stats['fix_rate_hz']} Hz | "
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
