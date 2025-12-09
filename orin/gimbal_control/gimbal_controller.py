"""Gimbal Controller - Pan/Tilt stepper control with limit switch support."""

import serial
import time
import logging
from typing import Optional, Tuple
from dataclasses import dataclass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('gimbal')


@dataclass
class GimbalLimits:
    """Gimbal axis limits in steps.

    Calibrated 2025-12-08/09 (after limit switch fixes):
    - PAN range: 0 to 4200 (~4255 steps total, measured 2025-12-08)
    - TILT range: 0 to 2600 (~2675 steps total, measured 2025-12-09)
    - Home position: 0 for both axes (at negative limit switches)

    Limit switches:
    - D6/PB10 (PP) = physical LEFT switch, stops rightward pan
    - D11/PA7 (PN) = physical RIGHT switch, stops leftward pan / home
    - D12/PA6 (TP) = tilt positive limit
    - D7/PA8 (TN) = tilt negative limit / home
    """
    # Steps per degree (calibrated from actual range)
    # PAN: 4255 steps / 180° ≈ 23.6 steps/degree
    # TILT: 2675 steps / 180° ≈ 14.86 steps/degree
    steps_per_degree_pan: float = 23.6
    steps_per_degree_tilt: float = 14.86

    # Actual measured limits in steps (from home position = 0)
    pan_min_steps: int = 0       # Left limit / home
    pan_max_steps: int = 4200    # Right limit (PP triggers ~4255)
    tilt_min_steps: int = 0      # Down limit / home
    tilt_max_steps: int = 2600   # Up limit (TP triggers ~2675)

    # Center position (middle of travel)
    pan_center_steps: int = 2100   # Halfway between 0 and 4200
    tilt_center_steps: int = 1300  # Halfway between 0 and 2600

    # Soft limits in degrees (relative to home = 0)
    pan_min: float = 0.0      # At home/left
    pan_max: float = 178.0    # 4200 / 23.6
    tilt_min: float = 0.0     # At home/down
    tilt_max: float = 175.0   # 2600 / 14.86

    def pan_to_steps(self, degrees: float) -> int:
        return int(degrees * self.steps_per_degree_pan)

    def tilt_to_steps(self, degrees: float) -> int:
        return int(degrees * self.steps_per_degree_tilt)

    def steps_to_pan(self, steps: int) -> float:
        return steps / self.steps_per_degree_pan

    def steps_to_tilt(self, steps: int) -> float:
        return steps / self.steps_per_degree_tilt


