# Phone-on-Tripod Sensor Diagnostic (Stage 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only "Sensors" diagnostic that shows the rig-mounted phone's heading / GPS / **altitude** (with accuracy + freshness) side-by-side with the Wio base position, gated by a phone↔base "at-rig" check — the evidence step before any corrective use.

**Architecture:** The phone→rig pipeline already exists (`PhoneSensorPublisher` → `POST /api/v1/sensors/phone` → `SensorHub`, observe-only). This plan (a) adds altitude + true-heading to that POST/ingest, (b) computes an `at_rig` co-location gate from phone-GPS vs base-GPS and suppresses the observe-only monitors when the phone is confirmed *not* at the rig, (c) exposes a `sensors` block in `/api/v1/status`, and (d) adds a `SensorsView` as a 4th segment inside the existing Tools tab. Additive and read-only; no corrective behavior.

**Tech Stack:** Python/FastAPI + pydantic (backend, pytest), Swift/SwiftUI + CoreLocation/CoreMotion (iOS, no XCTest target — verified by device build).

**Spec:** `docs/superpowers/specs/2026-06-16-phone-sensor-diagnostic-design.md`
**Branch:** `feat/phone-sensor-diagnostic` (off `origin/main`). Backend tests run from `orin/wavecam/` (`python3 -m pytest -q`); mypy via `python3 -m mypy --config-file mypy.ini`.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `orin/wavecam/wavecam/sensor_hub.py` | modify | `PhoneSample` gains altitude/true-heading; new pure `compute_at_rig()`; `SensorHub` gains a `base_pos` provider + `at_rig` monitor gate |
| `orin/wavecam/wavecam/control_api.py` | modify | `PhoneSampleRequest` gains fields; route builds them; `SensorHub(...)` gets `base_pos`; adapter `status_snapshot()` merges the sensors block |
| `orin/wavecam/wavecam/control_snapshots.py` | modify | new pure `build_sensors_snapshot(sample, base_pos)` (phone / base / co_location) |
| `orin/wavecam/tests/test_sensor_hub.py` | create | unit tests for `compute_at_rig` + the gated monitors |
| `orin/wavecam/tests/test_control_api.py` | modify | `/status` exposes a `sensors` block with phone/base/co_location |
| `ios/WaveCam/Sources/PhoneSensorPublisher.swift` | modify | capture + POST true-heading, altitude, barometric; set `headingOrientation` |
| `ios/WaveCam/Sources/WaveCamClient.swift` | modify | decode the `sensors` block (`WCStatus.Sensors`) |
| `ios/WaveCam/Sources/SensorsView.swift` | create | the diagnostic panel |
| `ios/WaveCam/Sources/ToolsView.swift` | modify | add the `Sensors` segment |

---

## Task 1: Backend — `PhoneSample` + request carry altitude & true heading

**Files:**
- Modify: `orin/wavecam/wavecam/sensor_hub.py` (PhoneSample dataclass, ~lines 29-38)
- Modify: `orin/wavecam/wavecam/control_api.py` (PhoneSampleRequest ~232-243, route ~712-724)
- Test: `orin/wavecam/tests/test_control_api.py`

- [ ] **Step 1: Write the failing test** — create `orin/wavecam/tests/test_sensor_hub.py` (Task 2 extends this same file) with:

```python
def test_phone_sample_carries_altitude_and_true_heading():
    from wavecam.sensor_hub import PhoneSample
    s = PhoneSample(heading_deg=100.0, heading_acc=3.0, lat=21.0, lon=-157.0,
                    h_acc=4.0, bump=False, received_at=1.0,
                    true_heading_deg=102.5, alt_m=12.4, alt_acc=6.0, baro_rel_m=0.2)
    assert s.true_heading_deg == 102.5
    assert s.alt_m == 12.4 and s.alt_acc == 6.0 and s.baro_rel_m == 0.2
```

- [ ] **Step 2: Run it — expect FAIL.**
Run: `cd orin/wavecam && python3 -m pytest tests/test_sensor_hub.py::test_phone_sample_carries_altitude_and_true_heading -q`
Expected: FAIL — `PhoneSample.__init__() got an unexpected keyword argument 'true_heading_deg'`.

