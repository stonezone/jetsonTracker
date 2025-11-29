"""Check for available cameras."""
import cv2
import glob

def check_cameras():
    print('Checking video devices...')
    devices = glob.glob('/dev/video*')

    if not devices:
        print('No video devices found.')
        print('\nTo use Android phone as USB webcam:')
        print('1. Install DroidCam or USB Camera app on Android')
        print('2. Enable USB debugging on Android')
        print('3. Connect phone via USB')
        print('4. Run the webcam app on phone')
        print('5. Check for /dev/video* devices')
        return

    print(f'Found devices: {devices}')

    for dev in devices:
        idx = int(dev.replace('/dev/video', ''))
        cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
        if cap.isOpened():
            w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
            h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
            ret, frame = cap.read()
            if ret:
                print(f'  {dev}: OK ({int(w)}x{int(h)})')
            else:
                print(f'  {dev}: opened but no frames')
            cap.release()
        else:
            print(f'  {dev}: failed to open')

if __name__ == '__main__':
    check_cameras()
