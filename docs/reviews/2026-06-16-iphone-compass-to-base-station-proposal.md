# WaveCam iPhone Compass → Base Station — Legacy Review and Proposed Fix

**Date:** 2026-06-16  
**Scope:** Compare legacy `gps-relay-framework` compass/heading handling with current `ios/WaveCam` + `orin/wavecam`, identify why the base station is not using iPhone compass, and propose a minimal fix.  
**Constraint:** Read-only analysis; no project code was modified.

---

## 1. Executive Summary

**The current backend already accepts iPhone compass data.** `PhoneSensorPublisher` captures `CLHeading.magneticHeading` / `trueHeading` and streams them over `/api/v1/sensors/phone/ws`. The data appears in `/status.sensors.phone.heading_deg` and drives the heading-drift monitor.

**What is missing:** the backend never *uses* that compass reading for camera control or calibration. The legacy app streamed `heading_deg` in a `RelayUpdate`; the current backend receives the equivalent value but treats it as diagnostic-only. If the goal is for the base station to accept and act on iPhone compass data, the missing piece is wiring the stream into calibration/reference-heading, not fixing a transport failure.

**Recommended path:** add a one-tap "Capture phone compass heading" action in `CalibrateView` that posts the live `true_heading_deg` (or magnetic fallback) to the existing `POST /api/v1/calibration/heading` endpoint. This is the smallest change that makes the base station *accept and use* iPhone compass data, reuses the existing backend endpoint, and matches the legacy intent of using the phone-mounted compass as a tripod reference.

---

## 2. What the Legacy App Actually Did

### 2.1 Source of compass data

The legacy `gps-relay-framework` **did not get compass data from the Watch**. The watch code explicitly sets:

```swift
// Sources/WatchLocationProvider/WatchLocationProvider.swift:417
headingDegrees: nil,  // Apple Watch doesn't have compass
```

The iPhone-side `LocationRelayService` was the compass source:

- It calls `CLLocationManager.headingAvailable()` and `startUpdatingHeading()`.
- It stores `currentHeading: CLHeading?`.
- It resolves `trueHeading` (preferred) or `magneticHeading` fallback.
- It publishes a `LocationFix` with `headingDegrees` set, source `.iOS`.

### 2.2 Transport to the base station

The iPhone sent a `RelayUpdate` JSON/CBOR object over WebSocket to a standalone `orin-gps_server.py` (`apple-gps-cloudflare/orin-gps_server.py`). The payload contained:

```json
{
  "base": { "source": "iOS", "heading_deg": <compass>, ... },
  "remote": { "source": "watchOS", "heading_deg": null, ... }
}
```

The server decoded `heading_deg` and rebroadcast the message to local consumers, but **the provided server code does not act on the heading** (it only logs lat/lon/speed/course/accuracy). The legacy UI displayed the heading value (`ContentView.swift:220`), confirming the data reached the operator.

### 2.3 Key legacy assumption

The iPhone was physically mounted on the tripod/camera plate so that its compass axis aligned with the camera lens. That alignment is what made `heading_deg` useful as a tripod reference.

---

## 3. What the Current App Does

### 3.1 iPhone compass capture

`ios/WaveCam/Sources/PhoneSensorPublisher.swift`:

- Sets `locationManager.headingOrientation = .landscapeRight` (line 131).
- Calls `locationManager.startUpdatingHeading()` (line 132).
- Receives `didUpdateHeading` and stores:
  - `latestHeadingDeg = newHeading.magneticHeading`
  - `latestHeadingAcc = newHeading.headingAccuracy`
  - `latestTrueHeadingDeg = newHeading.trueHeading >= 0 ? trueHeading : nil`
- Publishes every second over the WebSocket to `ws/wss://<orin>/api/v1/sensors/phone/ws`.

`WaveCamApp.swift` creates and starts the publisher when the app becomes active (lines 43–45, 56).

### 3.2 Backend ingest

`orin/wavecam/wavecam/control_api.py`:

- `POST /api/v1/sensors/phone` and WebSocket `/api/v1/sensors/phone/ws` accept `heading_deg`, `heading_acc`, and `true_heading_deg`.
- The decoded sample is passed to `SensorHub.ingest()`.

`orin/wavecam/wavecam/sensor_hub.py`:

- Caches the latest sample.
- Uses `heading_deg` + `heading_acc` for **drift alerting only** (`_check_drift`).
- No-ops when `sensors.enabled` is false.

`orin/wavecam/wavecam/control_snapshots.py`:

- Exposes the sample in `/status.sensors.phone`.
- Computes `heading_bias_deg = true_heading_deg - reference_heading` when the phone is confirmed at the rig and a manual `reference_heading` exists.

`SensorsView.swift` displays these values.