class GimbalController:
    """Controls pan/tilt gimbal via UART to STM32/Arduino."""
    
    def __init__(self, port: str = '/dev/ttyACM0', baudrate: int = 115200,
                 limits: Optional[GimbalLimits] = None):
        self.port = port
        self.baudrate = baudrate
        self.ser: Optional[serial.Serial] = None
        self.limits = limits or GimbalLimits()
        
        # Current position tracking (steps from center)
        self._pan_steps = 0
        self._tilt_steps = 0
        self._homed = False
        
    def connect(self) -> bool:
        """Open serial connection. Returns True on success."""
        try:
            self.ser = serial.Serial(self.port, self.baudrate, timeout=1)
            time.sleep(0.5)  # Wait for Arduino reset
            self.ser.reset_input_buffer()
            logger.info(f'Connected to {self.port}')
            return True
        except serial.SerialException as e:
            logger.error(f'Connection failed: {e}')
            return False
    
    def disconnect(self):
        """Close serial connection."""
        if self.ser and self.ser.is_open:
            self.ser.close()
            self.ser = None
            logger.info('Disconnected')
    
    def _send(self, cmd: str, timeout: float = 2.0, expect_prefix: str = None) -> str:
        """Send command and return response.

        Args:
            cmd: Command to send
            timeout: Max time to wait for response
            expect_prefix: If set, keep reading until response starts with this prefix
        """
        if not self.ser or not self.ser.is_open:
            raise RuntimeError('Not connected')

        # Clear any stale data
        self.ser.reset_input_buffer()
        time.sleep(0.02)  # Small delay for buffer to settle

        # Send command
        self.ser.write(f'{cmd}\n'.encode('ascii'))
        self.ser.flush()

        # Wait for response
        start = time.time()
        while time.time() - start < timeout:
            if self.ser.in_waiting:
                line = self.ser.readline().decode('ascii', errors='ignore').strip()
                if not line:
                    continue
                # If we expect a specific prefix, keep reading until we get it
                if expect_prefix and not line.startswith(expect_prefix):
                    logger.debug(f'Skipping unexpected response: {line}')
                    continue
                return line
            time.sleep(0.01)

        logger.warning(f'Timeout waiting for response to: {cmd}')
        return ''

    def _drain_buffer(self):
        """Clear any pending data in the serial buffer."""
        if self.ser and self.ser.is_open:
            time.sleep(0.05)
            while self.ser.in_waiting:
                self.ser.read(self.ser.in_waiting)
                time.sleep(0.01)
    
    def ping(self) -> bool:
        """Test connection. Returns True if PONG received."""
        return self._send('PING') == 'PONG'
    
    def get_position(self) -> Tuple[int, int]:
        """Get current position in steps. Returns (pan, tilt)."""
        resp = self._send('GET_POS', expect_prefix='POS')
        try:
            # Response format: "POS PAN:xxx TILT:yyy"
            parts = resp.split()
            pan = int(parts[1].split(':')[1])
            tilt = int(parts[2].split(':')[1])
            self._pan_steps = pan
            self._tilt_steps = tilt
            return pan, tilt
        except (IndexError, ValueError) as e:
            logger.debug(f'Position parse error: {e}, response was: {resp}')
            return self._pan_steps, self._tilt_steps
    
    def get_position_degrees(self) -> Tuple[float, float]:
        """Get current position in degrees. Returns (pan, tilt)."""
        pan_steps, tilt_steps = self.get_position()
        return (self.limits.steps_to_pan(pan_steps),
                self.limits.steps_to_tilt(tilt_steps))
    
    def get_limits_status(self) -> dict:
        """Get limit switch status.

        Returns dict with:
            pan_limit_neg (PN): True if left limit triggered (D11)
            pan_limit_pos (PP): True if right limit triggered (D6)
            tilt_limit_neg (TN): True if down limit triggered
            tilt_limit_pos (TP): True if up limit triggered
            pan_homed (PH): True if pan has been homed
            tilt_homed (TH): True if tilt has been homed
        """
        resp = self._send('GET_STATUS', expect_prefix='STATUS')
        try:
            # Response format: "STATUS PN:x PP:x TN:x TP:x PH:x TH:x"
            parts = resp.split()
            return {
                'pan_limit_neg': bool(int(parts[1].split(':')[1])),
                'pan_limit_pos': bool(int(parts[2].split(':')[1])),
                'tilt_limit_neg': bool(int(parts[3].split(':')[1])),
                'tilt_limit_pos': bool(int(parts[4].split(':')[1])),
                'pan_homed': bool(int(parts[5].split(':')[1])),
                'tilt_homed': bool(int(parts[6].split(':')[1])),
                'homed': self._homed
            }
        except (IndexError, ValueError) as e:
            logger.debug(f'Status parse error: {e}, response was: {resp}')
            return {'pan_limit_neg': False, 'pan_limit_pos': False,
                    'tilt_limit_neg': False, 'tilt_limit_pos': False,
                    'pan_homed': False, 'tilt_homed': False,
                    'homed': False}
    
    def _clamp_pan(self, steps: int) -> int:
        """Clamp pan to hardware limits."""
        return max(self.limits.pan_min_steps, min(self.limits.pan_max_steps, steps))

    def _clamp_tilt(self, steps: int) -> int:
        """Clamp tilt to hardware limits."""
        return max(self.limits.tilt_min_steps, min(self.limits.tilt_max_steps, steps))
    
    def move_relative(self, pan: int = 0, tilt: int = 0,
                      respect_limits: bool = True,
                      wait_for_completion: bool = False) -> Tuple[int, int]:
        """Move relative steps. Returns actual (pan, tilt) moved.

        Args:
            pan: Steps to move pan axis (positive = right)
            tilt: Steps to move tilt axis (positive = up)
            respect_limits: If True, clamp to soft limits
            wait_for_completion: If True, wait for motion to complete
        """
        pan_actual = 0
        tilt_actual = 0

        if pan != 0:
            target = self._pan_steps + pan
            if respect_limits:
                target = self._clamp_pan(target)
                pan = target - self._pan_steps

            if pan != 0:
                # Response format: "OK PAN:xxx" where xxx is steps actually moved
                resp = self._send(f'PAN_REL:{pan}', expect_prefix='OK')
                try:
                    pan_actual = int(resp.split(':')[1])
                    self._pan_steps += pan_actual
                except (IndexError, ValueError) as e:
                    logger.debug(f'PAN_REL parse error: {e}, response: {resp}')
                    # Assume full move if parse fails
                    self._pan_steps += pan

        if tilt != 0:
            target = self._tilt_steps + tilt
            if respect_limits:
                target = self._clamp_tilt(target)
                tilt = target - self._tilt_steps

            if tilt != 0:
                resp = self._send(f'TILT_REL:{tilt}', expect_prefix='OK')
                try:
                    tilt_actual = int(resp.split(':')[1])
                    self._tilt_steps += tilt_actual
                except (IndexError, ValueError) as e:
                    logger.debug(f'TILT_REL parse error: {e}, response: {resp}')
                    self._tilt_steps += tilt

        if wait_for_completion:
            # Wait a bit for motion to settle, then drain buffer
            time.sleep(0.1)
            self._drain_buffer()

        return pan_actual, tilt_actual
    
    def move_relative_degrees(self, pan_deg: float = 0, tilt_deg: float = 0) -> Tuple[float, float]:
        """Move relative degrees. Returns actual degrees moved."""
        pan_steps = self.limits.pan_to_steps(pan_deg)
        tilt_steps = self.limits.tilt_to_steps(tilt_deg)
        actual_pan, actual_tilt = self.move_relative(pan_steps, tilt_steps)
        return (self.limits.steps_to_pan(actual_pan),
                self.limits.steps_to_tilt(actual_tilt))
    
    def sync_position(self) -> Tuple[int, int]:
        """Re-sync internal position tracking with actual hardware position."""
        self._drain_buffer()
        return self.get_position()

    def move_absolute(self, pan: Optional[int] = None,
                      tilt: Optional[int] = None,
                      wait_for_completion: bool = False) -> Tuple[int, int]:
        """Move to absolute position in steps. Returns final (pan, tilt)."""
        if pan is not None:
            pan = self._clamp_pan(pan)
            self._send(f'PAN_ABS:{pan}', expect_prefix='OK')
            self._pan_steps = pan
        if tilt is not None:
            tilt = self._clamp_tilt(tilt)
            self._send(f'TILT_ABS:{tilt}', expect_prefix='OK')
            self._tilt_steps = tilt

        if wait_for_completion:
            time.sleep(0.1)
            self._drain_buffer()

        return self._pan_steps, self._tilt_steps
    
    def move_absolute_degrees(self, pan_deg: Optional[float] = None,
                              tilt_deg: Optional[float] = None) -> Tuple[float, float]:
        """Move to absolute position in degrees."""
        pan_steps = self.limits.pan_to_steps(pan_deg) if pan_deg is not None else None
        tilt_steps = self.limits.tilt_to_steps(tilt_deg) if tilt_deg is not None else None
        final_pan, final_tilt = self.move_absolute(pan_steps, tilt_steps)
        return (self.limits.steps_to_pan(final_pan),
                self.limits.steps_to_tilt(final_tilt))
    
    def center(self) -> Tuple[int, int]:
        """Move to center position (0, 0)."""
        return self.move_absolute(0, 0)
    
    def home(self, axis: str = 'all', timeout: float = 30.0) -> bool:
        """
        Home axis using limit switches.
        axis: 'pan', 'tilt', or 'all'
        
        Homing sequence:
        1. Move slowly toward negative limit until switch triggers
        2. Back off slightly
        3. Move to center position
        """
        cmd = {'pan': 'HOME_PAN', 'tilt': 'HOME_TILT', 'all': 'HOME_ALL'}[axis]
        self._send(cmd)
        
        start = time.time()
        while time.time() - start < timeout:
            if self.ser.in_waiting:
                line = self.ser.readline().decode('ascii').strip()
                logger.info(f'Homing: {line}')
                if 'HOMED' in line:
                    self._homed = True
                    self._pan_steps = 0
                    self._tilt_steps = 0
                    return True
                if 'ERROR' in line or 'LIMIT' in line:
                    logger.error(f'Homing failed: {line}')
                    return False
            time.sleep(0.1)
        
        logger.error('Homing timeout')
        return False
    
    def emergency_stop(self):
        """Immediately stop all motion."""
        self._send('STOP')
        logger.warning('Emergency stop triggered')
    
    def set_speed(self, pan_speed: int = 1000, tilt_speed: int = 1000):
        """Set motor speeds in steps/second."""
        self._send(f'SET_SPEED:{pan_speed},{tilt_speed}')
    
    def __enter__(self):
        self.connect()
        return self
    
    def __exit__(self, *args):
        self.disconnect()


if __name__ == '__main__':
    # Test connection
    print('Testing gimbal controller...')
    
    gimbal = GimbalController()
    if gimbal.connect():
        print(f'Ping: {gimbal.ping()}')
        
        pos = gimbal.get_position_degrees()
        print(f'Position: pan={pos[0]:.1f}°, tilt={pos[1]:.1f}°')
        
        limits = gimbal.get_limits_status()
        print(f'Limits: {limits}')
        
        # Test small movement
        print('Moving pan +10 steps...')
        moved = gimbal.move_relative(pan=10)
        print(f'Moved: {moved}')
        
        gimbal.disconnect()
    else:
        print('Could not connect - is Arduino attached?')
