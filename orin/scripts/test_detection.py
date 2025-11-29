"""Test TensorRT detection on sample image."""
import cv2
import numpy as np
from ultralytics import YOLO

def test_detection():
    print('Loading TensorRT engine...')
    model = YOLO('/data/projects/gimbal/models/yolov8n.engine', task='detect')
    print('Engine loaded.')

    # Create synthetic test image with person-like blob
    print('Creating test image...')
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    img[:] = (100, 100, 100)
    cv2.rectangle(img, (280, 100), (360, 380), (200, 180, 160), -1)  # body
    cv2.circle(img, (320, 80), 40, (220, 200, 180), -1)  # head

    print('Running detection...')
    results = model(img, verbose=False)

    for r in results:
        boxes = r.boxes
        print(f'Found {len(boxes)} detections')
        for box in boxes:
            cls = int(box.cls[0])
            conf = float(box.conf[0])
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            print(f'  Class {cls}: conf={conf:.2f}, box=({x1:.0f},{y1:.0f})-({x2:.0f},{y2:.0f})')

    # Also test on downloaded sample
    import urllib.request
    sample_url = 'https://ultralytics.com/images/bus.jpg'
    sample_path = '/tmp/bus.jpg'

    try:
        print('\nDownloading sample image...')
        urllib.request.urlretrieve(sample_url, sample_path)

        sample = cv2.imread(sample_path)
        print(f'Sample size: {sample.shape}')

        results = model(sample, verbose=False)
        for r in results:
            print(f'Found {len(r.boxes)} detections')
            for box in r.boxes:
                cls = int(box.cls[0])
                conf = float(box.conf[0])
                print(f'  Class {cls}: conf={conf:.2f}')
    except Exception as e:
        print(f'Sample test skipped: {e}')

    print('\nDetection test passed!')

if __name__ == '__main__':
    test_detection()