### 3.3 Current calibration — does not use phone compass

`CalibrateView.swift` solves `reference_heading` from **GPS base→remote bearing**, not from the phone compass (line 419):

```swift
result = await client.captureCalibrationHeading(headingDeg: client.status?.gps?.bearingDeg ?? 0.0)
```

There is a separate endpoint `POST /api/v1/calibration/heading` (`WaveCamClient.swift:1238`) that accepts an arbitrary `heading_deg`, but the wizard never calls it with the live compass value.

---

## 4. Findings — Why It "Isn't Working"

| Claim | Finding | Confidence |
|---|---|---|
| "Current app isn't getting compass data from the Watch" | **Correct, but legacy didn't either.** The Watch has no live compass stream to the phone in either codebase. The current Watch records `CMDeviceMotion.heading` to a JSONL file for offline scoring only. | High |
| "Base station doesn't accept compass data from the iPhone" | **Incorrect as stated.** The base station accepts it via `/api/v1/sensors/phone/ws` and surfaces it in `/status`. | High |
| "Base station doesn't *use* compass data from the iPhone" | **Correct.** It is diagnostic-only (drift alert + UI). It is never fed into `CameraPose` or calibration. | High |
| "There is a transport/protocol gap" | **No.** Current WebSocket payload shape matches what the backend expects. | High |
| Potential iOS bug: heading may not restart after auth grant | `startHeading()` is called once in `startSensors()` and is **not** re-invoked from `locationManagerDidChangeAuthorization`. If the user grants location permission after `startSensors()` runs, heading updates may never start until the app is backgrounded/foregrounded. This would make `SensorsView` show blank heading even though the publisher is running. | Medium |

### 4.1 Files and lines verified

- Legacy iPhone compass source: `archive/legacy-20260606/gps-relay-framework/Sources/LocationRelayService/LocationRelayService.swift:703-780, 1053-1103`
- Legacy watch nil heading: `archive/legacy-20260606/gps-relay-framework/Sources/WatchLocationProvider/WatchLocationProvider.swift:417`
- Legacy server decode/rebroadcast: `archive/legacy-20260606/apple-gps-cloudflare/orin-gps_server.py:143-252, 388-435`
- Current iPhone publisher: `ios/WaveCam/Sources/PhoneSensorPublisher.swift:128-133, 210-215, 243-253`
- Current backend ingest: `orin/wavecam/wavecam/control_api.py:713-729, 736-779`
- Current backend use: `orin/wavecam/wavecam/sensor_hub.py:113-200`
- Current calibration (GPS bearing): `ios/WaveCam/Sources/CalibrateView.swift:419`
- Existing compass calibration endpoint: `ios/WaveCam/Sources/WaveCamClient.swift:1238-1253`, `orin/wavecam/wavecam/control_api.py:470-485`
- Calibration store saves `reference_heading`: `orin/wavecam/wavecam/calibration_store.py:31-32`
- Calibration heading step calibrates pan aim: `orin/wavecam/wavecam/control_calibration.py:761-770`

---

## 5. Proposed Solution

### 5.1 Goal

Make the base station **accept and use** the iPhone compass as the tripod reference heading, with the same physical assumption as the legacy app: the iPhone is mounted on the camera plate and aligned with the lens.

### 5.2 Option A — Recommended: CalibrateView "Capture Phone Compass" button

**iOS changes (no backend change required):**

1. In `CalibrateView`, expose a new action on the **Heading** step:
   - Button title: "Use Phone Compass" or "Capture Compass Heading".
   - Enabled only when `PhoneSensorPublisher` has a valid heading (`latestHeadingAcc >= 0`).
2. On tap, call the existing endpoint:
   ```swift
   let heading = publisher.latestTrueHeadingDeg ?? publisher.latestHeadingDeg
   guard let heading else { return }
   result = await client.captureCalibrationHeading(headingDeg: heading)
   ```
3. Send `source: "ios_compass"` in the request body so the backend can distinguish GPS-bearing captures from compass captures in the calibration log.
4. Show a warning if `latestHeadingAcc` is poor (e.g., > 15°) or if the phone is not confirmed at the rig (`status.sensors.co_location.at_rig != true`).

**Backend already supports this:** `POST /api/v1/calibration/heading` writes `heading_deg` to the calibration store and calls `CameraPose.calibrate_pan_aim()` with the current pan encoder.

**Pros:**
- Minimal code surface.
- Reuses existing, tested endpoint.
- Operator retains control over when the compass is sampled.
- Aligns with the legacy "phone-as-tripod-reference" model.

**Cons:**
- Requires the operator to tap the button.
- Assumes the phone is aligned with the camera; misalignment creates a fixed bias.

