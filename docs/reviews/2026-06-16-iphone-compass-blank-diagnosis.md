# iPhone Compass Blank in WaveCam App — Diagnosis and Fix

**Date:** 2026-06-16  
**Symptom:** `SensorsView` shows "Phone (magnetic) —" and "Phone (true) —" even though the iPhone is on the rig and connected.  
**Goal:** Get the iPhone compass reading to display (and ultimately feed calibration).  
**Constraint:** Read-only analysis; no code changes made yet.

---

## 1. What Should Be Happening

`ios/WaveCam/Sources/PhoneSensorPublisher.swift` is responsible for compass data:

1. `startHeading()` checks `CLLocationManager.headingAvailable()` and calls `locationManager.startUpdatingHeading()`.
2. `locationManager(_:didUpdateHeading:)` receives `CLHeading` updates and stores:
   - `latestHeadingDeg = newHeading.magneticHeading`
   - `latestHeadingAcc = newHeading.headingAccuracy`
   - `latestTrueHeadingDeg = newHeading.trueHeading >= 0 ? trueHeading : nil`
3. The 1 Hz `publish()` loop sends the values to the backend over `/api/v1/sensors/phone/ws`.
4. The backend returns them in `/status.sensors.phone`, which `SensorsView` renders.

The pipeline is correct in principle. The blank reading means `didUpdateHeading` is never firing **or** the publisher is being torn down before it fires.

---

## 2. Most Likely Causes

### 2.1 You are running on the iOS Simulator

**This is the #1 suspect.** The iOS Simulator does **not** simulate the magnetometer. `CLLocationManager.startUpdatingHeading()` will not produce `didUpdateHeading` callbacks in Simulator, even with a GPX file. You must test compass on a physical device.

**How to verify:**

- If you are building/running with `xcodebuild -sdk iphonesimulator` or Xcode's iPhone 16 simulator, compass will always be blank.
- If you are running on a physical iPhone, continue to the next checks.

### 2.2 Location-authorization race

`PhoneSensorPublisher.startSensors()` calls `startHeading()` immediately, then `startLocation()`, which may trigger the permission prompt. On iOS, `startUpdatingHeading()` often needs an active/authorized location session to deliver updates. The current code does **not** restart heading updates after the user grants permission.

Current `locationManagerDidChangeAuthorization` (lines 284–300):

```swift
nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
    Task { @MainActor [weak self] in
        guard let self else { return }
        switch manager.authorizationStatus {
        case .authorizedWhenInUse, .authorizedAlways:
            if self.running {
                manager.desiredAccuracy = kCLLocationAccuracyBest
                manager.startUpdatingLocation()
            }
        default:
            self.latestLat = nil
            self.latestLon = nil
            self.latestHAcc = nil
        }
    }
}
```

It restarts **location** updates but not **heading** updates. If the prompt appeared after `startSensors()` ran, `startUpdatingHeading()` was called before authorization and may never resume.

### 2.3 `startHeading()` is not guarded by authorization

`startHeading()` currently does:

```swift
private func startHeading() {
    guard CLLocationManager.headingAvailable() else { return }
    locationManager.headingOrientation = .landscapeRight
    locationManager.startUpdatingHeading()
}
```

It does not check `authorizationStatus`. Calling `startUpdatingHeading()` while `.notDetermined` or `.denied` can silently fail.

### 2.4 App not in `.live` mode or publisher stopped

`startSensors()` is only called when `client.mode == .live` (line 94). If the app is in mock/offline mode, heading never starts. Also, after 30 s of WebSocket disconnect, `stopSensors()` is called (line 232–234), which stops heading updates.

### 2.5 `PhoneSensorPublisher` deallocated or not started

`WaveCamApp` holds `sensorPublisher` as a `@State` property. SwiftUI `@State` is retained for the lifetime of the view, so deallocation is unlikely, but if `scenePhase` transitions rapidly, `start()`/`stop()` could race.

---

## 3. Minimal Fix (iOS only)

Change `PhoneSensorPublisher` so that heading updates are started **only after** location authorization is granted, and are restarted whenever authorization changes to granted.

### 3.1 Patch for `PhoneSensorPublisher.swift`

