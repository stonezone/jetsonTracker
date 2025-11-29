"""Vision Tracking Pipeline - YOLOv8 TensorRT + Gimbal Control"""

import cv2
import numpy as np
import time
from pathlib import Path
from typing import Optional, Tuple, List, Dict
from dataclasses import dataclass
from ultralytics import YOLO


@dataclass
class Detection:
    """Single detection result."""
    class_id: int
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float
    
    @property
    def center(self) -> Tuple[float, float]:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)
    
    @property
    def area(self) -> float:
        return (self.x2 - self.x1) * (self.y2 - self.y1)


class VisionTracker:
    """YOLOv8 TensorRT object detection and tracking."""
    
    COCO_PERSON = 0  # Person class in COCO dataset
    
    def __init__(self, model_path: str = '/data/projects/gimbal/models/yolov8n.engine',
                 conf_threshold: float = 0.5,
                 target_class: int = COCO_PERSON):
        self.model_path = model_path
        self.conf_threshold = conf_threshold
        self.target_class = target_class
        self.model: Optional[YOLO] = None
        self.frame_size: Tuple[int, int] = (640, 480)
        
    def load_model(self):
        """Load TensorRT engine."""
        print(f'Loading model: {self.model_path}')
        self.model = YOLO(self.model_path, task='detect')
        print('Model loaded.')
        
    def detect(self, frame: np.ndarray) -> List[Detection]:
        """Run detection on frame. Returns list of Detection objects."""
        if self.model is None:
            raise RuntimeError('Model not loaded')
        
        results = self.model(frame, verbose=False, conf=self.conf_threshold)
        detections = []
        
        for r in results:
            boxes = r.boxes
            if boxes is None:
                continue
            for box in boxes:
                cls_id = int(box.cls[0])
                if cls_id != self.target_class:
                    continue
                conf = float(box.conf[0])
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                detections.append(Detection(cls_id, conf, x1, y1, x2, y2))
        
        return detections
    
    def get_target(self, detections: List[Detection]) -> Optional[Detection]:
        """Select primary target from detections (largest by area)."""
        if not detections:
            return None
        return max(detections, key=lambda d: d.area)
    
    def compute_offset(self, target: Detection, frame_width: int, frame_height: int) -> Tuple[float, float]:
        """Compute normalized offset from frame center (-1 to 1)."""
        cx, cy = target.center
        offset_x = (cx - frame_width / 2) / (frame_width / 2)
        offset_y = (cy - frame_height / 2) / (frame_height / 2)
        return offset_x, offset_y


class TrackingController:
    """Tracking control with gimbal integration."""
    
    def __init__(self, gimbal=None,
                 dead_zone: float = 0.1,
                 gain_pan: float = 50.0,
                 gain_tilt: float = 40.0,
                 max_step_pan: int = 200,
                 max_step_tilt: int = 150):
        self.gimbal = gimbal
        self.dead_zone = dead_zone
        self.gain_pan = gain_pan
        self.gain_tilt = gain_tilt
        self.max_step_pan = max_step_pan
        self.max_step_tilt = max_step_tilt
        self.tracking_enabled = True
        self.last_target_time = 0
        self.target_lost_timeout = 2.0
        
    def compute_gimbal_steps(self, offset_x: float, offset_y: float) -> Tuple[int, int]:
        """Convert normalized offset to gimbal steps."""
        pan_step = 0
        tilt_step = 0
        
        if abs(offset_x) > self.dead_zone:
            pan_step = int(offset_x * self.gain_pan)
            pan_step = max(-self.max_step_pan, min(self.max_step_pan, pan_step))
        
        if abs(offset_y) > self.dead_zone:
            tilt_step = int(offset_y * self.gain_tilt)
            tilt_step = max(-self.max_step_tilt, min(self.max_step_tilt, tilt_step))
        
        return pan_step, tilt_step
    
    def update(self, offset_x: float, offset_y: float) -> Tuple[int, int]:
        """Update gimbal position. Returns steps commanded."""
        if not self.tracking_enabled:
            return 0, 0
        
        pan_step, tilt_step = self.compute_gimbal_steps(offset_x, offset_y)
        
        if self.gimbal and (pan_step != 0 or tilt_step != 0):
            try:
                self.gimbal.move_relative(pan=pan_step, tilt=tilt_step)
            except Exception as e:
                print(f'Gimbal error: {e}')
        
        self.last_target_time = time.time()
        return pan_step, tilt_step


