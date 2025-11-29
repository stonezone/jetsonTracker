"""WebSocket client for receiving GPS fixes from iPhone app."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Dict, Any
from threading import Thread, Lock

try:
    import websockets
except ImportError:
    websockets = None
    print('Warning: websockets not installed. Run: pip3 install websockets')

from .geo_calc import GeoPoint

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('gps_client')


@dataclass
class GPSState:
    """Current GPS state for both streams."""
    gimbal: Optional[GeoPoint] = None  # Phone/base station GPS
    target: Optional[GeoPoint] = None  # Watch/subject GPS
    gimbal_updated: float = 0.0
    target_updated: float = 0.0
    connected: bool = False
    fixes_received: Dict[str, int] = field(default_factory=lambda: {'iOS': 0, 'watchOS': 0})


def parse_location_fix(data: Dict[str, Any]) -> Optional[GeoPoint]:
    """Parse a location fix JSON into a GeoPoint."""
    try:
        return GeoPoint(
            lat=data['lat'],
            lon=data['lon'],
            alt=data.get('alt_m'),
            heading=data.get('heading_deg'),
            speed=data.get('speed_mps', 0),
            course=data.get('course_deg', 0),
            timestamp=data.get('ts_unix_ms', 0) / 1000.0,
            accuracy=data.get('h_accuracy_m', 0)
        )
    except (KeyError, TypeError) as e:
        logger.warning(f'Failed to parse fix: {e}')
        return None


class GPSClient:
    """Async WebSocket client for GPS fixes."""

    def __init__(self, uri: str = 'ws://localhost:8765',
                 on_update: Optional[Callable[[GPSState], None]] = None):
        self.uri = uri
        self.on_update = on_update
        self.state = GPSState()
        self._lock = Lock()
        self._running = False
        self._thread: Optional[Thread] = None
        self._ws = None

    def get_state(self) -> GPSState:
        """Get current GPS state (thread-safe)."""
        with self._lock:
            return GPSState(
                gimbal=self.state.gimbal,
                target=self.state.target,
                gimbal_updated=self.state.gimbal_updated,
                target_updated=self.state.target_updated,
                connected=self.state.connected,
                fixes_received=self.state.fixes_received.copy()
            )

    def _handle_fix(self, data: Dict[str, Any]) -> None:
        """Process incoming GPS fix."""
        source = data.get('source', 'unknown')
        point = parse_location_fix(data)

        if point is None:
            return

        now = time.time()

        with self._lock:
            if source == 'iOS':
                self.state.gimbal = point
                self.state.gimbal_updated = now
                self.state.fixes_received['iOS'] += 1
            elif source == 'watchOS':
                self.state.target = point
                self.state.target_updated = now
                self.state.fixes_received['watchOS'] += 1

        if self.on_update:
            self.on_update(self.get_state())

        logger.debug(f'[{source}] lat={point.lat:.6f} lon={point.lon:.6f} acc={point.accuracy}m')

    async def _connect_loop(self) -> None:
        """Main connection loop with reconnection."""
        while self._running:
            try:
                logger.info(f'Connecting to {self.uri}...')
                async with websockets.connect(self.uri) as ws:
                    self._ws = ws
                    with self._lock:
                        self.state.connected = True
                    logger.info('Connected to GPS server')

                    async for message in ws:
                        if not self._running:
                            break
                        try:
                            data = json.loads(message)
                            self._handle_fix(data)
                        except json.JSONDecodeError as e:
                            logger.warning(f'Invalid JSON: {e}')

            except Exception as e:
                logger.warning(f'Connection error: {e}')

            with self._lock:
                self.state.connected = False
                self._ws = None

            if self._running:
                logger.info('Reconnecting in 5 seconds...')
                await asyncio.sleep(5)

    def _run_async(self) -> None:
        """Run the async event loop in a thread."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._connect_loop())

    def start(self) -> None:
        """Start the GPS client in a background thread."""
        if self._running:
            return

        if websockets is None:
            logger.error('websockets module not available')
            return

        self._running = True
        self._thread = Thread(target=self._run_async, daemon=True)
        self._thread.start()
        logger.info('GPS client started')

    def stop(self) -> None:
        """Stop the GPS client."""
        self._running = False
        # Websocket will close when thread exits
        if self._thread:
            self._thread.join(timeout=2.0)
        logger.info('GPS client stopped')

    def is_gimbal_fresh(self, max_age: float = 5.0) -> bool:
        """Check if gimbal GPS is recent enough."""
        with self._lock:
            return (time.time() - self.state.gimbal_updated) < max_age

    def is_target_fresh(self, max_age: float = 5.0) -> bool:
        """Check if target GPS is recent enough."""
        with self._lock:
            return (time.time() - self.state.target_updated) < max_age


