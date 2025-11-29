"""Integration of GPS fusion with vision tracker and gimbal."""

import sys
import time
import argparse
from typing import Optional
from dataclasses import dataclass

# Add parent directory for imports
sys.path.insert(0, '/data/projects/gimbal')

from .geo_calc import GeoPoint
from .gps_client import GPSClient, GPSState
from .fusion_engine import FusionEngine, VisualTarget, TrackingMode, FusionOutput


@dataclass
class IntegratedTrackerConfig:
    """Configuration for integrated tracker."""
    # Camera
    camera_id: int = 10
    frame_width: int = 640
    frame_height: int = 480
    
    # Model
    model_path: str = '/data/projects/gimbal/models/yolov8n.engine'
    conf_threshold: float = 0.5
    
    # GPS
    gps_uri: str = 'ws://192.168.1.100:8765'  # iPhone IP
    gps_enabled: bool = True
    
    # Gimbal
    gimbal_port: str = '/dev/ttyACM0'
    gimbal_enabled: bool = True
    
    # Tracking
    dead_zone: float = 0.08
    gain_pan: float = 40.0
    gain_tilt: float = 30.0
    max_step: int = 150
    
    # Fusion
    visual_timeout: float = 1.0
    gps_timeout: float = 5.0
    prediction_enabled: bool = True