class TrackerPipeline:
    """Main tracking pipeline combining vision and gimbal control."""
    
    def __init__(self, camera_id: int = 0,
                 frame_width: int = 640,
                 frame_height: int = 480,
                 model_path: str = '/data/projects/gimbal/models/yolov8n.engine',
                 gimbal_port: str = '/dev/ttyACM0',
                 headless: bool = False):
        self.camera_id = camera_id
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.model_path = model_path
        self.gimbal_port = gimbal_port
        self.headless = headless
        
        self.cap: Optional[cv2.VideoCapture] = None
        self.tracker: Optional[VisionTracker] = None
        self.controller: Optional[TrackingController] = None
        self.gimbal = None
        
        self.fps_counter = 0
        self.fps_time = time.time()
        self.current_fps = 0.0
        
    def init_camera(self) -> bool:
        """Initialize camera capture."""
        print(f'Opening camera {self.camera_id}...')
        self.cap = cv2.VideoCapture(self.camera_id, cv2.CAP_V4L2)
        
        if not self.cap.isOpened():
            print('Failed to open camera')
            return False
        
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.frame_width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.frame_height)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        
        actual_w = self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        actual_h = self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        print(f'Camera opened: {int(actual_w)}x{int(actual_h)}')
        return True
    
    def init_tracker(self):
        """Initialize vision tracker."""
        self.tracker = VisionTracker(self.model_path)
        self.tracker.load_model()
    
    def init_gimbal(self) -> bool:
        """Initialize gimbal controller (optional)."""
        try:
            from gimbal_controller import GimbalController
            self.gimbal = GimbalController(self.gimbal_port)
            if self.gimbal.connect():
                if self.gimbal.ping():
                    print('Gimbal connected and responding')
                    self.controller = TrackingController(self.gimbal)
                    return True
            print('Gimbal connection failed')
        except Exception as e:
            print(f'Gimbal init error: {e}')
        
        self.controller = TrackingController(None)
        return False
    
    def update_fps(self):
        """Update FPS counter."""
        self.fps_counter += 1
        now = time.time()
        if now - self.fps_time >= 1.0:
            self.current_fps = self.fps_counter / (now - self.fps_time)
            self.fps_counter = 0
            self.fps_time = now
    
    def draw_overlay(self, frame: np.ndarray, detections: List[Detection],
                     target: Optional[Detection], offset: Tuple[float, float],
                     steps: Tuple[int, int]) -> np.ndarray:
        """Draw detection boxes and tracking info."""
        h, w = frame.shape[:2]
        
        # Draw crosshair at center
        cv2.line(frame, (w//2 - 20, h//2), (w//2 + 20, h//2), (0, 255, 0), 1)
        cv2.line(frame, (w//2, h//2 - 20), (w//2, h//2 + 20), (0, 255, 0), 1)
        
        # Draw all detections
        for det in detections:
            color = (0, 255, 255) if det != target else (0, 0, 255)
            cv2.rectangle(frame, (int(det.x1), int(det.y1)),
                         (int(det.x2), int(det.y2)), color, 2)
            cv2.putText(frame, f'{det.confidence:.2f}',
                       (int(det.x1), int(det.y1) - 5),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
        
        # Draw target info
        if target:
            cx, cy = target.center
            cv2.circle(frame, (int(cx), int(cy)), 5, (0, 0, 255), -1)
            cv2.line(frame, (w//2, h//2), (int(cx), int(cy)), (0, 0, 255), 1)
        
        # Status text
        status = f'FPS: {self.current_fps:.1f} | Targets: {len(detections)}'
        if target:
            status += f' | Offset: ({offset[0]:.2f}, {offset[1]:.2f}) | Steps: {steps}'
        cv2.putText(frame, status, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        
        tracking_status = 'TRACKING' if self.controller and self.controller.tracking_enabled else 'PAUSED'
        gimbal_status = 'GIMBAL: ON' if self.gimbal else 'GIMBAL: OFF'
        cv2.putText(frame, f'{tracking_status} | {gimbal_status}', (10, h - 10),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        
        return frame
    
    def run(self):
        """Main tracking loop."""
        print('\n=== Vision Tracking Pipeline ===')
        print('Controls: q=quit, t=toggle tracking, h=home gimbal')
        print('================================\n')
        
        if not self.init_camera():
            return
        
        self.init_tracker()
        self.init_gimbal()
        
        try:
            while True:
                ret, frame = self.cap.read()
                if not ret:
                    print('Frame capture failed')
                    break
                
                # Detection
                detections = self.tracker.detect(frame)
                target = self.tracker.get_target(detections)
                
                offset = (0.0, 0.0)
                steps = (0, 0)
                
                if target:
                    offset = self.tracker.compute_offset(target, frame.shape[1], frame.shape[0])
                    steps = self.controller.update(offset[0], offset[1])
                
                self.update_fps()
                
                # Display
                if not self.headless:
                    display = self.draw_overlay(frame.copy(), detections, target, offset, steps)
                    cv2.imshow('Tracker', display)
                    
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord('q'):
                        break
                    elif key == ord('t'):
                        if self.controller:
                            self.controller.tracking_enabled = not self.controller.tracking_enabled
                            print(f'Tracking: {self.controller.tracking_enabled}')
                    elif key == ord('h'):
                        if self.gimbal:
                            print('Homing gimbal...')
                            self.gimbal.home()
                
        finally:
            print('\nShutting down...')
            if self.cap:
                self.cap.release()
            if self.gimbal:
                self.gimbal.disconnect()
            cv2.destroyAllWindows()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Vision Tracking Pipeline')
    parser.add_argument('--camera', type=int, default=0, help='Camera device ID')
    parser.add_argument('--width', type=int, default=640, help='Frame width')
    parser.add_argument('--height', type=int, default=480, help='Frame height')
    parser.add_argument('--model', type=str, default='/data/projects/gimbal/models/yolov8n.engine', help='Model path')
    parser.add_argument('--gimbal', type=str, default='/dev/ttyACM0', help='Gimbal serial port')
    parser.add_argument('--headless', action='store_true', help='Run without display')
    args = parser.parse_args()
    
    pipeline = TrackerPipeline(
        camera_id=args.camera,
        frame_width=args.width,
        frame_height=args.height,
        model_path=args.model,
        gimbal_port=args.gimbal,
        headless=args.headless
    )
    pipeline.run()