### 5.3 Option B — Automatic ingestion from the sensor stream

**Backend changes:**

1. Add a config key, e.g., `sensors.auto_reference_heading: bool` (default `false`).
2. In `SensorHub.ingest()`, when:
   - `sensors.enabled == true`
   - `auto_reference_heading == true`
   - phone is confirmed at rig (`at_rig == true`)
   - a calibration session is active (`pipeline.calibration_status().valid`)
   - sample has valid `true_heading_deg` (or `heading_deg` fallback)
   - heading accuracy is within a threshold (e.g., `heading_acc <= 10`)

   then call `pipeline.pose.set_reference_heading(sample.true_heading_deg)` and/or write a "heading" step to the calibration store.

**iOS changes:** none.

**Pros:**
- Fully automatic once enabled.

**Cons:**
- Hidden behavior; operator can't reject a bad compass reading.
- Risk of overwriting a GPS-derived reference_heading with a magnetometer value next to motors/steel.
- Larger backend change and needs tests.

### 5.4 Option C — Watch compass live stream

If the actual requirement is compass data **from the Watch** (e.g., the operator is holding the watch as a pointing device):

1. **Watch side:** add a live heading publisher in `WatchClient` or `WatchSessionRecorder` that reads `CMDeviceMotion.heading` at 1–4 Hz and sends it to the iPhone via `WCSession.sendMessage`.
2. **iPhone side:** receive the message in `WatchSessionReceiver` and forward it to the backend with a new field such as `watch_heading_deg` or a distinct `source: "watch_compass"`.
3. **Backend side:** extend `PhoneSampleRequest` / `PhoneSample` to accept `watch_heading_deg`, and add a separate gate in `SensorHub`.

**Pros:**
- Enables watch-as-compass use cases.

**Cons:**
- Apple Watch compass accuracy next to a PTZ motor/steel rig is unverified.
- Larger cross-component change.
- Legacy code did **not** do this, so it is not the shortest path to restore legacy behavior.

---

## 6. Recommended Implementation Order

1. **First, verify the existing stream is live.** Open `SensorsView` in the current app while the phone is on the rig and connected. If `Phone (magnetic)` stays blank:
   - Check that `NSLocationWhenInUseUsageDescription` is present (it is).
   - Fix the authorization race by re-calling `locationManager.startUpdatingHeading()` inside `locationManagerDidChangeAuthorization` when authorization is granted.
   - Confirm the WebSocket is connected (`POST status` row in `SensorsView`).
2. **Implement Option A** — add the "Capture Phone Compass" button in `CalibrateView` and call `/api/v1/calibration/heading`.
3. **Field test** the compass-derived `reference_heading` against the existing GPS-bearing method. Compare `status.sensors.heading_bias_deg`.
4. Only pursue Option B or C if Option A proves insufficient for the operational need.

---

## 7. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Phone not aligned with camera lens | Require landscape-right mount, document alignment, and show `heading_bias_deg` so operators can detect misalignment. |
| Magnetic interference from PTZ motors/steel | Use `trueHeading_deg` when available; warn if `heading_acc` is high; allow operator to reject the capture. |
| Authorization race prevents heading updates | Re-start heading updates in `locationManagerDidChangeAuthorization`. |
| Operator confuses GPS-bearing and compass capture | Label the two capture modes distinctly in the UI and log `source` in the backend calibration store. |

---

## 8. No-Code Verification Commands

These can be run against the live rig to confirm the current pipeline:

```bash
# 1. Confirm the sensor route exists and the websocket is reachable
curl -fsS http://<orin>:8088/api/v1/config | jq '.routes | contains(["/api/v1/sensors/phone"])'

# 2. Check that sensors.enabled is true
curl -fsS http://<orin>:8088/api/v1/config | jq '.sensors.enabled'

# 3. Open SensorsView and verify status.sensors.phone.heading_deg is non-null
curl -fsS http://<orin>:8088/api/v1/status | jq '.sensors.phone | {heading_deg, true_heading_deg, heading_acc}'

# 4. Check heading_bias_deg once a calibration reference_heading exists
curl -fsS http://<orin>:8088/api/v1/status | jq '.sensors.heading_bias_deg, .calibration.reference_heading'
```

---

## 9. Bottom Line

- **Legacy behavior:** iPhone compass was captured and streamed to the base station as `heading_deg`; the Watch was never the compass source.
- **Current state:** the iPhone compass is already captured and streamed to `/api/v1/sensors/phone/ws`; the base station accepts and displays it but does not use it for camera control.
- **Fix:** wire the live phone compass into the existing `POST /api/v1/calibration/heading` endpoint (Option A). This makes the base station accept *and use* iPhone compass data with the smallest change and lowest risk.

No source files were modified in producing this analysis.
