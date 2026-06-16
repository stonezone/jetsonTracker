# Phone-on-Tripod Sensor Diagnostic & Comparison — Stage 1 design

**Status:** Draft (awaiting Zack's review)
**Date:** 2026-06-16
**Deciders:** Zack
**Owner lane:** Claude (iOS + backend; Codex out)

## Context

The operator iPhone is MagSafe-mounted (landscape) on the **fixed** rig base — aligned
with the camera's home/forward axis, ~6" from the PTZ motors and separated by a steel
plate. Empirically the compass tracks cleanly when the tripod head is rotated (the steel
plate + gap shield the motor field; the plate's own field is a *constant* hard-iron bias,
not jitter). The phone is USB-tethered to the Orin (`172.20.10.8`), same network as the
control API.

A phone-on-tripod pipeline **already exists** (Phase-3 "T3.2"):

- iOS `PhoneSensorPublisher` POSTs `heading_deg, heading_acc, lat, lon, h_acc, bump` to
  `/api/v1/sensors/phone` at 1 Hz (`ios/WaveCam/Sources/PhoneSensorPublisher.swift`).
- Backend `SensorHub` (`orin/wavecam/wavecam/sensor_hub.py`) ingests it and runs two
  **observe-only** monitors (heading-drift vs session baseline, bump). Its own docstring:
  *"NEVER corrective. Observe-only until Phase-3 post-G-PH evidence justifies it."*
  `SensorHub.latest()` is a non-blocking snapshot read.

So the plumbing to get phone heading/GPS to the rig is built; what's missing is (a) the
data is **not surfaced for display**, (b) **altitude is not captured at all**, and (c)
there is **no side-by-side comparison** against the Wio base.

**Why now:** the existing code is explicitly staged the way Zack proposed — ingest, then
*prove*, then use correctively. This diagnostic is the evidence gate that unblocks the
corrective stages. It is also needed per-location: Zack runs recurring **elevated** spots
(yard; friends' 2nd- and 3rd-floor lanais) where (i) tilt becomes a real pointing angle
sensitive to camera height, and (ii) building steel may bias the compass differently than
the open yard — so heading/altitude trust must be *measured at each spot*, not assumed.

## Goals / Non-goals

**Goal:** a read-only diagnostic that shows the phone's heading / GPS / **altitude** with
trust indicators (accuracy, freshness) side-by-side with the Wio base's position, so we
can decide — per location — whether the phone is reliable enough to later drive
calibration. Add altitude (currently dropped) to the existing pipeline.

**Non-goals (each its own later spec):**
- Corrective use of phone data (phone as heading/GPS *source*; primary/failover arbiter).
- Tilt automation from altitude; pan automation from heading; level from gravity.
- Per-location named calibration profiles.
- Replacing the Wio (it remains the LoRa receiver for the remote/subject tracker).

## Design

### Components & boundaries

| Unit | Responsibility | Depends on |
|---|---|---|
| `PhoneSensorPublisher` (iOS, exists) | Read CoreLocation/CoreMotion, POST at 1 Hz | CLLocationManager, CMMotionManager, CMAltimeter |
| `SensorHub` (backend, exists) | Store latest `PhoneSample`, observe-only monitors | `cfg.sensors` |
| `build_sensors_snapshot` (backend, **new**) | Compose phone-vs-base comparison for `/status` | `SensorHub.latest()`, `gps.get_camera_position()`, gps reader |
| `SensorsSection` (iOS, **new**) | Render phone vs base, read-only | `WaveCamClient` `/status` `sensors` block (phone *as the rig received it* — validates the whole pipeline end-to-end, not the phone's local read) |

### Data flow

```
phone CoreLocation/CoreMotion/CMAltimeter
   → PhoneSensorPublisher (1 Hz POST /api/v1/sensors/phone)
       → SensorHub.ingest()  (stores PhoneSample; observe-only monitors unchanged)
   ← GET /api/v1/status  (sensors block: phone {…} + base {…})
       → SensorsSection (poll ~1–2 Hz) renders two columns
```

### Backend changes (additive, read-only, no behavior change)

1. **`PhoneSample` + the `/api/v1/sensors/phone` request schema gain fields** (all optional,
   tolerant decode): `true_heading_deg`, `alt_m`, `alt_acc` (vertical accuracy, m),
   `baro_rel_m` (barometric relative altitude, m). `SensorHub` stores them with the sample.
   The existing observe-only monitors are **untouched** (they still key off magnetic
   heading + bump). No new corrective logic.
2. **New `build_sensors_snapshot(pipeline)`** in `control_snapshots.py`, surfaced under a
   `sensors` key in the existing status payload, with two sub-objects:
   - `phone`: `heading_deg`, `true_heading_deg`, `heading_acc`, `lat`, `lon`, `h_acc`,
     `alt_m`, `alt_acc`, `baro_rel_m`, `age_sec`, `drift_state` (from `SensorHub`).
   - `base`: `lat`, `lon`, `alt_m`, `sats`, `hdop`, `age_sec` — from
     `gps.get_camera_position()` + the gps reader (this is the "1b" base-position exposure;
     base lat/lon are computed-from but not currently emitted).
3. `sensors.enabled` continues to gate ingest; the snapshot reports `phone: null` (with a
   reason) when no sample / disabled, so the panel can say "phone telemetry off".

### iOS changes

4. **`PhoneSensorPublisher`** adds to the POST: `true_heading_deg` (set
   `locationManager.headingOrientation` for landscape so true/magnetic heading is correct),
   `alt_m` + `alt_acc` (`CLLocation.altitude` / `verticalAccuracy`), and `baro_rel_m`
   (`CMAltimeter.startRelativeAltitudeUpdates`, guarded by `isRelativeAltitudeAvailable`).
   `headingAccuracy < 0` keeps signalling invalid (existing convention).
5. **`SensorsSection`** — a `Section` inside `ToolsView` (not a 6th tab; the app is already
   at the 5-tab limit before iOS's "More" overflow). Two columns, Phone vs Wio base:
   - Heading: phone `trueHeading ± acc°` (—for base; no compass).
   - Position: lat/lon, phone `± h_acc m` vs base `sats / HDOP`; show the phone↔base
     distance (should be ~0, both describe the tripod).
   - Altitude: phone GPS alt `± v_acc` + barometric relative; base alt if present.
   - Freshness: per-source age badge.
   - Trust colouring: green/amber/red thresholds on `heading_acc` and `h_acc`/`v_acc`.
6. **Heading-bias readout (no new truth source):** when a manual heading lock exists
   (`reference_heading` from CALIBRATE), show `phone_true_heading − reference_heading` as
   the measured fixed offset. This is the number Stage 2 would bake in to skip the aim
   step; here it is display-only.

### Error handling

- `heading_acc < 0` / no `true_heading` → "INVALID" (existing iOS convention).
- Location permission denied → "no location permission" row; other fields still shown.
- Altimeter unavailable → omit barometric row.
- Phone sample stale (`age_sec` > 5 s) or absent → grey "stale"/"no telemetry" badge.
- Base no fix → "base: no fix" (mirrors `gps_unavailable`).

### Testing

- **Backend:** unit test that the status payload includes the `sensors` block with both
  `phone` and `base` sub-objects and the new fields, given an ingested sample + a stub
  camera position (extend `tests/test_control_api.py`); `SensorHub` stores the new
  `alt_m`/`true_heading_deg` fields (extend the sensor-hub test). Full suite + mypy.
- **iOS:** no XCTest target exists; verify by device build + on-rig read — the panel shows
  live phone-vs-base values; sanity-check phone↔base distance ≈ 0 and heading bias against
  one manual heading lock at the yard.

### Rollout

- Backend additive → redeploy via `deploy.sh` **when the rig is idle**; verify the
  `sensors` block via `curl /api/v1/status`.
- iOS → `build-device.sh` install; eyeball the panel at the yard and (later) a lanai.

## Roadmap (informs but is out of scope here)

- **Stage 2 — phone-fed calibration:** phone `true_heading` pre-fills the CALIBRATE heading
  step (pan); phone altitude **+ a per-location measured camera-height** drives tilt
  geometry (`tilt = atan2(height_above_water, distance)`); gravity/attitude informs level.
  Keeps the fail-closed confirm. Gated on Stage 1 evidence.
- **Stage 3 — redundancy & profiles:** base-source arbiter (phone GPS primary/failover vs
  Wio; Wio stays the LoRa relay for the remote tracker); **named per-location calibration
  profiles** (recall heading/location/tilt/height for yard / 2nd-floor / 3rd-floor).

## Consequences

- **Easier:** an evidence-based, per-location go/no-go on phone heading & altitude before
  any corrective change; altitude finally captured; base position visible for the first time.
- **Harder / to revisit:** the status payload grows a `sensors` block (additive, but a new
  contract surface iOS feature-detects); `PhoneSensorPublisher` gains CMAltimeter lifecycle.
- **Unchanged:** all corrective behavior, the observe-only SensorHub monitors, the Wio's
  LoRa role, and the existing calibration flow.
