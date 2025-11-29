#!/bin/bash
# Stream Android phone camera to virtual webcam using scrcpy

# Check ADB
if ! adb devices | grep -q 'device$'; then
    echo 'No ADB device found. Enable USB debugging on phone.'
    echo 'Settings > Developer Options > USB Debugging'
    exit 1
fi

# Load v4l2loopback if not loaded
if [ ! -e /dev/video10 ]; then
    sudo modprobe v4l2loopback devices=1 video_nr=10 card_label=PhoneCam exclusive_caps=1
fi

# Give access to video device
sudo chmod 666 /dev/video10

echo 'Starting phone camera stream to /dev/video10...'
echo 'Open camera app on phone and keep it fullscreen'
echo 'Press Ctrl+C to stop'

# Stream phone screen to v4l2loopback
scrcpy --v4l2-sink=/dev/video10 --no-display --max-size 640 --max-fps 30 2>&1