- [ ] **Step 3: Add the fields** — in `sensor_hub.py`, extend the dataclass (keep `received_at` last so positional callers still work; new optionals default `None`):

```python
@dataclass
class PhoneSample:
    """One inbound POST from the iOS publisher."""
    heading_deg: Optional[float]       # magnetic, deg; None → absent
    heading_acc: Optional[float]       # <0 → invalid (iOS convention)
    lat: Optional[float]
    lon: Optional[float]
    h_acc: Optional[float]
    bump: bool
    received_at: float                 # time.time() at ingest
    true_heading_deg: Optional[float] = None   # GPS-corrected; None → no fix/invalid
    alt_m: Optional[float] = None              # GPS altitude (m, ellipsoidal/MSL per iOS)
    alt_acc: Optional[float] = None            # vertical accuracy (m); <0 → invalid
    baro_rel_m: Optional[float] = None         # CMAltimeter relative altitude (m)
```

- [ ] **Step 4: Carry them through the request + route** — in `control_api.py`, extend `PhoneSampleRequest`:

```python
class PhoneSampleRequest(BaseModel):
    heading_deg: float | None = Field(default=None, ge=0.0, le=360.0)
    heading_acc: float | None = Field(default=None, ge=-1.0, le=360.0)
    true_heading_deg: float | None = Field(default=None, ge=0.0, le=360.0)
    lat: float | None = Field(default=None, ge=-90.0, le=90.0)
    lon: float | None = Field(default=None, ge=-180.0, le=180.0)
    h_acc: float | None = Field(default=None, ge=0.0)
    alt_m: float | None = Field(default=None)
    alt_acc: float | None = Field(default=None)
    baro_rel_m: float | None = Field(default=None)
    bump: bool = False
```

and the route body (`sensors_phone`):

```python
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
```

- [ ] **Step 5: Run the unit test — expect PASS.** Run: `cd orin/wavecam && python3 -m pytest tests/test_control_api.py::test_phone_sample_carries_altitude_and_true_heading -q` → PASS.

- [ ] **Step 6: Commit**

```bash
git add orin/wavecam/wavecam/sensor_hub.py orin/wavecam/wavecam/control_api.py orin/wavecam/tests/test_sensor_hub.py
git commit -m "feat(sensors): carry altitude + true heading through phone ingest"
```

---

## Task 2: Backend — `compute_at_rig` + gate the observe-only monitors

**Files:**
- Modify: `orin/wavecam/wavecam/sensor_hub.py` (new pure fn + `__init__` `base_pos` + `ingest` gate)
- Modify: `orin/wavecam/wavecam/control_api.py` (pass `base_pos` to `SensorHub(...)`, ~785-788)
- Test: `orin/wavecam/tests/test_sensor_hub.py` (append — created in Task 1)

