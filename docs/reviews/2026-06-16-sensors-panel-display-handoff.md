# WaveCam — Phone-Sensor Diagnostic Panel: blank-readings handoff

**Date:** 2026-06-16
**For:** an independent coding agent / reviewer with **no repo access** — everything needed is inlined below.
**Ask:** diagnose why the iOS "Sensors" diagnostic panel shows `—` (blank) for the phone heading/altitude/POST-status **even though the backend `/status` clearly contains the data**, and why the phone's 1 Hz POST feed goes stale. Then propose the fix(es).

---

## 1. System in one paragraph

A native iOS/SwiftUI app (`WaveCam`) is the operator console for a Jetson-based auto-filming PTZ camera. An iPhone is mounted (MagSafe) on the camera rig. The app runs `PhoneSensorPublisher`, which at **1 Hz** POSTs the phone's CoreLocation heading/GPS + CoreMotion altitude to a **Python/FastAPI** backend at `POST /api/v1/sensors/phone`. The backend (`SensorHub`) caches the **latest** sample and re-exposes it inside `GET /api/v1/status` under `sensors.phone`. A read-only SwiftUI screen (`SensorsView`, under Tools → Sensors) renders that cached sample. The phone is on the **same Wi-Fi as the rig** (it does **not** need USB tether); the rig API is reachable at `http://<orin-lan-ip>:8088`.

Data flow:

```
iPhone CoreLocation/CoreMotion
   └─ PhoneSensorPublisher.publish()  (1 Hz, only when client.connected && mode==.live)
        └─ WaveCamClient.postPhoneSensor()  → POST /api/v1/sensors/phone
             └─ FastAPI route → PhoneSampleRequest (pydantic) → SensorHub.ingest() → caches latest PhoneSample
GET /api/v1/status  (app polls at 1 Hz via WaveCamClient.refresh())
   └─ build_sensors_snapshot(latest_sample, base_pos, reference_heading) → status["sensors"]
        └─ WaveCamClient decodes WCStatus.Sensors (tolerant Codable)
             └─ SensorsView renders client.status?.sensors
```

---

## 2. The problem

**Symptom A — the panel HEADING and POST-status rows are blank (`—`) on the operator's desk**, even though:

**Live `GET /api/v1/status` → `sensors` (captured moments before, from the real rig):**
```json
"phone": {
  "heading_deg": 223.869,         // magnetic heading — PRESENT
  "true_heading_deg": 233.163,    // true heading — PRESENT
  "heading_acc": 20.52,           // ±20° (good, on the desk)
  "lat": <redacted>, "lon": <redacted>, "h_acc": 9.87,
  "alt_m": 172.37, "alt_acc": 30.0, "baro_rel_m": 0.066,
  "age_sec": 161.4,               // <-- STALE: last successful POST was 161s ago
  "tripod_reference": false
}
"co_location": { "phone_base_dist_m": null, "at_rig": null, "basis": "no_base_fix" }
```

So the backend **has** a valid magnetic heading (`223.9°`) yet the panel's heading row renders `—`.

**Symptom B — the feed is not live.** `age_sec` does not stay near ~1 s; it climbs to 30–160 s and only occasionally resets. The phone posts intermittently even while the app is open. (The live MJPEG video and the 1 Hz `GET /status` are reported to work; only the `POST /sensors/phone` cadence is broken.)

