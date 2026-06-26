# Yard Test How-To


## Purpose

Validate the vision-only follow loop. This test uses the Prisual PTZ camera,
YOLO person detection, and the orange/red HSV color cue. GPS is left off or set to
`tracking.mode: vision_only` so the test is purely vision-driven. Direct-LoRa GPS is
not involved in this test. (The Apple-Watch GPS relay is retired/superseded.)

## Preflight

1. Orin is powered and reachable on the normal Wi-Fi/tether address.
2. Camera Ethernet is on the dedicated camera LAN:
   - Orin interface: `enP8p1s0`
   - Orin address: `192.168.100.10/24`
   - Camera address: `192.168.100.88`
   - No gateway on the camera LAN interface.
3. WaveCam web control is reachable from the phone: `http://<orin-wifi-ip>:8088`.
4. WaveCam status/network panel shows:
   - camera LAN has `192.168.100.10`
   - camera `192.168.100.88` is reachable
   - internet/uplink is on Wi-Fi or USB tether, not on camera LAN
5. Wear the orange rashguard or another large orange/red target.
6. Set `tracking.mode: vision_only` (Tune tab or web UI) so GPS cannot claim the camera. Stop Auto before manual PTZ tuning.

## Basic Run

1. Open the WaveCam web control page or the iOS Live tab.
2. Confirm the `/2` live preview is updating.
3. Use manual PTZ to put yourself near frame center.
4. Press **Start Auto** on the PTZ control rail.
5. Watch the status line:
   - `src=both`: YOLO person and color cue agree. Best state.
   - `src=yolo`: person detected, color cue not useful.
   - `src=color`: orange cue found, no YOLO person box. Useful for rough lock, zoom should hold.
   - `src=none`: no usable target; camera should stop and hold.
6. Walk left/right and toward/away from the camera.
7. Press **Stop PTZ**. The camera should stop and hold. Press **Home** to return to a wide starting pose.

## Expected Behavior

- Camera pans in the direction that reduces horizontal offset.
- Camera tilts in the direction that reduces vertical offset.
- Zoom changes only when YOLO has a person box (`src=yolo` or `src=both`), not on color-only lock.
- Manual PTZ joystick commands temporarily take over from autonomous tracking.
- `tracking.mode: vision_only` prevents GPS from claiming the camera.
- `tracking.mode: gps_only` can be used for a parallel GPS-only test, but not mixed with vision-only on the same run.

## Failure Checks

- **Wrong pan direction:** stop follow immediately. Do not tune gains until direction is fixed.
- **Oscillating pan/tilt:** lower follow gain or add deadband before increasing zoom.
- **Zoom hunts:** verify status is not rapidly flipping between `src=yolo` and `src=none`.
- **Locks onto another object:** move other orange/red objects out of frame for the first test.
- **Follow says started then stops:** open the status/log card. The WaveCam web UI reports early
  subprocess exit code and recent lines.
- **No preview:** verify RTSP `/2` from the Orin and camera LAN routing before debugging vision.

## Evidence To Capture

- Screenshot of WaveCam web UI while following.
- Short recording of the camera following a walk/jog path.
- Follow log lines showing `src`, offset, size, pan/tilt command, and zoom behavior.
- Any failure mode with exact status line and whether the camera was wide or zoomed.

## Pass Criteria

- Sustained 60 second walk-around with no competing PTZ writer.
- Target remains mostly centered at wide view.
- Zoom tightens only when YOLO has a stable person box.
- Stop returns pan/tilt to start and leaves the camera in a usable wide view.
- WaveCam reports failures clearly enough to decide whether the issue is vision, PTZ, network,
  or process startup.
