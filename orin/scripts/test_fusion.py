#!/usr/bin/env python3
"""Quick test of GPS-Vision fusion pipeline."""

import sys
import argparse
import time
sys.path.insert(0, '/data/projects/gimbal')

def test_geo():
    """Test geographic calculations."""
    print("\n=== Testing Geo Calculations ===")
    from gps_fusion import GeoPoint, calculate_relative_position, gps_to_gimbal_angles

    # Gimbal at origin facing north, target 100m north
    gimbal = GeoPoint(lat=37.7749, lon=-122.4194, alt=10, heading=0)
    target = GeoPoint(lat=37.7758, lon=-122.4194, alt=10)

    rel = calculate_relative_position(gimbal, target)
    pan, tilt = gps_to_gimbal_angles(rel)

    print(f"Distance: {rel.distance:.1f}m (expected ~100m)")
    print(f"Bearing: {rel.bearing:.1f}° (expected ~0°)")
    print(f"Pan: {pan:.1f}°, Tilt: {tilt:.1f}°")
    print("✓ Geo OK")

def test_fusion():
    """Test fusion engine modes."""
    print("\n=== Testing Fusion Engine ===")
    from gps_fusion import FusionEngine, TrackingMode, VisualTarget, GeoPoint

    fusion = FusionEngine()
    now = time.time()

    # Visual only
    visual = VisualTarget(cx=0.6, cy=0.45, width=0.15, height=0.3, confidence=0.85)
    fusion.update_visual(visual)
    out = fusion.compute()
    print(f"Visual only: {out.mode} (pan={out.pan_offset:.2f}, tilt={out.tilt_offset:.2f})")

    # GPS assisted
    gimbal = GeoPoint(lat=37.7749, lon=-122.4194, alt=10, heading=0, timestamp=now)
    target = GeoPoint(lat=37.7758, lon=-122.4194, alt=10, speed=2.0, course=45, timestamp=now)
    fusion.update_gps(gimbal, target)
    out = fusion.compute()
    print(f"With GPS: {out.mode} (distance={out.gps_distance:.1f}m)")

    # GPS primary (no visual)
    fusion2 = FusionEngine()
    fusion2.update_gps(gimbal, target)
    out2 = fusion2.compute()
    print(f"GPS only: {out2.mode} (pan={out2.pan_offset:.2f})")
    print("✓ Fusion OK")

def test_gps_client():
    """Test GPS client (mock server)."""
    print("\n=== Testing GPS Client ===")
    from gps_fusion.gps_client import MockGPSServer, GPSClient

    # Start mock server (runs in thread)
    server = MockGPSServer(port=18765)
    server.start()
    print(f"Mock server started")
    time.sleep(0.5)

    # Create client
    client = GPSClient(f'ws://127.0.0.1:18765')
    client.start()

    time.sleep(2.0)  # Let some data flow

    state = client.get_state()
    print(f"Gimbal GPS: {state.gimbal is not None}")
    print(f"Target GPS: {state.target is not None}")
    if state.target:
        print(f"  lat={state.target.lat:.4f}, lon={state.target.lon:.4f}")

    client.stop()
    server.stop()
    print("✓ GPS Client OK")

def test_camera(camera_id):
    """Test camera capture."""
    print(f"\n=== Testing Camera (ID: {camera_id}) ===")
    import cv2

    cap = cv2.VideoCapture(camera_id)
    if not cap.isOpened():
        print(f"✗ Cannot open camera {camera_id}")
        return False

    ret, frame = cap.read()
    cap.release()

    if ret:
        print(f"Frame: {frame.shape[1]}x{frame.shape[0]}")
        print("✓ Camera OK")
        return True
    else:
        print("✗ Cannot read frame")
        return False

def test_detection(camera_id):
    """Test YOLOv8 detection."""
    print("\n=== Testing Detection ===")
    import cv2
    from ultralytics import YOLO

    cap = cv2.VideoCapture(camera_id)
    if not cap.isOpened():
        print(f"✗ Camera {camera_id} not available")
        return

    model = YOLO('/data/projects/gimbal/models/yolov8n.engine')

    ret, frame = cap.read()
    if ret:
        results = model(frame, verbose=False)
        boxes = results[0].boxes
        print(f"Detected {len(boxes)} objects")
        for box in boxes[:3]:
            cls = int(box.cls[0])
            conf = float(box.conf[0])
            print(f"  Class {cls}: {conf:.2f}")
        print("✓ Detection OK")

    cap.release()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Test GPS-Vision fusion')
    parser.add_argument('--camera', type=int, default=10, help='Camera ID')
    parser.add_argument('--all', action='store_true', help='Run all tests')
    parser.add_argument('--geo', action='store_true', help='Test geo calculations')
    parser.add_argument('--fusion', action='store_true', help='Test fusion engine')
    parser.add_argument('--gps', action='store_true', help='Test GPS client')
    parser.add_argument('--cam', action='store_true', help='Test camera')
    parser.add_argument('--detect', action='store_true', help='Test detection')
    args = parser.parse_args()

    if args.all or args.geo:
        test_geo()
    if args.all or args.fusion:
        test_fusion()
    if args.all or args.gps:
        test_gps_client()
    if args.all or args.cam:
        test_camera(args.camera)
    if args.all or args.detect:
        test_detection(args.camera)

    if not any([args.all, args.geo, args.fusion, args.gps, args.cam, args.detect]):
        print('Usage: python3 test_fusion.py --all')
        print('       python3 test_fusion.py --geo --fusion')
        print('       python3 test_fusion.py --cam --camera 10')