**Semantics:** `compute_at_rig` returns `(at_rig: Optional[bool], dist_m, basis)`. `True` = phone within `AT_RIG_M` of base; `False` = confirmed far; `None` = unknown (no base or no phone fix). The monitor gate suppresses **only when `at_rig is False`** — so a rig with no base GPS fix still runs drift detection (preserves today's behavior).

- [ ] **Step 1: Write the failing test** — append to `orin/wavecam/tests/test_sensor_hub.py` (add these imports at the top of the file created in Task 1):

```python
"""SensorHub at-rig co-location gate (transport != mounted)."""
import types
from wavecam.sensor_hub import PhoneSample, SensorHub, compute_at_rig, AT_RIG_M


def test_compute_at_rig_near_far_unknown():
    base = (21.0, -157.0, 5.0)
    near, d_near, basis_near = compute_at_rig(21.00001, -157.00001, base)
    far, d_far, basis_far = compute_at_rig(21.05, -157.05, base)       # ~7 km
    unk, d_unk, basis_unk = compute_at_rig(21.0, -157.0, None)
    none_phone, _, basis_np = compute_at_rig(None, None, base)
    assert near is True and d_near < AT_RIG_M and basis_near == "gps_proximity"
    assert far is False and d_far > AT_RIG_M
    assert unk is None and basis_unk == "no_base_fix"
    assert none_phone is None and basis_np == "no_phone_fix"


def _hub(base_pos, drift_alert_deg=12.0):
    events = types.SimpleNamespace(emit=lambda *a, **k: events_log.append((a, k)))
    cfg = types.SimpleNamespace(sensors=types.SimpleNamespace(enabled=True, drift_alert_deg=drift_alert_deg))
    return SensorHub(events=events, cfg=cfg, base_pos=base_pos), cfg


events_log: list = []


def test_monitors_suppressed_only_when_confirmed_off_rig():
    events_log.clear()
    base = (21.0, -157.0, 5.0)
    # Phone ~7 km from base → at_rig False → bump must NOT emit.
    hub_far, _ = _hub(lambda: base)
    hub_far.ingest(PhoneSample(heading_deg=10.0, heading_acc=2.0, lat=21.05, lon=-157.05,
                               h_acc=4.0, bump=True, received_at=1.0))
    assert events_log == []          # off-rig bump suppressed
    # No base fix → at_rig None → bump SHOULD emit (don't lose drift/bump when base absent).
    events_log.clear()
    hub_nobase, _ = _hub(lambda: None)
    hub_nobase.ingest(PhoneSample(heading_deg=10.0, heading_acc=2.0, lat=21.0, lon=-157.0,
                                  h_acc=4.0, bump=True, received_at=2.0))
    assert len(events_log) == 1      # bump still fires when at-rig unknown
```

(Adjust the `events.emit` shape in `_hub` to match the real `EventRing` method the existing monitors call — read `sensor_hub.py` `_check_bump` to confirm the method name, and mirror it.)

- [ ] **Step 2: Run it — expect FAIL.** Run: `cd orin/wavecam && python3 -m pytest tests/test_sensor_hub.py -q`
Expected: FAIL — `ImportError: cannot import name 'compute_at_rig'` / `SensorHub.__init__() got an unexpected keyword argument 'base_pos'`.

- [ ] **Step 3: Implement** — in `sensor_hub.py`, add the pure helper near the top (after imports; reuse the existing `haversine_m`):

```python
from .gps_geo import haversine_m

AT_RIG_M = 15.0   # phone within this of the base ⇒ treated as co-located with the rig


def compute_at_rig(phone_lat, phone_lon, base_pos, gate_m: float = AT_RIG_M):
    """(at_rig, dist_m, basis). at_rig is True/False when both fixes exist, else None.

    Co-location only — proves the phone is *near* the rig, NOT that it is docked
    in the mount (a handheld phone at the tripod also passes). Docked confirmation
    (motion-stillness) is Stage 2.
    """
    if phone_lat is None or phone_lon is None:
        return None, None, "no_phone_fix"
    if base_pos is None:
        return None, None, "no_base_fix"
    dist = haversine_m(base_pos[0], base_pos[1], phone_lat, phone_lon)
    return (dist <= gate_m), round(dist, 1), "gps_proximity"
```

Extend `__init__` to accept the provider (default `None` keeps existing tests/callers working):

```python
    def __init__(self, events, cfg, base_pos=None) -> None:
        ...
        self._base_pos = base_pos      # callable -> (lat, lon, alt) | None, or None
```

Gate the monitors in `ingest()` (record the sample always; suppress monitors only when confirmed off-rig):

```python
    def ingest(self, sample: PhoneSample) -> None:
        if not getattr(getattr(self._cfg, "sensors", None), "enabled", False):
            return
        base_pos = self._base_pos() if callable(self._base_pos) else None
        at_rig, _dist, _basis = compute_at_rig(sample.lat, sample.lon, base_pos)
        with self._lock:
            self._sample = sample
            if at_rig is False:
                return                 # phone confirmed off-rig: not anchor drift; skip monitors
            self._update_baseline(sample)
            self._check_drift(sample)
            self._check_bump(sample)
```

- [ ] **Step 4: Wire the provider** — in `control_api.py`, the `SensorHub(...)` construction (~785-788):

```python
        self.sensor_hub = SensorHub(
            events=getattr(pipeline, "events", None),
            cfg=getattr(pipeline, "cfg", None),
            base_pos=(lambda: pipeline.gps.get_camera_position()
                      if getattr(pipeline, "gps", None) is not None else None),
        )
```

- [ ] **Step 5: Run tests — expect PASS.** Run: `cd orin/wavecam && python3 -m pytest tests/test_sensor_hub.py -q` → PASS. Then the full suite + mypy:
`python3 -m pytest -q` (all pass) and `python3 -m mypy --config-file mypy.ini` (clean).

- [ ] **Step 6: Commit**

```bash
git add orin/wavecam/wavecam/sensor_hub.py orin/wavecam/wavecam/control_api.py orin/wavecam/tests/test_sensor_hub.py
git commit -m "feat(sensors): at-rig co-location gate; suppress monitors only when confirmed off-rig"
```

---

## Task 3: Backend — expose the `sensors` block in `/status`

**Files:**
- Modify: `orin/wavecam/wavecam/control_snapshots.py` (new `build_sensors_snapshot`)
- Modify: `orin/wavecam/wavecam/control_api.py` (adapter `status_snapshot()` merges it, ~799-800)
- Test: `orin/wavecam/tests/test_control_api.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_control_api.py`:

```python
def test_status_exposes_sensors_block():
    # Base position present + an ingested phone sample near it → at_rig true.
    pipe = DummyPipeline()
    pipe.cfg.sensors.enabled = True
    pipe.gps = types.SimpleNamespace(
        get_camera_position=lambda: (21.0, -157.0, 5.0),
        get_camera_age=lambda: 1.0,
        reader_alive=lambda: True, last_poll_age_sec=lambda: 0.5,
        get_target_telemetry=lambda: {},
    )
    pipe._store = types.SimpleNamespace(reference_heading=100.0)
    app = build_app(pipe)
    client = TestClient(app)
    client.post("/api/v1/sensors/phone", json={
        "heading_deg": 100.0, "heading_acc": 3.0, "true_heading_deg": 101.0,
        "lat": 21.00001, "lon": -157.00001, "h_acc": 4.0,
        "alt_m": 12.0, "alt_acc": 6.0, "bump": False,
    })
    body = client.get("/api/v1/status").json()
    s = body["sensors"]
    assert s["phone"]["true_heading_deg"] == 101.0
    assert s["phone"]["alt_m"] == 12.0
    assert s["base"]["lat"] == 21.0
    assert s["co_location"]["at_rig"] is True
    assert s["co_location"]["basis"] == "gps_proximity"
    assert s["phone"]["tripod_reference"] is True
    assert s["heading_bias_deg"] == 1.0   # phone true 101.0 − reference 100.0
```

- [ ] **Step 2: Run it — expect FAIL.** Run: `cd orin/wavecam && python3 -m pytest tests/test_control_api.py::test_status_exposes_sensors_block -q`
Expected: FAIL — `KeyError: 'sensors'`.

- [ ] **Step 3: Implement `build_sensors_snapshot`** — add to `control_snapshots.py` (import the helper from sensor_hub):

```python
from .sensor_hub import compute_at_rig
from .gps_geo import normalize_180


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

- [ ] **Step 4: Merge it into the adapter status** — in `control_api.py`, change `status_snapshot()`:

```python
    def status_snapshot(self) -> dict:
        snap = build_status_snapshot(self.pipeline, self.revision, self.media.status())
        base_pos = (self.pipeline.gps.get_camera_position()
                    if getattr(self.pipeline, "gps", None) is not None else None)
        ref = getattr(getattr(self.pipeline, "_store", None), "reference_heading", None)
        snap["sensors"] = build_sensors_snapshot(self.sensor_hub.latest(), base_pos, ref)
        return snap
```

Add `build_sensors_snapshot` to the existing `from .control_snapshots import (...)` import block.

- [ ] **Step 5: Run tests — expect PASS.** Run: `cd orin/wavecam && python3 -m pytest tests/test_control_api.py::test_status_exposes_sensors_block -q` → PASS. Then `python3 -m pytest -q` + `python3 -m mypy --config-file mypy.ini` clean.

- [ ] **Step 6: Commit**

```bash
git add orin/wavecam/wavecam/control_snapshots.py orin/wavecam/wavecam/control_api.py orin/wavecam/tests/test_control_api.py
git commit -m "feat(sensors): expose phone/base/at-rig sensors block in /status"
```

---

## Task 4: iOS — publish true heading, altitude, barometric

**Files:**
- Modify: `ios/WaveCam/Sources/PhoneSensorPublisher.swift`

No XCTest target exists; verification is a device build (`./build-device.sh build` → `** BUILD SUCCEEDED **`).

- [ ] **Step 1: Add stored properties** (after the existing `latestHAcc`):

```swift
    private var latestTrueHeadingDeg: Double? = nil
    private var latestAltM: Double? = nil
    private var latestAltAcc: Double? = nil
    private let altimeter = CMAltimeter()
    private var latestBaroRelM: Double? = nil
```

- [ ] **Step 2: Set landscape heading orientation + start the altimeter** — in `startHeading()` set the orientation to the mount's landscape variant, and add an altimeter start in `startSensors()`:

```swift
    private func startHeading() {
        guard CLLocationManager.headingAvailable() else { return }
        // Phone mounts in landscape on the rig; without this every heading is off 90°.
        // .landscapeRight = home/Dynamic-Island on the LEFT (confirm against the mount).
        locationManager.headingOrientation = .landscapeRight
        locationManager.startUpdatingHeading()
    }

    private func startAltimeter() {
        guard CMAltimeter.isRelativeAltitudeAvailable() else { return }
        altimeter.startRelativeAltitudeUpdates(to: .main) { [weak self] data, _ in
            guard let data else { return }
            self?.latestBaroRelM = data.relativeAltitude.doubleValue
        }
    }
```

Call `startAltimeter()` from `startSensors()` (alongside `startHeading()/startLocation()/startAccelerometer()`), and stop it in `stopSensors()` with `altimeter.stopRelativeAltitudeUpdates()`.

- [ ] **Step 3: Capture true heading + altitude in the delegates:**

```swift
    nonisolated func locationManager(_ manager: CLLocationManager,
                                     didUpdateHeading newHeading: CLHeading) {
        Task { @MainActor [weak self] in
            self?.latestHeadingDeg = newHeading.magneticHeading
            self?.latestHeadingAcc = newHeading.headingAccuracy
            // trueHeading is -1 until there is a location fix for declination.
            self?.latestTrueHeadingDeg = newHeading.trueHeading >= 0 ? newHeading.trueHeading : nil
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager,
                                     didUpdateLocations locations: [CLLocation]) {
        guard let loc = locations.last else { return }
        Task { @MainActor [weak self] in
            self?.latestLat = loc.coordinate.latitude
            self?.latestLon = loc.coordinate.longitude
            self?.latestHAcc = loc.horizontalAccuracy
            self?.latestAltM = loc.altitude
            self?.latestAltAcc = loc.verticalAccuracy
        }
    }
```

- [ ] **Step 4: Add the fields to the POST body** in `publish()` (after the existing `h_acc`):

```swift
        if let th = latestTrueHeadingDeg { body["true_heading_deg"] = th }
        if let alt = latestAltM { body["alt_m"] = alt }
        if let altAcc = latestAltAcc { body["alt_acc"] = altAcc }
        if let baro = latestBaroRelM { body["baro_rel_m"] = baro }
```

- [ ] **Step 5: Build to verify it compiles.**
Run: `cd ios/WaveCam && ./build-device.sh build`
Expected: `** BUILD SUCCEEDED **` (if the device is unavailable, the build still compiles the app target; a sim build will compile Swift and fail only at the watch AppIcon C1 — that's acceptable, it proves the Swift compiles).

- [ ] **Step 6: Commit**

```bash
git add ios/WaveCam/Sources/PhoneSensorPublisher.swift
git commit -m "feat(ios): publish true heading + altitude + barometric from the rig phone"
```

---

## Task 5: iOS — decode the `sensors` block

**Files:**
- Modify: `ios/WaveCam/Sources/WaveCamClient.swift` (add `WCStatus.Sensors`; decode it)

- [ ] **Step 1: Add the nested structs** near the other `WCStatus` nested types (e.g. beside the `GPS` struct ~line 93). Use snake_case `CodingKeys` to match the backend:

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
```

- [ ] **Step 2: Add the property + tolerant decode** — add `var sensors: Sensors?` to `WCStatus`, add `case sensors` to its `CodingKeys`, and in the tolerant `init(from:)` (~line 122):

```swift
        sensors = try c.decodeIfPresent(Sensors.self, forKey: .sensors)
```

- [ ] **Step 3: Build to verify.**
Run: `cd ios/WaveCam && ./build-device.sh build` → `** BUILD SUCCEEDED **`.

- [ ] **Step 4: Commit**

```bash
git add ios/WaveCam/Sources/WaveCamClient.swift
git commit -m "feat(ios): decode the /status sensors block"
```

---

## Task 6: iOS — `SensorsView` + Tools "Sensors" segment

**Files:**
- Create: `ios/WaveCam/Sources/SensorsView.swift`
- Modify: `ios/WaveCam/Sources/ToolsView.swift` (add the segment)

- [ ] **Step 1: Create `SensorsView.swift`** (matches the existing `OperatorCard`/`infoRow`/`WCFont` patterns):

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
                    row("Phone GPS", fmtMeters(sensors?.phone?.altM,
                                               acc: sensors?.phone?.altAcc))
                    row("Phone baro (rel)", fmtMeters(sensors?.phone?.baroRelM))
                    row("Base", fmtMeters(sensors?.base?.altM))
                }
                OperatorCard(title: "FRESHNESS") {
                    row("Phone age", fmtSec(sensors?.phone?.ageSec))
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
        let a = (acc ?? -1) >= 0 ? String(format: " ±%.0f°", acc!) : " (invalid)"
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
}
```

(If `WC.warn`/`WC.ok` names differ, use the exact tokens from `Theme+Glass.swift` — read them and substitute.)

- [ ] **Step 2: Add the Tools segment** — in `ToolsView.swift`, extend the enum and the switch:

```swift
    private enum Tool: String, CaseIterable, Hashable {
        case tune = "Tune"
        case sensors = "Sensors"
        case agent = "Agent"
        case log = "Log"
    }
```

```swift
            switch selectedTool {
            case .tune: TuneView()
            case .sensors: SensorsView()
            case .agent: AgentView()
            case .log: SessionLogView()
            }
```

- [ ] **Step 3: Regenerate the project (new file) + build.**
Run: `cd ios/WaveCam && xcodegen generate && ./build-device.sh build`
Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 4: Commit**

```bash
git add ios/WaveCam/Sources/SensorsView.swift ios/WaveCam/Sources/ToolsView.swift
git commit -m "feat(ios): Sensors diagnostic panel as a Tools segment"
```

---

## Task 7: Integration verify on the live rig

**Files:** none (verification only).

- [ ] **Step 1: Deploy the backend (rig must be idle).** Confirm `owner: idle` first:
`ssh orin 'curl -s localhost:8088/api/v1/status'` — check not TRACKING. Then `./orin/wavecam/deploy.sh` and confirm `DEPLOY OK`.

- [ ] **Step 2: Verify the sensors block end-to-end.**
Run: `ssh orin 'curl -s localhost:8088/api/v1/status -o /tmp/s.json; grep -o "\"sensors\":{.*" /tmp/s.json | head -c 600'`
Expected: a `sensors` object with `phone`, `base`, `co_location` (and `at_rig` reflecting whether the phone is mounted right now).

- [ ] **Step 3: Install iOS + eyeball.** `cd ios/WaveCam && ./build-device.sh` (device unlocked). Open Tools → Sensors. With the phone mounted: badge "PHONE MOUNTED ON RIG", heading ±acc shown, phone↔base distance ≈ 0. Walk the phone away (or test on home Wi-Fi): badge flips to "NOT AT RIG".

- [ ] **Step 4: Final commit / branch state.** No code change; record the verification in the PR description when raising `feat/phone-sensor-diagnostic`.

---

## Notes for the implementer

- **Re-read before editing each file** (anti-vibe drift control) — line numbers above are from exploration and may shift.
- **`events.emit` shape (Task 2 test):** confirm the real `EventRing` method the existing `_check_bump`/`_check_drift` call and mirror it in the test stub (read `sensor_hub.py`).
- **Heading orientation (Task 4):** `.landscapeRight` is the starting assumption — the on-device heading-bias readout vs a known bearing will reveal a 90°/180° error; flip the variant if so.
- **Out of scope (do NOT add here):** corrective use of phone data, tilt automation, docked-via-motion confirmation, per-location profiles, background-location entitlement. Those are Stages 2/3.