```swift
// MARK: - Lifecycle

func start() {
    guard !running else { return }
    running = true
    if client.mode == .live { startSensors() }
    socket.open()
    startPublishTimer()
}

private func startSensors() {
    guard !sensorsActive else { return }
    sensorsActive = true
    startLocation()            // requests auth if needed
    // Heading requires location auth; start it only if already authorized,
    // otherwise it will be started from the delegate callback.
    if isAuthorized(locationManager.authorizationStatus) {
        startHeading()
    }
    startAccelerometer()
    startAltimeter()
}

private func stopSensors() {
    guard sensorsActive else { return }
    sensorsActive = false
    locationManager.stopUpdatingHeading()
    locationManager.stopUpdatingLocation()
    motionManager.stopDeviceMotionUpdates()
    altimeter.stopRelativeAltitudeUpdates()
}

private func isAuthorized(_ status: CLAuthorizationStatus) -> Bool {
    switch status {
    case .authorizedWhenInUse, .authorizedAlways: return true
    default: return false
    }
}

// MARK: - Heading

private func startHeading() {
    guard CLLocationManager.headingAvailable(),
          isAuthorized(locationManager.authorizationStatus)
    else { return }
    locationManager.headingOrientation = .landscapeRight
    locationManager.startUpdatingHeading()
}

// MARK: - Location

private func startLocation() {
    let status = locationManager.authorizationStatus
    switch status {
    case .notDetermined:
        locationManager.requestWhenInUseAuthorization()
    case .authorizedWhenInUse, .authorizedAlways:
        locationManager.desiredAccuracy = kCLLocationAccuracyBest
        locationManager.startUpdatingLocation()
    default:
        break
    }
}

// MARK: - CLLocationManagerDelegate

nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
    Task { @MainActor [weak self] in
        guard let self else { return }
        switch manager.authorizationStatus {
        case .authorizedWhenInUse, .authorizedAlways:
            if self.running {
                manager.desiredAccuracy = kCLLocationAccuracyBest
                manager.startUpdatingLocation()
                self.startHeading()   // <-- ADD THIS
            }
        default:
            self.latestLat = nil
            self.latestLon = nil
            self.latestHAcc = nil
            self.latestHeadingDeg = nil
            self.latestHeadingAcc = -1
            self.latestTrueHeadingDeg = nil
        }
    }
}
```

### 3.2 Why this fixes it

- Heading updates are not started before the user has granted location permission.
- Once permission is granted, both location and heading updates start.
- If the user later re-grants permission (e.g., after toggling in Settings), heading resumes.

---

## 4. Additional Diagnostics to Run on a Physical Device

Add temporary logging inside `PhoneSensorPublisher` to confirm the data path:

```swift
private func startHeading() {
    guard CLLocationManager.headingAvailable() else {
        print("[PhoneSensorPublisher] heading not available")
        return
    }
    print("[PhoneSensorPublisher] startUpdatingHeading")
    locationManager.headingOrientation = .landscapeRight
    locationManager.startUpdatingHeading()
}

nonisolated func locationManager(_ manager: CLLocationManager,
                                 didUpdateHeading newHeading: CLHeading) {
    print("[PhoneSensorPublisher] didUpdateHeading: \(newHeading.magneticHeading) acc=\(newHeading.headingAccuracy)")
    Task { @MainActor [weak self] in
        self?.latestHeadingDeg = newHeading.magneticHeading
        self?.latestHeadingAcc = newHeading.headingAccuracy
        self?.latestTrueHeadingDeg = newHeading.trueHeading >= 0 ? newHeading.trueHeading : nil
    }
}
```

Expected output on a physical device after granting permission:

```
[PhoneSensorPublisher] startUpdatingHeading
[PhoneSensorPublisher] didUpdateHeading: 127.5 acc=10.0
[PhoneSensorPublisher] didUpdateHeading: 128.1 acc=10.0
...
```

If you see the first line but never the second, the device is not delivering heading updates — check physical device vs. simulator and that Location Services are enabled in Settings.

---

## 5. Backend Verification

Once the iPhone is sending data, confirm the backend receives it:

```bash
# On the Orin or via SSH
ssh orin 'curl -fsS http://localhost:8088/api/v1/status | jq .sensors.phone'
```

You should see non-null `heading_deg` and `heading_acc >= 0`.

If `heading_acc` is `-1`, the iPhone is sending the default invalid value — meaning `didUpdateHeading` is still not firing.

---

## 6. Watch-Compass Note

If you later decide you want compass data **from the Watch** instead of the phone, that is a separate, larger change. The legacy app did not do it; the current Watch only records motion heading to a JSONL file. This document focuses on the phone compass because that matches both the legacy behavior and your stated setup (phone stays on the base).

---

## 7. Recommended Next Step

1. Confirm you are testing on a **physical iPhone**, not the Simulator.
2. Apply the patch in section 3.1 to `PhoneSensorPublisher.swift`.
3. Add the temporary logging from section 4.
4. Build to device, grant location permission, and check `SensorsView`.
5. Once heading displays, proceed with the calibration integration described in `docs/reviews/2026-06-16-iphone-compass-to-base-station-proposal.md`.

No source files were modified in producing this diagnosis.