class IntegratedTracker:
    """Combined GPS + Vision tracker with gimbal control."""
    
    def __init__(self, config: IntegratedTrackerConfig):
        self.config = config
        self.fusion = FusionEngine(
            frame_width=config.frame_width,
            frame_height=config.frame_height,
            visual_timeout=config.visual_timeout,
            gps_timeout=config.gps_timeout
        )
        
        self.gps_client: Optional[GPSClient] = None
        self.gimbal = None
        self.model = None
        self.cap = None
        
        self.running = False
        self.stats = {
            'frames': 0,
            'detections': 0,
            'gps_fixes': 0,
            'mode_changes': 0
        }
        self.last_mode = TrackingMode.IDLE
    
    def _on_gps_update(self, state: GPSState) -> None:
        """Handle GPS update from client."""
        self.fusion.update_gps(state.gimbal, state.target)
        self.stats['gps_fixes'] += 1
    
    def init_gps(self) -> bool:
        """Initialize GPS client."""
        if not self.config.gps_enabled:
            print('GPS disabled')
            return False
        
        print(f'Connecting to GPS server at {self.config.gps_uri}...')
        self.gps_client = GPSClient(
            uri=self.config.gps_uri,
            on_update=self._on_gps_update
        )
        self.gps_client.start()
        return True
    
    def init_gimbal(self) -> bool:
        """Initialize gimbal controller."""
        if not self.config.gimbal_enabled:
            print('Gimbal disabled')
            return False
        
        try:
            from gimbal_controller import GimbalController
            self.gimbal = GimbalController(self.config.gimbal_port)
            if self.gimbal.connect():
                if self.gimbal.ping():
                    print('Gimbal connected')
                    return True
            print('Gimbal connection failed')
        except Exception as e:
            print(f'Gimbal init error: {e}')
        return False
    
    def init_vision(self) -> bool:
        """Initialize camera and model."""
        import cv2
        from ultralytics import YOLO
        
        print(f'Opening camera {self.config.camera_id}...')
        self.cap = cv2.VideoCapture(self.config.camera_id, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            print('Camera failed')
            return False
        
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.frame_width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.frame_height)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        
        print(f'Loading model {self.config.model_path}...')
        self.model = YOLO(self.config.model_path, task='detect')
        print('Vision initialized')
        return True
    
    def detect_target(self, frame) -> Optional[VisualTarget]:
        """Run detection and return best target."""
        results = self.model(frame, verbose=False, conf=self.config.conf_threshold)
        
        best = None
        best_area = 0
        
        for r in results:
            for box in r.boxes:
                if int(box.cls[0]) != 0:  # Person class
                    continue
                
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                w = x2 - x1
                h = y2 - y1
                area = w * h
                
                if area > best_area:
                    best_area = area
                    # Normalize to 0-1
                    best = VisualTarget(
                        cx=(x1 + x2) / 2 / self.config.frame_width,
                        cy=(y1 + y2) / 2 / self.config.frame_height,
                        width=w / self.config.frame_width,
                        height=h / self.config.frame_height,
                        confidence=float(box.conf[0])
                    )
        
        return best
    
    def compute_gimbal_command(self, fusion_out: FusionOutput) -> tuple:
        """Convert fusion output to gimbal steps."""
        pan_step = 0
        tilt_step = 0
        
        if fusion_out.confidence < 0.1:
            return 0, 0
        
        # Use prediction if target moving fast and GPS available
        if (self.config.prediction_enabled and 
            fusion_out.predicted_pan is not None and
            fusion_out.mode == TrackingMode.GPS_ASSISTED):
            # Blend current offset with prediction
            blend = 0.3  # 30% prediction
            pan_offset = (1 - blend) * fusion_out.pan_offset + blend * fusion_out.predicted_pan
            tilt_offset = (1 - blend) * fusion_out.tilt_offset + blend * fusion_out.predicted_tilt
        else:
            pan_offset = fusion_out.pan_offset
            tilt_offset = fusion_out.tilt_offset
        
        # Apply dead zone and gain
        if abs(pan_offset) > self.config.dead_zone:
            pan_step = int(pan_offset * self.config.gain_pan)
            pan_step = max(-self.config.max_step, min(self.config.max_step, pan_step))
        
        if abs(tilt_offset) > self.config.dead_zone:
            tilt_step = int(tilt_offset * self.config.gain_tilt)
            tilt_step = max(-self.config.max_step, min(self.config.max_step, tilt_step))
        
        return pan_step, tilt_step
    
    def run_frame(self, frame) -> FusionOutput:
        """Process one frame."""
        # Detect
        target = self.detect_target(frame)
        self.fusion.update_visual(target)
        
        if target:
            self.stats['detections'] += 1
        
        # Fuse
        fusion_out = self.fusion.compute()
        
        # Track mode changes
        if fusion_out.mode != self.last_mode:
            self.stats['mode_changes'] += 1
            print(f'Mode: {self.last_mode.name} -> {fusion_out.mode.name}')
            self.last_mode = fusion_out.mode
        
        # Command gimbal
        if self.gimbal and fusion_out.mode != TrackingMode.IDLE:
            pan_step, tilt_step = self.compute_gimbal_command(fusion_out)
            if pan_step != 0 or tilt_step != 0:
                try:
                    self.gimbal.move_relative(pan=pan_step, tilt=tilt_step)
                except Exception as e:
                    print(f'Gimbal error: {e}')
        
        self.stats['frames'] += 1
        return fusion_out
    
    def run(self):
        """Main tracking loop."""
        import cv2
        
        print('\n=== Integrated GPS-Vision Tracker ===')
        print('Controls: q=quit, g=toggle GPS, p=toggle prediction')
        print('========================================\n')
        
        self.init_gps()
        self.init_gimbal()
        if not self.init_vision():
            return
        
        self.running = True
        fps_time = time.time()
        fps_count = 0
        current_fps = 0
        
        try:
            while self.running:
                ret, frame = self.cap.read()
                if not ret:
                    continue
                
                fusion_out = self.run_frame(frame)
                
                # FPS counter
                fps_count += 1
                if time.time() - fps_time >= 1.0:
                    current_fps = fps_count
                    fps_count = 0
                    fps_time = time.time()
                
                # Status line
                gps_status = 'GPS:OK' if self.gps_client and self.gps_client.get_state().connected else 'GPS:--'
                dist_str = f'{fusion_out.gps_distance:.0f}m' if fusion_out.gps_distance else '--'
                print(f'\r[{current_fps}fps] {fusion_out.mode.name:12} | '
                      f'{gps_status} dist={dist_str} | '
                      f'pan={fusion_out.pan_offset:+.2f} tilt={fusion_out.tilt_offset:+.2f} '
                      f'conf={fusion_out.confidence:.2f}', end='')
                
        except KeyboardInterrupt:
            print('\n\nInterrupted')
        finally:
            self.shutdown()
    
    def shutdown(self):
        """Clean shutdown."""
        self.running = False
        
        if self.cap:
            self.cap.release()
        if self.gps_client:
            self.gps_client.stop()
        if self.gimbal:
            self.gimbal.disconnect()
        
        print(f'\nStats: {self.stats}')


def main():
    parser = argparse.ArgumentParser(description='Integrated GPS-Vision Tracker')
    parser.add_argument('--camera', type=int, default=10)
    parser.add_argument('--gps-uri', type=str, default='ws://192.168.1.100:8765')
    parser.add_argument('--no-gps', action='store_true')
    parser.add_argument('--no-gimbal', action='store_true')
    parser.add_argument('--no-predict', action='store_true')
    args = parser.parse_args()
    
    config = IntegratedTrackerConfig(
        camera_id=args.camera,
        gps_uri=args.gps_uri,
        gps_enabled=not args.no_gps,
        gimbal_enabled=not args.no_gimbal,
        prediction_enabled=not args.no_predict
    )
    
    tracker = IntegratedTracker(config)
    tracker.run()


if __name__ == '__main__':
    main()