### Two competing explanations the reviewer must disambiguate
The panel binds to `client.status?.sensors?.phone`. It can render `—` for two very different reasons:
1. **The running binary is NOT the latest build.** A fix was just installed (build 486) that *adds* a "Phone (magnetic)" row and a "Phone POST" row. If **both new rows are entirely absent** (not just blank), the device may still be running the **previous** binary (iOS dev installs don't always replace a resident app without a relaunch). The operator reported "they do not show," which is consistent with an old binary.
2. **The app isn't connected / the sensor sub-object is nil.** If `client.connected` is false, `publish()` returns early (no POST → `age_sec` climbs), and if `client.status` is nil/stale the whole `sensors` object is nil → every row blanks. The phone-side `lastPhoneSensorPostAt` would be nil → "Phone POST" shows "no attempt yet".

`age_sec = 161` while the app is foreground strongly implies `client.connected == false` at the moment of observation (publisher skips). **But** the backend still holds a 161-s-old valid heading, so a *correctly-built, correctly-decoding* panel should still display that stale heading rather than `—`. That contradiction is the crux: **is the device on build 486, and is the `sensors` object actually reaching `SensorsView`?**

---

## 3. Environment / constraints
- iPhone 15 Pro Max, iOS 26+. App is a personal dev build (free-ish provisioning), installed via `xcrun devicectl`.
- The phone is frequently picked up / moved, so heading is only meaningful when the operator declares it stationary. Heading accuracy on the mount has been observed ±42–85° (MagSafe ring magnets + a steel plate sit on the magnetometer); on the bare desk it was ±20°.
- `sensors.enabled` is true on the backend (samples were stored earlier in the session). Service restart count = 0.
- An earlier launch crash was already fixed by adding `NSMotionUsageDescription` (CMAltimeter requires it).

---

## 4. What has already been changed (and did NOT resolve the blank panel)
1. **SensorsView**: added a "Phone (magnetic)" row bound to `headingDeg` (always present) above the existing "Phone (true)" row, so the card shouldn't blank when only true-heading is absent. Added a "Phone POST" row in FRESHNESS bound to client-side `lastPhoneSensorPostOk`/`At`.
2. **PhoneSensorPublisher**: `didUpdateLocations` now drops `lat/lon/h_acc` when `horizontalAccuracy < 0` (so a negative `h_acc` can't 422 the whole sample), and `alt_m/alt_acc` when `verticalAccuracy < 0`.
3. **WaveCamClient.postPhoneSensor**: now records `lastPhoneSensorPostOk` / `lastPhoneSensorPostAt` (was `_ = try? await post(...)`, silently swallowing).

After build 486 install: operator reports the magnetic-heading row and the POST-status row **do not show**. (Hence the suspicion the new binary isn't running, OR `client.status.sensors` is nil because the app isn't connected.)

---

## 5. Prior independent review (4 models, blind) — for context
- **(A) blank compass — agreed by all:** `SensorsView` previously bound HEADING only to `trueHeadingDeg`; `fmtHeading` returns `—` on nil; magnetic `heading_deg` was never displayed. `trueHeading` is `nil` whenever iOS returns −1 (no calibration / no location fix). → addressed by change #1 above (pending verification).
- **(B) laggy feed — contested:** candidate causes were (i) `publish()` gated on `client.connected`, which only the status-GET loop sets, so transient GET failures skip posts [most consistent]; (ii) tether-reprobe every 15 s + `isWriteRouteFailoverAllowed` excluding `.timedOut` [real latent bug, but absent tether usually yields `.cannotConnectToHost`, which *does* fail over]; (iii) negative `magneticHeading` → 422 [false: magneticHeading is 0–359.9]; (iv) `.cannotConnectToHost` not failed over [false: it *is* whitelisted].
- **Latent bug confirmed:** any one out-of-bounds field 422s the **entire** idempotent POST (e.g. `h_acc: ge=0.0` when iOS sends a negative accuracy on an invalid fix). → addressed by change #2.
- **Non-bug (corrected):** an earlier hypothesis that `co_location:no_base_fix` despite `base_locked:true` was a defect is **wrong** — `base_locked` is the base-drift-gate flag (forced true when that feature is off), unrelated to whether `get_camera_position()` returns a live base fix. `no_base_fix` is correct when the base Wio isn't providing a stable position.

---

## 6. Specific questions for the reviewer
1. Given the backend `/status` contains a valid `heading_deg` but the panel shows `—`: what would make `client.status?.sensors?.phone` be `nil` (or `headingDeg` nil) **in the app** while the rig clearly returns it? Decode path? Connection state? Is the WCStatus tolerant `init(from:)` silently dropping `sensors`?
2. Why does `publish()` stop posting (`age_sec` climbs to 161 s) while the app is foreground? Is `client.connected` realistically false here, and if so why (the status GET supposedly works)? Is gating the sensor POST on the status-GET-derived `connected` flag the design flaw?
3. Is there a way the panel could render `—` for **all** rows that would also explain the **POST-status row being absent** — i.e. is the running binary actually build 486, or is this an install/relaunch artifact? How would you prove which build is running from code/behavior alone?
4. Does anything in the publisher lifecycle (`start()`/`stop()` on `scenePhase`, the `disconnectedSince`/`sensorIdleGrace` logic) leave it wedged after a background→foreground cycle on Wi-Fi-only?

---

## 7. Code (current, post-change)

The following sections inline the relevant source verbatim.

### WaveCamApp.swift — publisher lifecycle / scenePhase (full)
```swift
import SwiftUI
import WatchConnectivity

@main
struct WaveCamApp: App {
    @AppStorage(WaveCamDefaults.modeKey) private var modeRaw = WaveCamClient.Mode.live.rawValue
    @AppStorage(WaveCamDefaults.baseURLKey) private var legacyBaseURLString = WaveCamDefaults.baseURLString
    @AppStorage(WaveCamDefaults.tetherBaseURLKey) private var tetherBaseURLString = WaveCamDefaults.tetherBaseURLString
    @AppStorage(WaveCamDefaults.wifiBaseURLKey) private var wifiBaseURLString = WaveCamDefaults.wifiBaseURLString
    @AppStorage(WaveCamDefaults.mockFallbackKey) private var mockFallbackEnabled = false

    @State private var client = WaveCamClient(mode: .live)
    @Environment(\.scenePhase) private var scenePhase

    // Phase-3 T3.1: phone-on-tripod sensor publisher. Lifecycle follows the app
    // foreground state; the publisher posts unconditionally while foregrounded
    // (server ignores when sensors.enabled=false on the backend).
    @State private var sensorPublisher: PhoneSensorPublisher? = nil

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(client)
                .preferredColorScheme(.dark)
                .task {
                    // Apply persisted settings once at launch. Runtime changes go through
                    // ConnectionView.applySettings (the single configure path); we deliberately
                    // do NOT observe @AppStorage here, because writing those keys on Apply would
                    // re-fire client.configure redundantly (iOS review #8).
                    KeychainStore.migrateLegacyToken(legacyDefaultsKey: WaveCamDefaults.tokenKey)
                    applyStoredSettings()
                    // Activate WatchConnectivity receiver for incoming session JSONL files.
                    // Set the activation callback first so the broadcast fires once the
                    // session is .activated — on first launch broadcastWatchContext() is a
                    // no-op if called before activation completes (activationState=.notActivated).
                    WatchSessionReceiver.shared.onActivated = { [self] in
                        broadcastWatchContext()
                    }
                    WatchSessionReceiver.shared.activate()
                    await client.refresh()
                    // Start the sensor publisher after settings are applied so it inherits
                    // the configured client URL and mode.
                    let publisher = PhoneSensorPublisher(client: client)
                    sensorPublisher = publisher
                    publisher.start()
                }
                .onChange(of: scenePhase) { _, phase in
                    // Pause the 1Hz status poll in the background (beach battery);
                    // .inactive is transient (app switcher, Control Center) — ignore.
                    switch phase {
                    case .background:
                        client.setPollingActive(false)
                        sensorPublisher?.stop()
                    case .active:
                        client.setPollingActive(true)
                        sensorPublisher?.start()
                    default: break
                    }
                }
        }
    }

    private func applyStoredSettings() {
        let mode = WaveCamClient.Mode(rawValue: modeRaw) ?? .live
        let routeURLs = storedRouteURLs()
        let token = KeychainStore.load(account: KeychainStore.tokenAccount) ?? ""
        client.configure(
            mode: mode,
            tetherBaseURL: routeURLs.tether,
            wifiBaseURL: routeURLs.wifi,
            token: token,
            mockFallbackEnabled: mockFallbackEnabled
        )
    }

    /// Pushes the stored token + URLs to the paired watch so WatchClient has
    /// current credentials without requiring a manual Connection Settings Apply.
    private func broadcastWatchContext() {
        guard WCSession.isSupported(), WCSession.default.activationState == .activated else { return }
        let token = KeychainStore.load(account: KeychainStore.tokenAccount) ?? ""
        let ctx: [String: Any] = [
            "wavecam_auth_token": token,
            "wavecam_tether_url": tetherBaseURLString,
            "wavecam_wifi_url": wifiBaseURLString,
        ]
        try? WCSession.default.updateApplicationContext(ctx)
    }

    private func storedRouteURLs() -> (tether: URL, wifi: URL) {
        var tether = URL(string: tetherBaseURLString) ?? WaveCamDefaults.tetherBaseURL
        var wifi = URL(string: wifiBaseURLString) ?? WaveCamDefaults.wifiBaseURL

        if tetherBaseURLString == WaveCamDefaults.tetherBaseURLString,
           wifiBaseURLString == WaveCamDefaults.wifiBaseURLString,
           legacyBaseURLString != WaveCamDefaults.baseURLString,
           let legacyURL = URL(string: legacyBaseURLString) {
            if legacyBaseURLString == WaveCamDefaults.legacyLANBaseURLString ||
                legacyBaseURLString.contains("192.168.") {
                wifi = legacyURL
            } else {
                tether = legacyURL
            }
        }

        return (tether: tether, wifi: wifi)
    }
}
```

### PhoneSensorPublisher.swift — the 1 Hz publisher (full, post-change)
```swift
import CoreLocation
import CoreMotion
import Foundation

/// Phase-3 T3.1: phone-on-tripod sensor publisher.
///
/// While the app is foregrounded and connected to the Orin, this object
/// POSTs to /api/v1/sensors/phone at 1 Hz:
///   - CLLocationManager heading (magneticHeading + headingAccuracy)
///   - CLLocationManager location (lat/lon/horizontalAccuracy) — when-in-use
///   - bump=true when CMMotionManager detects userAcceleration magnitude >1.2g
///     sustained <0.3s (one-shot flag cleared after each POST)
///
/// The backend ignores posts when sensors.enabled=false, so the publisher
/// does not need to know the server-side toggle — it always posts when
/// connected in live mode.
///
/// Denial of location or heading permission degrades silently — we post
/// whatever fields are available. CLHeading.headingAccuracy < 0 signals
/// invalid heading to the backend per the iOS convention.
///
/// Called from WaveCamApp on scene phase changes; `start()` / `stop()`
/// are idempotent.
@MainActor
final class PhoneSensorPublisher: NSObject {

    // MARK: - Configuration

    /// Accelerometer update interval (20 Hz).
    private static let accelHz: Double = 20.0

    /// Bump threshold: userAcceleration magnitude in g-forces.
    private static let bumpThreshold: Double = 1.2

    /// Maximum bump duration to count as an impulse (not a sustained lean).
    private static let bumpMaxDuration: TimeInterval = 0.3

    /// Publish interval (1 Hz).
    private static let publishInterval: TimeInterval = 1.0

    // MARK: - State

    private let client: WaveCamClient
    private let locationManager = CLLocationManager()
    private let motionManager = CMMotionManager()

    private var publishTimer: Task<Void, Never>?
    private var running = false
    private var sensorsActive = false
    private var disconnectedSince: Date?

    /// Disconnected this long -> sensors stop (GPS Best + 20Hz motion are the
    /// battery cost; the 1Hz timer itself is negligible). Review 2026-06-12.
    private static let sensorIdleGrace: TimeInterval = 30

    // Latest sensor snapshots (written from callbacks, read on publish timer).
    // All callbacks and the timer run on MainActor so no explicit lock needed.
    private var latestHeadingDeg: Double? = nil
    private var latestHeadingAcc: Double = -1   // default: invalid
    private var latestLat: Double? = nil
    private var latestLon: Double? = nil
    private var latestHAcc: Double? = nil
    private var latestTrueHeadingDeg: Double? = nil
    private var latestAltM: Double? = nil
    private var latestAltAcc: Double? = nil
    private let altimeter = CMAltimeter()
    private var latestBaroRelM: Double? = nil

    // Bump detection state.
    private var bumpPending = false
    private var bumpStartTime: Date? = nil

    // MARK: - Init

    init(client: WaveCamClient) {
        self.client = client
        super.init()
        locationManager.delegate = self
    }

    // MARK: - Lifecycle

    func start() {
        guard !running else { return }
        running = true
        // Sensors start on the first connected tick and stop after a
        // disconnected grace period — no rig, no GPS/IMU battery burn.
        startPublishTimer()
    }

    func stop() {
        guard running else { return }
        running = false
        publishTimer?.cancel()
        publishTimer = nil
        stopSensors()
    }

    private func startSensors() {
        guard !sensorsActive else { return }
        sensorsActive = true
        startHeading()
        startLocation()
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

    // MARK: - Heading

    private func startHeading() {
        guard CLLocationManager.headingAvailable() else { return }
        // The phone mounts landscape on the rig; without this the heading is off by 90°.
        locationManager.headingOrientation = .landscapeRight
        locationManager.startUpdatingHeading()
    }

    // MARK: - Altimeter

    private func startAltimeter() {
        guard CMAltimeter.isRelativeAltitudeAvailable() else { return }
        altimeter.startRelativeAltitudeUpdates(to: .main) { [weak self] data, _ in
            guard let data else { return }
            self?.latestBaroRelM = data.relativeAltitude.doubleValue
        }
    }

    // MARK: - Location

    private func startLocation() {
        let status = locationManager.authorizationStatus
        switch status {
        case .notDetermined:
            // Request once; if denied the delegate will not start updates.
            locationManager.requestWhenInUseAuthorization()
        case .authorizedWhenInUse, .authorizedAlways:
            locationManager.desiredAccuracy = kCLLocationAccuracyBest
            locationManager.startUpdatingLocation()
        default:
            // Denied or restricted — degrade silently (no location fields posted).
            break
        }
    }

    // MARK: - Accelerometer

    private func startAccelerometer() {
        guard motionManager.isDeviceMotionAvailable else { return }
        motionManager.deviceMotionUpdateInterval = 1.0 / Self.accelHz
        // deviceMotion separates gravity properly — the |raw|-1g proxy missed
        // horizontal bumps that rotate rather than add to the gravity vector
        // (review 2026-06-12).
        motionManager.startDeviceMotionUpdates(to: .main) { [weak self] data, _ in
            guard let self, let data else { return }
            let ua = data.userAcceleration
            let userMag = sqrt(ua.x * ua.x + ua.y * ua.y + ua.z * ua.z)
            if userMag > Self.bumpThreshold {
                if self.bumpStartTime == nil {
                    self.bumpStartTime = Date()
                }
            } else {
                if let start = self.bumpStartTime {
                    let duration = Date().timeIntervalSince(start)
                    if duration < Self.bumpMaxDuration {
                        self.bumpPending = true
                    }
                }
                self.bumpStartTime = nil
            }
        }
    }

    // MARK: - 1 Hz publish

    private func startPublishTimer() {
        publishTimer?.cancel()
        publishTimer = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(Self.publishInterval))
                await self?.publish()
            }
        }
    }

    private func publish() async {
        guard client.connected else {
            if disconnectedSince == nil {
                disconnectedSince = Date()
            } else if Date().timeIntervalSince(disconnectedSince!) > Self.sensorIdleGrace {
                stopSensors()
            }
            return
        }
        disconnectedSince = nil
        startSensors()
        var body: [String: Any] = ["bump": bumpPending]
        bumpPending = false

        body["heading_deg"] = latestHeadingDeg
        body["heading_acc"] = latestHeadingAcc
        if let lat = latestLat { body["lat"] = lat }
        if let lon = latestLon { body["lon"] = lon }
        if let hAcc = latestHAcc { body["h_acc"] = hAcc }
        if let th = latestTrueHeadingDeg { body["true_heading_deg"] = th }
        if let alt = latestAltM { body["alt_m"] = alt }
        if let altAcc = latestAltAcc { body["alt_acc"] = altAcc }
        if let baro = latestBaroRelM { body["baro_rel_m"] = baro }

        await client.postPhoneSensor(body)
    }
}

// MARK: - CLLocationManagerDelegate

extension PhoneSensorPublisher: CLLocationManagerDelegate {

    nonisolated func locationManager(_ manager: CLLocationManager,
                                     didUpdateHeading newHeading: CLHeading) {
        // headingAccuracy < 0 means invalid — pass it through as-is so the backend
        // can distinguish "no calibration" from a valid low-accuracy reading.
        Task { @MainActor [weak self] in
            self?.latestHeadingDeg = newHeading.magneticHeading
            self?.latestHeadingAcc = newHeading.headingAccuracy
            // trueHeading < 0 means no magnetic calibration yet — treat as absent.
            self?.latestTrueHeadingDeg = newHeading.trueHeading >= 0 ? newHeading.trueHeading : nil
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager,
                                     didUpdateLocations locations: [CLLocation]) {
        guard let loc = locations.last else { return }
        Task { @MainActor [weak self] in
            guard let self else { return }
            // iOS sets horizontalAccuracy < 0 to flag an invalid fix, where lat/lon are
            // meaningless. A negative h_acc also violates the backend's ge=0 bound and 422s
            // the ENTIRE sample (dropping a valid heading with it), so drop the location
            // fields until the fix is valid rather than poison the whole POST.
            if loc.horizontalAccuracy >= 0 {
                self.latestLat = loc.coordinate.latitude
                self.latestLon = loc.coordinate.longitude
                self.latestHAcc = loc.horizontalAccuracy
            } else {
                self.latestLat = nil
                self.latestLon = nil
                self.latestHAcc = nil
            }
            // verticalAccuracy < 0 flags an invalid altitude.
            if loc.verticalAccuracy >= 0 {
                self.latestAltM = loc.altitude
                self.latestAltAcc = loc.verticalAccuracy
            } else {
                self.latestAltM = nil
                self.latestAltAcc = nil
            }
        }
    }

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
                // Denied or restricted — clear any stale position data.
                self.latestLat = nil
                self.latestLon = nil
                self.latestHAcc = nil
            }
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager,
                                     didFailWithError error: Error) {
        // Degrade silently — the publisher continues with whatever fields are valid.
    }
}
```

### SensorsView.swift — the panel (full, post-change)
```swift
import SwiftUI

/// Read-only phone-on-tripod diagnostic (Stage 1). Shows the phone sensors AS THE
/// RIG RECEIVED THEM (validates the whole pipeline) vs the Wio base, gated by the
/// at-rig co-location check. No corrective use.
struct SensorsView: View {
    @Environment(WaveCamClient.self) private var client
    private var sensors: WCStatus.Sensors? { client.status?.sensors }

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                mountBadge
                OperatorCard(title: "HEADING") {
                    // Magnetic heading is present on every sample once the magnetometer
                    // reports; true heading needs a location fix + calibration and is
                    // frequently absent. Show magnetic as the primary row so the card is
                    // never blank when valid compass data exists. heading_acc describes the
                    // magnetic reading and is the key viability number on a magnetic mount.
                    row("Phone (magnetic)", fmtHeading(sensors?.phone?.headingDeg,
                                                       acc: sensors?.phone?.headingAcc))
                    row("Phone (true)", fmtHeading(sensors?.phone?.trueHeadingDeg,
                                                   acc: sensors?.phone?.headingAcc))
                    row("Base", "— (no compass)")
                }
                OperatorCard(title: "HEADING BIAS (phone − calibrated)") {
                    row("Offset", fmtBias(sensors?.headingBiasDeg))
                }
                OperatorCard(title: "POSITION") {
                    row("Phone", fmtLatLon(sensors?.phone?.lat, sensors?.phone?.lon,
                                           acc: sensors?.phone?.hAcc, accUnit: "m"))
                    row("Base", fmtLatLon(sensors?.base?.lat, sensors?.base?.lon))
                    row("Phone↔base", fmtMeters(sensors?.coLocation?.phoneBaseDistM))
                }
                OperatorCard(title: "ALTITUDE") {
                    row("Phone GPS", fmtMeters(sensors?.phone?.altM, acc: sensors?.phone?.altAcc))
                    row("Phone baro (rel)", fmtMeters(sensors?.phone?.baroRelM))
                    row("Base", fmtMeters(sensors?.base?.altM))
                }
                OperatorCard(title: "FRESHNESS") {
                    row("Rig age (received)", fmtSec(sensors?.phone?.ageSec))
                    row("Phone POST", fmtPostStatus(client.lastPhoneSensorPostOk,
                                                    at: client.lastPhoneSensorPostAt))
                }
            }
            .padding(.horizontal, 16).padding(.vertical, 12)
        }
        .background(WC.bg.ignoresSafeArea())
    }

    @ViewBuilder private var mountBadge: some View {
        let at = sensors?.coLocation?.atRig
        let (txt, tint): (String, Color) =
            at == true ? ("PHONE MOUNTED ON RIG", WC.ok)
          : at == false ? ("PHONE NOT AT RIG — NOT A TRIPOD REFERENCE", WC.warn)
          : ("MOUNT UNCONFIRMED (no base fix)", WC.muted)
        Text(txt).font(WCFont.label).tracking(1.2).foregroundStyle(tint)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder private func row(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label).font(WCFont.body).foregroundStyle(WC.txt)
            Spacer()
            Text(value).font(WCFont.captionMono).foregroundStyle(WC.muted)
                .lineLimit(1).truncationMode(.middle)
        }
    }

    private func fmtHeading(_ d: Double?, acc: Double?) -> String {
        guard let d else { return "—" }
        let a: String
        if let acc, acc >= 0 { a = String(format: " ±%.0f°", acc) } else { a = " (invalid)" }
        return String(format: "%.1f°%@", d, a)
    }
    private func fmtBias(_ d: Double?) -> String {
        guard let d else { return "— (needs at-rig + a heading lock)" }
        return String(format: "%+.1f°", d)
    }
    private func fmtLatLon(_ la: Double?, _ lo: Double?, acc: Double? = nil, accUnit: String = "") -> String {
        guard let la, let lo else { return "—" }
        let a = acc.map { String(format: " ±%.0f%@", $0, accUnit) } ?? ""
        return String(format: "%.5f, %.5f%@", la, lo, a)
    }
    private func fmtMeters(_ m: Double?, acc: Double? = nil) -> String {
        guard let m else { return "—" }
        let a = acc.map { String(format: " ±%.0f", $0) } ?? ""
        return String(format: "%.1f m%@", m, a)
    }
    private func fmtSec(_ s: Double?) -> String { s.map { String(format: "%.1f s", $0) } ?? "—" }
    private func fmtPostStatus(_ ok: Bool?, at: Date?) -> String {
        guard let ok, let at else { return "— (no attempt yet)" }
        let age = max(0, Date().timeIntervalSince(at))
        return ok ? String(format: "ok · %.0fs ago", age)
                  : String(format: "FAILED · %.0fs ago", age)
    }
}

#Preview {
    SensorsView()
        .environment(WaveCamClient(mode: .mock))
        .preferredColorScheme(.dark)
}
```

### WaveCamClient.swift — Sensors Codable + WCStatus tolerant init (118-179)
```swift
    struct Sensors: Codable, Sendable {
        struct Phone: Codable, Sendable {
            var headingDeg: Double?
            var trueHeadingDeg: Double?
            var headingAcc: Double?
            var lat: Double?
            var lon: Double?
            var hAcc: Double?
            var altM: Double?
            var altAcc: Double?
            var baroRelM: Double?
            var ageSec: Double?
            var tripodReference: Bool?
            enum CodingKeys: String, CodingKey {
                case headingDeg = "heading_deg", trueHeadingDeg = "true_heading_deg"
                case headingAcc = "heading_acc", lat, lon, hAcc = "h_acc"
                case altM = "alt_m", altAcc = "alt_acc", baroRelM = "baro_rel_m"
                case ageSec = "age_sec", tripodReference = "tripod_reference"
            }
        }
        struct Base: Codable, Sendable {
            var lat: Double?
            var lon: Double?
            var altM: Double?
            enum CodingKeys: String, CodingKey { case lat, lon, altM = "alt_m" }
        }
        struct CoLocation: Codable, Sendable {
            var phoneBaseDistM: Double?
            var atRig: Bool?
            var basis: String?
            enum CodingKeys: String, CodingKey {
                case phoneBaseDistM = "phone_base_dist_m", atRig = "at_rig", basis
            }
        }
        var phone: Phone?
        var base: Base?
        var coLocation: CoLocation?
        var headingBiasDeg: Double?
        enum CodingKeys: String, CodingKey {
            case phone, base, coLocation = "co_location", headingBiasDeg = "heading_bias_deg"
        }
    }
}

// H4: tolerant decoding — a renamed/missing backend field must degrade one HUD
// value, not throw the whole /status decode and blank the app to OFFLINE.
// (Extensions preserve the synthesized memberwise inits.)
extension WCStatus {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        revision = try c.decodeIfPresent(Int.self, forKey: .revision) ?? 0
        timeUnixMs = try c.decodeIfPresent(Int.self, forKey: .timeUnixMs)
        session = try c.decodeIfPresent(Session.self, forKey: .session)
            ?? Session(state: "UNKNOWN", mode: nil, startedAtUnixMs: nil)
        safety = try c.decodeIfPresent(Safety.self, forKey: .safety)
            ?? Safety(killed: false, killReason: nil, lastKillAtUnixMs: nil)
        ptz = try c.decodeIfPresent(PTZ.self, forKey: .ptz)
            ?? PTZ(owner: "idle", enabled: nil, panTiltCmd: nil, zoomState: nil)
        tracking = try c.decodeIfPresent(Tracking.self, forKey: .tracking)
            ?? Tracking(locked: false, state: "UNKNOWN", confidence: 0, fps: 0,
                        hasColor: nil, hasPerson: nil, matched: nil)
        gps = try c.decodeIfPresent(GPS.self, forKey: .gps)
```

### WaveCamClient.swift — connection/state vars (916-946)
```swift
    var mode: Mode
    var baseURL: URL
    var tetherBaseURL: URL
    var wifiBaseURL: URL
    var token: String?
    var mockFallbackEnabled: Bool

    private(set) var status: WCStatus?
    private(set) var connected: Bool = false
    private(set) var activeRoute: ConnectionRoute = .offline
    private(set) var lastError: String?
    /// Last failed *command* (e.g. safety stop). Kept separate from `lastError` so the
    /// 1Hz status poll never wipes a failed-KILL message before the operator sees it (review #2).
    private(set) var lastCommandError: String?
    /// Last failed PTZ/control command. Separate from `lastError` so a transient status-poll
    /// failure can't paint a false "PTZ failed" banner on the PTZ screen (review #P1-C).
    private(set) var lastControlError: String?

    /// Result + time of the most recent phone-sensor POST (T3.1 diagnostic). The backend's
    /// sensors.age_sec alone can't distinguish "app stopped posting" from "app posting but
    /// backend dropping the sample" — this surfaces the client side of that. nil until the
    /// first attempt.
    private(set) var lastPhoneSensorPostOk: Bool?
    private(set) var lastPhoneSensorPostAt: Date?

    /// Optimistic local KILL latch: set the instant the operator hits Emergency Stop so the
    /// latch overlay appears immediately, before the ~1Hz poll round-trips (review: optimistic KILL).
    private(set) var optimisticKilled = false

    /// True while a KILL request is in flight and the backend has not yet confirmed it.
    /// Gates `refresh()` so a poll returning killed==false cannot prematurely clear the
```

### WaveCamClient.swift — configure/refresh/startPolling/setPollingActive (989-1065)
```swift
    func configure(mode: Mode,
                   tetherBaseURL: URL,
                   wifiBaseURL: URL,
                   token: String?,
                   mockFallbackEnabled: Bool) {
        self.mode = mode
        self.tetherBaseURL = tetherBaseURL
        self.wifiBaseURL = wifiBaseURL
        self.baseURL = tetherBaseURL
        self.token = normalizedToken(token)
        self.mockFallbackEnabled = mockFallbackEnabled
        self.activeRoute = mode == .mock ? .mock : .offline
        if mode == .live { startPolling() } else { stopPolling() }
    }

    // MARK: status

    func refresh() async {
        if mode == .mock {
            status = .mockTracking(killed: mockKilled)
            connected = true
            activeRoute = .mock
            lastError = nil
            return
        }
        do {
            let data = try await getWithFallback("status")
            status = try Self.decoder.decode(WCStatus.self, from: data)
            connected = true
            lastError = nil
            if status?.safety.killed == true {
                // Backend confirmed the kill — safe to drop the optimistic latch.
                optimisticKilled = false
                killInFlight = false
            } else if !killInFlight {
                // No pending kill request; fresh status is authoritative.
                optimisticKilled = false
            }
            // When killInFlight==true and killed==false the latch stays set:
            // the kill POST is still in flight and the UI must not falsely clear.
        } catch {
            if mockFallbackEnabled {
                status = .mockTracking(killed: mockKilled)
                connected = false
                activeRoute = .mockFallback
                lastError = "Live API failed; showing mock data: \(error.localizedDescription)"
                return
            }
            connected = false
            activeRoute = .offline
            lastError = error.localizedDescription
        }
    }

    /// ~1Hz status polling so the HUD reflects live Orin state without a manual
    /// refresh (review #4). Driven by `configure`: active in `.live`, idle in `.mock`.
    private func startPolling(interval: Duration = .seconds(1)) {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refresh()
                try? await Task.sleep(for: interval)
            }
        }
    }

    private func stopPolling() {
        pollTask?.cancel()
        pollTask = nil
    }

    /// Scene-phase hook: the 1Hz poll has no business running while the app is
    /// backgrounded on a beach battery. Live mode only — mock never polls.
    func setPollingActive(_ active: Bool) {
        guard mode == .live else { return }
        if active { startPolling() } else { stopPolling() }
    }
```

### WaveCamClient.swift — postPhoneSensor (1668-1677)
```swift
    func postPhoneSensor(_ body: [String: Any]) async {
        guard mode == .live else { return }
        do {
            _ = try await post("sensors/phone", body: body)
            lastPhoneSensorPostOk = true
        } catch {
            lastPhoneSensorPostOk = false
        }
        lastPhoneSensorPostAt = Date()
    }
```

### WaveCamClient.swift — getWithFallback / candidateOrder / markConnected (1805-1900)
```swift
    private func getWithFallback(_ path: String, queryItems: [URLQueryItem] = []) async throws -> Data {
        var lastError: Error?
        for candidate in apiCandidates() {
            do {
                var url = candidate.appending(path: path)
                if !queryItems.isEmpty { url.append(queryItems: queryItems) }
                var req = URLRequest(url: url)
                req.timeoutInterval = 3
                authorize(&req)
                let (data, response) = try await URLSession.shared.data(for: req)
                markConnected(to: candidate)
                if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                    throw WaveCamAPIError(statusCode: http.statusCode, data: data)
                }
                return data
            } catch let error as WaveCamAPIError {
                throw error
            } catch {
                if let urlError = error as? URLError, !urlError.isReadRouteFailoverAllowed {
                    throw error
                }
                lastError = error
            }
        }
        throw lastError ?? URLError(.cannotConnectToHost)
    }

    private func apiCandidates() -> [URL] {
        deduped(candidateOrder())
    }

    private func candidateOrder(now: Date = Date()) -> [URL] {
        if activeRoute == .wifi || activeRoute == .custom {
            if now < nextTetherProbeAt {
                // Within the recheck window: stay on the known-good route and do NOT
                // probe the (usually-absent) tether subnet — otherwise every status
                // poll AND control POST blackholes on the tether read timeout. Tether
                // is retried once per tetherRecheckInterval when the window elapses.
                return [baseURL, wifiBaseURL]
            }
            nextTetherProbeAt = now.addingTimeInterval(tetherRecheckInterval)
        }
        return [tetherBaseURL, wifiBaseURL]
    }

    private func deduped(_ urls: [URL]) -> [URL] {
        var seen = Set<String>()
        return urls.filter { url in
            let key = url.absoluteString
            guard !seen.contains(key) else { return false }
            seen.insert(key)
            return true
        }
    }

    private func markConnected(to candidate: URL) {
        baseURL = candidate
        activeRoute = route(for: candidate)
        if activeRoute == .tether {
            nextTetherProbeAt = .distantPast
        }
    }

    private func route(for candidate: URL) -> ConnectionRoute {
        if candidate.absoluteString == tetherBaseURL.absoluteString { return .tether }
        if candidate.absoluteString == wifiBaseURL.absoluteString { return .wifi }
        return .custom
    }

    @discardableResult
    private func sendControl(_ path: String, body: [String: Any]) async -> Bool {
        do {
            if !connected {
                await refresh()
            }
            let data = try await post(path, body: body)
            if applyControlResponse(data) == false {
                return false
            }
            lastControlError = nil
            return true
        } catch let error as WaveCamAPIError {
            applyStatusIfPresent(error.data)
            lastControlError = error.localizedDescription
            return false
        } catch {
            lastControlError = error.localizedDescription
            return false
        }
    }

    private func applyControlResponse(_ data: Data) -> Bool {
        guard let response = try? Self.decoder.decode(WCControlResponse.self, from: data) else {
            refreshAfterLegacyResponse()
            lastControlError = "Control response was not parseable."
            return false
```

### WaveCamClient.swift — post() + failover predicates (1961-2016)
```swift
    private func post(_ path: String, body: [String: Any]) async throws -> Data {
        let payload = try JSONSerialization.data(withJSONObject: body)
        var failoverError: Error?
        // Mutating POSTs fail over only on *connection* errors. A reached server that
        // returns an HTTP error propagates immediately -- never re-send to another host
        // (would risk double-applying a command). Same candidates as getWithFallback.
        for candidate in apiCandidates() {
            do {
                var req = URLRequest(url: candidate.appending(path: path))
                req.httpMethod = "POST"
                req.timeoutInterval = 5
                req.setValue("application/json", forHTTPHeaderField: "Content-Type")
                authorize(&req)
                req.httpBody = payload
                let (data, response) = try await URLSession.shared.data(for: req)
                markConnected(to: candidate)
                if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                    throw WaveCamAPIError(statusCode: http.statusCode, data: data)
                }
                return data
            } catch let error as WaveCamAPIError {
                throw error
            } catch let error as URLError {
                guard error.isWriteRouteFailoverAllowed else { throw error }
                failoverError = error
            }
        }
        throw failoverError ?? URLError(.cannotConnectToHost)
    }
}

private extension URLError {
    var isReadRouteFailoverAllowed: Bool {
        // Reads are idempotent, so failing over after a post-send drop is harmless.
        switch code {
        case .timedOut, .networkConnectionLost, .notConnectedToInternet:
            return true
        default:
            return isWriteRouteFailoverAllowed
        }
    }

    var isWriteRouteFailoverAllowed: Bool {
        // Mutating POSTs fail over ONLY on pre-connection errors, where the server
        // provably never received the request. .timedOut / .networkConnectionLost /
        // .notConnectedToInternet can fire AFTER the server already acted, so retrying
        // to the other host would double-apply non-idempotent commands like
        // media/record/* or config/hot. (review C2)
        switch code {
        case .cannotConnectToHost, .cannotFindHost, .dnsLookupFailed:
            return true
        default:
            return false
        }
    }
}
```

### backend control_api.py — PhoneSampleRequest model (233-248)
```python
class PhoneSampleRequest(BaseModel):
    """POST /api/v1/sensors/phone — phone-on-tripod telemetry (Phase-3 T3.2).

    All fields are optional; the publisher sends whatever sensors are valid.
    heading_acc < 0 means the iOS heading is invalid (CLLocationManager convention).
    """
    heading_deg: float | None = Field(default=None, ge=0.0, le=360.0)
    heading_acc: float | None = Field(default=None, ge=-1.0, le=360.0)
    lat: float | None = Field(default=None, ge=-90.0, le=90.0)
    lon: float | None = Field(default=None, ge=-180.0, le=180.0)
    h_acc: float | None = Field(default=None, ge=0.0)
    bump: bool = False
    true_heading_deg: float | None = Field(default=None, ge=0.0, le=360.0)
    alt_m: float | None = Field(default=None)
    alt_acc: float | None = Field(default=None)
    baro_rel_m: float | None = Field(default=None)
```

### backend control_api.py — sensors/phone route (713-730)
```python
    @app.post("/api/v1/sensors/phone", dependencies=[Depends(require(READ))])
    def sensors_phone(req: PhoneSampleRequest):
        sample = PhoneSample(
            heading_deg=req.heading_deg,
            heading_acc=req.heading_acc,
            lat=req.lat,
            lon=req.lon,
            h_acc=req.h_acc,
            bump=req.bump,
            received_at=time.time(),
            true_heading_deg=req.true_heading_deg,
            alt_m=req.alt_m,
            alt_acc=req.alt_acc,
            baro_rel_m=req.baro_rel_m,
        )
        api.sensor_hub.ingest(sample)
        return {"ok": True, "request_id": make_request_id()}

```

### backend control_api.py — SensorHub base_pos construction (799-804)
```python
        self.sensor_hub = SensorHub(
            events=getattr(pipeline, "events", None),
            cfg=getattr(pipeline, "cfg", None),
            base_pos=(lambda: pipeline.gps.get_camera_position()
                      if getattr(pipeline, "gps", None) is not None else None),
        )
```

### backend sensor_hub.py — compute_at_rig / PhoneSample / SensorHub.__init__ (35-96)
```python
def compute_at_rig(
    phone_lat: Optional[float],
    phone_lon: Optional[float],
    base_pos: Optional[Tuple[float, float, float]],
    gate_m: float = AT_RIG_M,
) -> Tuple[Optional[bool], Optional[float], str]:
    """Return (at_rig, dist_m, basis).

    at_rig is True when the phone is within gate_m of the base, False when
    confirmed farther, and None when either fix is absent (unknown).
    Co-location only — proves the phone is NEAR the rig, not docked (Stage 2).
    """
    if phone_lat is None or phone_lon is None:
        return None, None, "no_phone_fix"
    if base_pos is None:
        return None, None, "no_base_fix"
    dist = haversine_m(base_pos[0], base_pos[1], phone_lat, phone_lon)
    return (dist <= gate_m), round(dist, 1), "gps_proximity"


@dataclass
class PhoneSample:
    """One inbound POST from the iOS publisher."""
    heading_deg: Optional[float]       # None → absent / not reported
    heading_acc: Optional[float]       # <0 → invalid (iOS convention)
    lat: Optional[float]
    lon: Optional[float]
    h_acc: Optional[float]
    bump: bool
    received_at: float                 # time.time() at ingest
    true_heading_deg: Optional[float] = None   # GPS-corrected; None → no fix/invalid
    alt_m: Optional[float] = None              # GPS altitude (m)
    alt_acc: Optional[float] = None            # vertical accuracy (m); <0 → invalid
    baro_rel_m: Optional[float] = None         # CMAltimeter relative altitude (m)


class SensorHub:
    """Lock-guarded cache of the latest phone sample plus alert state.

    No background threads. `ingest()` is called on the FastAPI request
    thread; `latest()` is a non-blocking snapshot read.
    """

    def __init__(self, events, cfg, base_pos=None) -> None:
        """
        events:   EventRing instance (or None in unit tests — hub skips recording).
        cfg:      live Config object; reads cfg.sensors.enabled / drift_alert_deg.
        base_pos: callable returning (lat, lon, alt) of the base Wio, or None
                  when unavailable.  Used by the at-rig gate to suppress monitors
                  when the phone is confirmed off-rig.
        """
        self._events = events
        self._cfg = cfg
        self._base_pos = base_pos
        self._lock = threading.Lock()

        # Latest sample (None until first POST).
        self._sample: Optional[PhoneSample] = None

        # Heading baseline: first valid heading sample after service start (or reset).
        self._heading_baseline: Optional[float] = None

```

### backend sensor_hub.py — ingest (113-143)
```python
    def ingest(self, sample: PhoneSample) -> None:
        """Called by the route handler with the decoded sample.

        Stores the sample, updates baseline, runs drift and bump monitors.
        No-ops if sensors.enabled is False (cheap kill-switch; route still 200s).
        """
        if not getattr(getattr(self._cfg, "sensors", None), "enabled", False):
            return

        # At-rig gate: resolve current base position and check co-location.
        # Suppress monitors ONLY when the phone is CONFIRMED off-rig (at_rig is
        # False).  Unknown (None) — no base fix or no phone fix — lets monitors
        # run so drift detection is not silently dropped on a base-GPS outage.
        # Never let a base-position read raise into the request thread (this runs in
        # the POST handler) — treat any failure as unknown (at_rig None ⇒ monitors run).
        try:
            base_pos = self._base_pos() if callable(self._base_pos) else None
        except Exception:
            base_pos = None
        at_rig, _, _ = compute_at_rig(sample.lat, sample.lon, base_pos)
        if at_rig is False:
            with self._lock:
                self._sample = sample   # still cache the latest sample
            return

        with self._lock:
            self._sample = sample
            self._update_baseline(sample)
            self._check_drift(sample)
            self._check_bump(sample)

```

### backend control_snapshots.py — build_sensors_snapshot (419-455)
```python
def build_sensors_snapshot(sample, base_pos, reference_heading=None,
                           now: float | None = None) -> dict:
    """Phone-on-tripod diagnostic block: phone (as the rig received it), the Wio
    base position, the co-location/at-rig gate, and the measured heading bias vs the
    calibrated reference. Read-only; no corrective use."""
    now = time.time() if now is None else now
    at_rig, dist_m, basis = compute_at_rig(
        getattr(sample, "lat", None), getattr(sample, "lon", None), base_pos
    )
    phone = None
    if sample is not None:
        phone = {
            "heading_deg": sample.heading_deg,
            "true_heading_deg": sample.true_heading_deg,
            "heading_acc": sample.heading_acc,
            "lat": sample.lat, "lon": sample.lon, "h_acc": sample.h_acc,
            "alt_m": sample.alt_m, "alt_acc": sample.alt_acc,
            "baro_rel_m": sample.baro_rel_m,
            "age_sec": round(now - sample.received_at, 1),
            "tripod_reference": (at_rig is True),
        }
    base = None
    if base_pos is not None:
        base = {"lat": base_pos[0], "lon": base_pos[1],
                "alt_m": base_pos[2] if len(base_pos) > 2 else None}
    # Measured fixed offset (steel-plate hard-iron + mount alignment) — only meaningful
    # when the phone is confirmed at the rig and a manual heading lock exists.
    heading_bias_deg = None
    th = getattr(sample, "true_heading_deg", None) if sample is not None else None
    if at_rig is True and th is not None and reference_heading is not None:
        heading_bias_deg = round(normalize_180(th - reference_heading), 1)
    return {
        "phone": phone,
        "base": base,
        "co_location": {"phone_base_dist_m": dist_m, "at_rig": at_rig, "basis": basis},
        "heading_bias_deg": heading_bias_deg,
    }
```