class MockGPSServer:
    """Mock GPS server for testing without iPhone."""

    def __init__(self, host: str = '0.0.0.0', port: int = 8765):
        self.host = host
        self.port = port
        self._running = False

    async def _handler(self, ws):
        """Send mock GPS fixes."""
        logger.info(f'Mock client connected')
        seq_ios = 0
        seq_watch = 0

        # Starting positions (Honolulu)
        gimbal_lat, gimbal_lon = 21.3069, -157.8583
        target_lat, target_lon = 21.3079, -157.8573

        try:
            while self._running:
                now = int(time.time() * 1000)

                # Send gimbal (iPhone) fix
                ios_fix = {
                    'ts_unix_ms': now,
                    'source': 'iOS',
                    'lat': gimbal_lat,
                    'lon': gimbal_lon,
                    'alt_m': 10.0,
                    'h_accuracy_m': 5.0,
                    'v_accuracy_m': 8.0,
                    'speed_mps': 0.0,
                    'course_deg': 0.0,
                    'heading_deg': 45.0,
                    'battery_pct': 0.85,
                    'seq': seq_ios
                }
                await ws.send(json.dumps(ios_fix))
                seq_ios += 1

                await asyncio.sleep(0.25)

                # Send target (Watch) fix - moving
                target_lon += 0.00001  # Move east
                watch_fix = {
                    'ts_unix_ms': int(time.time() * 1000),
                    'source': 'watchOS',
                    'lat': target_lat,
                    'lon': target_lon,
                    'alt_m': 12.0,
                    'h_accuracy_m': 8.0,
                    'v_accuracy_m': 12.0,
                    'speed_mps': 2.0,
                    'course_deg': 90.0,
                    'battery_pct': 0.72,
                    'seq': seq_watch
                }
                await ws.send(json.dumps(watch_fix))
                seq_watch += 1

                await asyncio.sleep(0.25)

        except websockets.ConnectionClosed:
            logger.info('Mock client disconnected')

    async def _serve(self):
        """Run the mock server."""
        async with websockets.serve(self._handler, self.host, self.port):
            logger.info(f'Mock GPS server running on ws://{self.host}:{self.port}')
            while self._running:
                await asyncio.sleep(1)

    def start(self):
        """Start mock server in background."""
        self._running = True
        Thread(target=lambda: asyncio.run(self._serve()), daemon=True).start()

    def stop(self):
        self._running = False


if __name__ == '__main__':
    # Test with mock server
    def on_update(state: GPSState):
        if state.gimbal and state.target:
            print(f'Gimbal: ({state.gimbal.lat:.4f}, {state.gimbal.lon:.4f}) '
                  f'Target: ({state.target.lat:.4f}, {state.target.lon:.4f})')

    print('Starting mock GPS server...')
    server = MockGPSServer()
    server.start()

    print('Starting GPS client...')
    client = GPSClient(uri='ws://localhost:8765', on_update=on_update)
    client.start()

    try:
        for _ in range(10):
            time.sleep(1)
            state = client.get_state()
            print(f'Connected: {state.connected}, iOS: {state.fixes_received["iOS"]}, Watch: {state.fixes_received["watchOS"]}')
    except KeyboardInterrupt:
        pass

    client.stop()
    server.stop()
    print('Done')
