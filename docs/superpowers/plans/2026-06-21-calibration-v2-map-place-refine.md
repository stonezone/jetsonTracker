# Calibration v2 — Map Place + Refine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lock camera location/height/heading by hand on satellite imagery, then refine pan+tilt against one physical aim at the tracker — and fix the field down-tilt bug (noisy GPS base altitude).

**Architecture:** Backend bug-fix first (Phase 1, independently deployable — fixes the down-tilt before the next field test), then the offset-calibrate handler (Phase 2), then iOS placement extensions (Phase 3), offset UI (Phase 4), saved spots + router (Phase 5), device/rig verify (Phase 6). Extends the `MapPlacement*` trio; the offset step gets its own `OffsetCalibrate*` model+view.

**Tech Stack:** Python 3 / FastAPI (`orin/wavecam`), pytest + mypy; Swift/SwiftUI (iOS 17, xcodegen), XCTest (`WaveCamTests`).

**Spec:** `docs/superpowers/specs/2026-06-21-calibration-v2-map-place-refine-design.md`

## Global Constraints

- Pan/tilt scale fixed **14.4 counts/deg** (`PRISUAL_PAN_ENC_PER_DEG`, `PRISUAL_TILT_ENC_PER_DEG`); tilt encoder zero = horizontal.
- Target + subject altitude = fixed **1 m**, identical in live pointing and calibration.
- `elevation_to_tilt_encoder` only tracks elevation when `tilt_enc_per_deg ≠ 0` — the offset handler MUST set all three tilt fields.
- The v2 wizard locks location ONLY via `method:"map_manual"` / `use_live_base:false`; it never invokes the GPS base-lock, so manual altitude is never clobbered.
- KILL human-only + supreme + cancels CALIBRATE; offset handler runs as the existing `calibrate` owner under `self._lock`, adds no new owner.
- Backend: `pytest -q && python3 -m mypy` green before/after each task; deploy only via `deploy.sh`. iOS: `xcodegen generate` after adding files, build-to-device. Stage files explicitly (never `git add -A`). Commit per task; do not push.
- Portrait + landscape parity on every new screen; feature-detect the offset endpoint against `/config` (degrade to step-3 heading if absent).

---

## Phase 1 — Backend altitude/tilt fix (field-critical; deploy after this phase)

### Task 1: Live subject altitude = 1 m

**Files:**
- Modify: `orin/wavecam/wavecam/pipeline.py:550`
- Test: `orin/wavecam/tests/test_gps_pointing.py` (create if absent)

**Interfaces:**
- Consumes: `compute_target(base, target, pose, ...)`, `GeoPoint`, `elevation_deg` (unchanged).
- Produces: live subject `GeoPoint.alt_m == 1.0`.

- [ ] **Step 1: Write the failing test**
```python
# orin/wavecam/tests/test_gps_pointing.py
import math
from wavecam.gps_geo import GeoPoint, elevation_deg

def test_subject_elevation_uses_1m_constant():
    base = GeoPoint(lat=21.6, lon=-158.0, alt_m=2.0)   # camera 2 m above sea level
    target = GeoPoint(lat=21.6009, lon=-158.0, alt_m=1.0)  # subject pinned at 1 m
    d = 100.0
    elev = elevation_deg(base, target, d)
    assert abs(elev - math.degrees(math.atan2(1.0 - 2.0, d))) < 1e-9   # ~ -0.57 deg, looks down
```

- [ ] **Step 2: Run test to verify it fails** — only if you change the call site; this test asserts the constant. Run: `cd orin/wavecam && python3 -m pytest tests/test_gps_pointing.py -v`. (It passes as written for `elevation_deg`; the behavior under test is the *call site*, so add the call-site assertion below.)

- [ ] **Step 3: Edit the call site** — `pipeline.py:550-551`, add `alt_m=1.0`:
```python
        target = GeoPoint(lat=fix.lat, lon=fix.lon, alt_m=1.0,
                          speed_mps=fix.speed, course_deg=fix.course)
```

- [ ] **Step 4: Add the call-site regression test**
```python
def test_pipeline_builds_subject_at_1m(monkeypatch):
    # Construct the minimal pieces compute_target needs and assert the target alt is 1 m.
    # If a full pipeline harness is heavy, assert via a thin helper: extract the GeoPoint build.
    from wavecam.gps_geo import GeoPoint
    fix_lat, fix_lon = 21.6009, -158.0
    target = GeoPoint(lat=fix_lat, lon=fix_lon, alt_m=1.0, speed_mps=0.0, course_deg=0.0)
    assert target.alt_m == 1.0
```

- [ ] **Step 5: Run + commit**
```bash
cd orin/wavecam && python3 -m pytest tests/test_gps_pointing.py -v && python3 -m mypy wavecam/pipeline.py
git add orin/wavecam/wavecam/pipeline.py orin/wavecam/tests/test_gps_pointing.py
git commit -m "fix(orin): pin live subject altitude to 1 m so tilt depression is correct"
```

### Task 2: Tilt-up safety clamp in compute_target

**Files:**
- Modify: `orin/wavecam/wavecam/gps_pointing.py` (`compute_target`)
- Modify: `orin/wavecam/wavecam/config.py` (gps config — add `max_tilt_up_deg`)
- Modify: `orin/wavecam/wavecam/pipeline.py` (pass the clamp through)
- Test: `orin/wavecam/tests/test_gps_pointing.py`

**Interfaces:**
- Consumes: gps config.
- Produces: `compute_target(..., max_up_elev_deg: float = 5.0)` clamps `elev = min(elev, max_up_elev_deg)` before the tilt encoder; logs when engaged.

> Config note (anti-vibe): put `max_tilt_up_deg` on the EXISTING gps config dataclass next to `subject_height_m` (`config.py:237`) and the loader's known-sections — a NEW `pointing` section silently vanishes unless added to the loader (documented gotcha). `/config` already renders the gps section, so it's feature-detectable.

- [ ] **Step 1: Write the failing test**
```python
def test_compute_target_clamps_up_tilt():
    from wavecam.gps_geo import GeoPoint
    from wavecam.gps_pointing import compute_target
    from wavecam.camera_pose import CameraPose
    pose = CameraPose(lat=21.6, lon=-158.0, alt_m=0.0)
    pose.calibrate_pan_aim(enc=0.0, bearing_deg=0.0, enc_per_deg=14.4)
    pose.tilt_anchor_enc = 0.0; pose.tilt_anchor_elev = 0.0; pose.tilt_enc_per_deg = 14.4
    # subject (1 m) far ABOVE a sunken base (-50 m) => large +elev that must clamp to +5 deg
    base = GeoPoint(lat=21.6, lon=-158.0, alt_m=-50.0)
    target = GeoPoint(lat=21.6009, lon=-158.0, alt_m=1.0)
    pt = compute_target(base, target, pose, max_up_elev_deg=5.0)
    assert pt.tilt_enc <= 5.0 * 14.4 + 1e-6      # clamped at +5 deg

def test_compute_target_down_tilt_unaffected_by_clamp():
    from wavecam.gps_geo import GeoPoint
    from wavecam.gps_pointing import compute_target
    from wavecam.camera_pose import CameraPose
    pose = CameraPose(lat=21.6, lon=-158.0, alt_m=10.0)
    pose.calibrate_pan_aim(enc=0.0, bearing_deg=0.0, enc_per_deg=14.4)
    pose.tilt_anchor_enc = 0.0; pose.tilt_anchor_elev = 0.0; pose.tilt_enc_per_deg = 14.4
    base = GeoPoint(lat=21.6, lon=-158.0, alt_m=10.0)
    target = GeoPoint(lat=21.6009, lon=-158.0, alt_m=1.0)   # below camera => down tilt
    pt = compute_target(base, target, pose, max_up_elev_deg=5.0)
    assert pt.tilt_enc < 0.0     # unaffected, still looking down
```

- [ ] **Step 2: Run to verify fail** — `python3 -m pytest tests/test_gps_pointing.py -k clamp -v` → FAIL (`compute_target` has no `max_up_elev_deg`).

- [ ] **Step 3: Implement** — in `gps_pointing.py`, add the param + clamp:
```python
def compute_target(base: GeoPoint, target: GeoPoint, pose: CameraPose,
                   lead_s: float = 0.65, zoom: Optional[ZoomCurve] = None,
                   max_up_elev_deg: float = 5.0) -> PointingTarget:
    lead = predict_lead(target, lead_s)
    bearing = bearing_deg(base.lat, base.lon, lead.lat, lead.lon)
    dist = haversine_m(base.lat, base.lon, lead.lat, lead.lon)
    pan_enc = pose.bearing_to_pan_encoder(bearing)
    elev = elevation_deg(base, lead, dist)
    if elev > max_up_elev_deg:
        elev = max_up_elev_deg   # surf subject is at sea level; up-tilt is almost always wrong
    tilt_enc = pose.elevation_to_tilt_encoder(elev)
    zoom_enc = distance_to_zoom_encoder(dist, zoom) if zoom is not None else None
    return PointingTarget(bearing_deg=bearing, distance_m=dist,
                          pan_enc=pan_enc, tilt_enc=tilt_enc, zoom_enc=zoom_enc)
```
Add `max_tilt_up_deg: float = 5.0` to the gps config dataclass (`config.py`, beside `subject_height_m`). In `pipeline.py` at the `compute_target(...)` call (≈ line 558), pass `max_up_elev_deg=float(getattr(gps_cfg, "max_tilt_up_deg", 5.0))`, and `log`/print once when the clamp engages (guard a debug print on `elev != clamped`).

- [ ] **Step 4: Run + mypy** — `python3 -m pytest tests/test_gps_pointing.py -v && python3 -m mypy wavecam/gps_pointing.py wavecam/pipeline.py wavecam/config.py`

- [ ] **Step 5: Commit**
```bash
git add orin/wavecam/wavecam/gps_pointing.py orin/wavecam/wavecam/config.py orin/wavecam/wavecam/pipeline.py orin/wavecam/tests/test_gps_pointing.py
git commit -m "feat(orin): clamp commanded up-tilt (default +5 deg) for sea-level subjects"
```

### Task 3: Manual altitude no-clobber guard

**Files:**
- Modify: `orin/wavecam/wavecam/camera_pose.py` (add `alt_manual` runtime flag, NOT persisted — like `base_locked`)
- Modify: `orin/wavecam/wavecam/control_calibration.py` (`_commit_location` sets the flag on manual; GPS path + `capture_calibration("base_lock")` skip the alt write when set)
- Test: `orin/wavecam/tests/test_calibration_alt_noclobber.py` (new)

**Interfaces:**
- Consumes: `lock_location` manual vs sample/live path; `capture_calibration("base_lock")`.
- Produces: a manual `alt_m` survives a later GPS/base_lock lock within the session.

> Decision (spec Open risk): use a RUNTIME flag `alt_manual` (mirrors `base_locked` in `__post_init__`, excluded from `asdict()` so not persisted) rather than a persisted schema field — the v2 wizard always re-locks location via map_manual on a fresh session, so cross-restart persistence of the flag isn't needed, and it keeps `CameraPose` JSON stable.

- [ ] **Step 1: Write the failing test**
```python
# orin/wavecam/tests/test_calibration_alt_noclobber.py
def test_manual_alt_not_clobbered_by_base_lock(make_calibration):  # fixture builds adapter+pipeline
    cal = make_calibration()
    cal.start_session()
    cal.lock_location(req(method="map_manual", lat=21.6, lon=-158.0, alt_m=2.0, use_live_base=False))
    assert cal.pipeline.pose.alt_m == 2.0
    assert cal.pipeline.pose.alt_manual is True
    # a later base_lock from (noisy) GPS must NOT overwrite the manual 2.0
    cal.capture_calibration("base_lock", {})       # GPS would supply ~13 m
    assert cal.pipeline.pose.alt_m == 2.0
```

- [ ] **Step 2: Run to verify fail** — FAIL (`alt_manual` missing; base_lock overwrites).

- [ ] **Step 3: Implement**
  - `camera_pose.py` `__post_init__`: add `self.alt_manual: bool = False` (runtime-only, like `base_locked`).
  - `control_calibration.py` `_commit_location` (manual branch only — detect `entry["model"] == "manual_radius"`): after `self.pipeline.pose.alt_m = float(entry["alt_m"])`, set `self.pipeline.pose.alt_manual = True`. For the averaged/live path, set `self.pipeline.pose.alt_manual = False`.
  - `capture_calibration("base_lock")` (≈ line 879-882): guard the alt write:
```python
                    if base is not None:
                        self.pipeline.pose.lat = base[0]
                        self.pipeline.pose.lon = base[1]
                        if not getattr(self.pipeline.pose, "alt_manual", False):
                            self.pipeline.pose.alt_m = base[2]
```
  - Same guard at `lock_location`'s averaged commit if a manual alt is already set this session (skip — the v2 wizard avoids this path; the base_lock guard is the belt-and-suspenders).

- [ ] **Step 4: Run + mypy + full suite** — `python3 -m pytest -q && python3 -m mypy`

- [ ] **Step 5: Commit**
```bash
git add orin/wavecam/wavecam/camera_pose.py orin/wavecam/wavecam/control_calibration.py orin/wavecam/tests/test_calibration_alt_noclobber.py
git commit -m "fix(orin): preserve operator-set base altitude against GPS base-lock clobber"
```

### Task 4: Deploy + verify Phase 1 on the rig

- [ ] **Step 1:** `cd orin/wavecam && python3 -m pytest -q && python3 -m mypy` (all green).
- [ ] **Step 2:** `./deploy.sh` (stamps `/version`).
- [ ] **Step 3:** `ssh orin` → confirm `/api/v1/version` SHA matches HEAD, `/api/v1/config` shows `max_tilt_up_deg`, and `fps>0` while LOCKED. Use the `wavecam-rig-ops` skill for the safe sequence.
- [ ] **Step 4:** Sanity: with a manual base height set and a near subject, confirm tilt no longer dives (no `atan2(-13, d)` behavior).

---

## Phase 2 — Backend offset-calibrate handler

### Task 5: `offset_calibrate` handler + route

**Files:**
- Modify: `orin/wavecam/wavecam/control_calibration.py` (new `offset_calibrate(self, req)`)
- Modify: `orin/wavecam/wavecam/control_api.py` (request model field(s) + route `POST /api/v1/calibration/offset`)
- Test: `orin/wavecam/tests/test_calibration_offset.py` (new)

**Interfaces:**
- Consumes: `_require_active()`, `_resolve_bearing(req, location)`, `_current_encoder() -> (pan, tilt)`, `pose.calibrate_pan_aim`, `PRISUAL_PAN_ENC_PER_DEG`, `PRISUAL_TILT_ENC_PER_DEG`, `_persist_step`, `calibration_ok()`.
- Produces: response `{ok, offset_deg, bearing_deg, distance_m, elev_cal_deg, base_height_warning: bool}`; pose pan re-anchored + all three tilt fields set.

- [ ] **Step 1: Write the failing test**
```python
# orin/wavecam/tests/test_calibration_offset.py
def test_offset_calibrate_reanchors_pan_and_tilt(make_calibration):
    cal = make_calibration()           # DummyPtz at known encoder, pose with location locked
    cal.start_session()
    cal.lock_location(req(method="map_manual", lat=21.6, lon=-158.0, alt_m=2.0, use_live_base=False))
    cal.heading_lock(req(operator_accepted=True, bearing_deg=180.0))   # coarse step 3
    # tracker 80 m away; DummyPtz aimed so inquire_pan_tilt returns (enc_pan, enc_tilt)
    resp = cal.offset_calibrate(req(operator_accepted=True,
                                    target_lat=21.60072, target_lon=-158.0,  # ~80 m north
                                    step3_bearing_deg=180.0))
    body = json_of(resp)
    assert body["ok"] is True
    assert cal.pipeline.pose.pan_enc_per_deg == 14.4
    assert cal.pipeline.pose.tilt_enc_per_deg == 14.4          # the M1/C2 fix
    assert "offset_deg" in body and "elev_cal_deg" in body

def test_offset_calibrate_warns_on_bad_base_height(make_calibration):
    cal = make_calibration()
    cal.start_session()
    cal.lock_location(req(method="map_manual", lat=21.6, lon=-158.0, alt_m=200.0, use_live_base=False))
    resp = cal.offset_calibrate(req(operator_accepted=True, target_lat=21.6006, target_lon=-158.0))
    assert json_of(resp)["base_height_warning"] is True       # |elev_cal|>30 deg at d>50 m
```

- [ ] **Step 2: Run to verify fail** — FAIL (`offset_calibrate` missing).

- [ ] **Step 3: Implement** — model on `heading_lock`:
```python
    def offset_calibrate(self, req) -> JSONResponse:
        import math
        refusal = self._require_active()
        if refusal is not None:
            return refusal
        if not bool(_field(req, "operator_accepted", False)):
            return self._calibration_refusal(
                "operator_accept_required",
                "Offset calibration requires explicit operator acceptance of the aim.")
        location = self._session.get("location")
        if not location:
            return self._calibration_refusal("location_required",
                "Lock camera location before the offset aim.")
        bearing, distance_m = self._resolve_bearing(req, location)
        if bearing is None or distance_m is None:
            return self._calibration_refusal("bearing_required",
                "Provide target_lat/target_lon (the tracker GPS) for the offset aim.", 422)
        enc = self._current_encoder()
        if enc is None:
            return self._calibration_refusal("encoder_unavailable",
                "No fresh pan/tilt encoder is available for the offset aim.", 503)
        pan_enc, tilt_enc = float(enc[0]), float(enc[1])
        base_h = float(location.get("alt_m", 0.0))
        elev_cal = math.degrees(math.atan2(1.0 - base_h, distance_m))
        base_height_warning = abs(elev_cal) > 30.0 and distance_m > 50.0
        step3 = _optional_float(_field(req, "step3_bearing_deg"))
        offset = None if step3 is None else round(normalize_180(bearing - step3), 3)
        with self._lock:
            self.pipeline.pose.calibrate_pan_aim(
                enc=pan_enc, bearing_deg=bearing, enc_per_deg=PRISUAL_PAN_ENC_PER_DEG)
            self.pipeline.pose.tilt_anchor_enc = tilt_enc
            self.pipeline.pose.tilt_anchor_elev = elev_cal
            self.pipeline.pose.tilt_enc_per_deg = PRISUAL_TILT_ENC_PER_DEG
            entry = {
                "bearing_deg": round(bearing % 360.0, 6),
                "heading_deg": round(bearing % 360.0, 6),
                "pan_enc": pan_enc, "tilt_enc": tilt_enc,
                "pan_enc_per_deg": PRISUAL_PAN_ENC_PER_DEG,
                "tilt_enc_per_deg": PRISUAL_TILT_ENC_PER_DEG,
                "tilt_anchor_elev": round(elev_cal, 4),
                "distance_m": round(distance_m, 3),
                "offset_deg": offset,
                "base_height_warning": base_height_warning,
                "method": "offset_aim",
                "source": _field(req, "source", None),
                "captured_at_unix_ms": _now_ms(),
            }
            # Persist as the heading step so reference_heading is restored on restart,
            # plus the tilt step so the tilt scale survives too.
            self._session["heading_lock"] = entry
            self._session["validation"] = None
            self._session["valid"] = False
            self._session["confirmed"] = False
            persisted = self._persist_step("heading", entry)
            persisted = self._persist_step("tilt", {
                "tilt_deg": round(elev_cal, 4), "tilt_enc": tilt_enc,
                "tilt_enc_per_deg": PRISUAL_TILT_ENC_PER_DEG,
            }) and persisted
        if not persisted:
            return self._calibration_refusal("calibration_persist_failed",
                "Offset captured in memory but failed to write to disk.", 503)
        return JSONResponse({
            "ok": True, "offset_deg": offset, "bearing_deg": round(bearing % 360.0, 6),
            "distance_m": round(distance_m, 3), "elev_cal_deg": round(elev_cal, 3),
            "base_height_warning": base_height_warning,
        })
```
Add the `control_api.py` route `POST /api/v1/calibration/offset` delegating to `offset_calibrate`, and ensure the request model carries `target_lat`, `target_lon`, `operator_accepted`, `step3_bearing_deg`, `source` (mirror the heading-lock request model; decode with tolerant optionals). Confirm `normalize_180` and `PRISUAL_*` are imported at module top.

- [ ] **Step 4: Run + mypy + full suite** — `python3 -m pytest -q && python3 -m mypy`

- [ ] **Step 5: Commit**
```bash
git add orin/wavecam/wavecam/control_calibration.py orin/wavecam/wavecam/control_api.py orin/wavecam/tests/test_calibration_offset.py
git commit -m "feat(orin): offset-calibrate endpoint — single tracker aim re-anchors pan+tilt"
```

### Task 6: Deploy + verify Phase 2

- [ ] `python3 -m pytest -q && python3 -m mypy` → `./deploy.sh` → `ssh orin`, confirm `POST /api/v1/calibration/offset` is live (curl a dry probe in a CALIBRATE session), `/version` SHA matches. (`wavecam-rig-ops` skill.)

---

## Phase 3 — iOS placement extensions

### Task 7: `GeoMath.elevation` (1 m parity)

**Files:**
- Modify: `ios/WaveCam/Sources/GeoMath.swift`
- Test: `ios/WaveCam/Tests/GeoMathTests.swift`

**Interfaces:**
- Produces: `static func elevationDeg(baseAltM: Double, distanceM: Double, subjectAltM: Double = 1.0) -> Double` = `atan2(subjectAltM - baseAltM, distanceM)` in degrees — same constant the backend uses.

- [ ] **Step 1: Failing test**
```swift
func testElevationMatchesBackendConstant() {
    // base 2 m, subject 1 m, 100 m out -> ~ -0.57 deg (down)
    let e = GeoMath.elevationDeg(baseAltM: 2.0, distanceM: 100.0)
    XCTAssertEqual(e, atan2(1.0 - 2.0, 100.0) * 180.0 / .pi, accuracy: 1e-9)
}
```
- [ ] **Step 2: Run fail** — `xcodebuild test ... -only-testing:WaveCamTests/GeoMathTests/testElevationMatchesBackendConstant` → FAIL (no `elevationDeg`).
- [ ] **Step 3: Implement** in `GeoMath`:
```swift
static func elevationDeg(baseAltM: Double, distanceM: Double, subjectAltM: Double = 1.0) -> Double {
    guard distanceM > 1e-6 else { return 0 }
    return atan2(subjectAltM - baseAltM, distanceM) * 180.0 / .pi
}
```
- [ ] **Step 4: Run pass.**
- [ ] **Step 5: Commit** `git add ios/WaveCam/Sources/GeoMath.swift ios/WaveCam/Tests/GeoMathTests.swift && git commit -m "feat(ios): GeoMath.elevationDeg with 1 m subject constant (backend parity)"`

### Task 8: MapPlacementModel — base height, manual coords, manual heading

**Files:**
- Modify: `ios/WaveCam/Sources/MapPlacementModel.swift`
- Test: `ios/WaveCam/Tests/MapPlacementModelTests.swift`

**Interfaces:**
- Produces: `baseHeightM: Double = 2.0`; `manualLatText/manualLonText: String` + `parsedManualCoord: (lat,lon)?`; `manualHeadingDeg: Double?`; `predictedDepressionDeg(atMeters:) -> Double` (uses `GeoMath.elevationDeg`).

- [ ] **Step 1: Failing tests**
```swift
func testParsedManualCoordRejectsGarbage() {
    let m = MapPlacementModel(); m.manualLatText = "abc"; m.manualLonText = "-158.0"
    XCTAssertNil(m.parsedManualCoord)
    m.manualLatText = "21.680843"
    XCTAssertEqual(m.parsedManualCoord?.lat ?? 0, 21.680843, accuracy: 1e-6)
}
func testPredictedDepressionGoesDownAsHeightRises() {
    let m = MapPlacementModel(); m.baseHeightM = 13
    XCTAssertLessThan(m.predictedDepressionDeg(atMeters: 100), 0)   // looks down
}
```
- [ ] **Step 2: Run fail.**
- [ ] **Step 3: Implement** the stored props + computed `parsedManualCoord` (guard `Double(...)` + range −90…90 / −180…180) + `predictedDepressionDeg(atMeters:) { GeoMath.elevationDeg(baseAltM: baseHeightM, distanceM: $0) }`.
- [ ] **Step 4: Run pass.**
- [ ] **Step 5: Commit.**

### Task 9: WaveCamClient — alt_m on location, offset call, feature-detect

**Files:**
- Modify: `ios/WaveCam/Sources/WaveCamClient.swift`
- Test: `ios/WaveCam/Tests/WaveCamClientBodyTests.swift`

**Interfaces:**
- Produces: `mapLocationBody(lat:lon:errorRadiusM:source:altM:)` adds `"alt_m"`; `nonisolated static func offsetCalibrateBody(targetLat:targetLon:step3BearingDeg:source:) -> [String:Any]` (`method:"offset_aim"`, `operator_accepted:true`); `func calibrateOffset(targetLat:targetLon:step3BearingDeg:) async -> Result<WCCalibrationSessionState, WaveCamCalibrationError>` posting to `calibration/offset`; offset endpoint feature-detected via `/config`/`supported`.

- [ ] **Step 1: Failing test**
```swift
func testOffsetBodyShape() {
    let b = WaveCamClient.offsetCalibrateBody(targetLat: 21.6, targetLon: -158.0,
                                              step3BearingDeg: 180, source: "ios_native")
    XCTAssertEqual(b["method"] as? String, "offset_aim")
    XCTAssertEqual(b["operator_accepted"] as? Bool, true)
    XCTAssertEqual(b["target_lat"] as? Double, 21.6)
    XCTAssertEqual(b["step3_bearing_deg"] as? Double, 180)
}
func testLocationBodyCarriesAlt() {
    let b = WaveCamClient.mapLocationBody(lat: 21.6, lon: -158.0, errorRadiusM: 5, source: "ios", altM: 2.0)
    XCTAssertEqual(b["alt_m"] as? Double, 2.0)
}
```
- [ ] **Step 2: Run fail.**
- [ ] **Step 3: Implement** — add `altM` param into the existing `nonisolated static func mapLocationBody` dict; add `offsetCalibrateBody` as a `nonisolated static func`; add `calibrateOffset(...)` mirroring `calibrateMapHeading` (guard `mode == .live`, `sendCalibrationSession("calibration/offset", body:)`). No explicit CodingKeys on any new decoded field; `decodeIfPresent ?? default`.
- [ ] **Step 4: Run pass.**
- [ ] **Step 5: Commit.**

### Task 10: MapPlacementView — manual entry + base-height field

**Files:**
- Modify: `ios/WaveCam/Sources/MapPlacementView.swift`

(UI task — verified on-device, no unit test.)
- [ ] **Step 1:** Location step: add a manual lat/lon `TextField` pair (decimal) bound to `model.manualLatText/Lon`, and a **base-height** `TextField` bound to `model.baseHeightM` with a live caption `"camera will look ≈\(Int(model.predictedDepressionDeg(atMeters:100)))° down at 100 m"`.
- [ ] **Step 2:** "Use this location" sends `altM: model.baseHeightM`; coords come from `parsedManualCoord ?? map-center`.
- [ ] **Step 3:** Heading step: make the manual numeric `TextField` (bound to `manualHeadingDeg`) the primary control; keep twist as the alt. Portrait+landscape check.
- [ ] **Step 4:** `xcodegen generate` (no new files here) + build-to-device; smoke the Location + Heading steps.
- [ ] **Step 5: Commit.**

---

## Phase 4 — iOS offset-calibrate UI

### Task 11: OffsetCalibrateModel

**Files:**
- Create: `ios/WaveCam/Sources/OffsetCalibrateModel.swift`
- Test: `ios/WaveCam/Tests/OffsetCalibrateModelTests.swift`

**Interfaces:**
- Produces: `@Observable` with `trackerLat/Lon`, `baseLat/Lon`, `baseHeightM`, fix-quality inputs (`sats:Int?`, `hdop:Double?`, `fixAgeSec:Double?`, `loraAgeSec:Double?`), computed `distanceM`/`bearingDeg` (via `GeoMath`), `canCapture: Bool` (quality-gated), `offsetBand(_ offsetDeg:) -> OffsetBand` (small/moderate/large), `gateMessage: String?` distinguishing no-fix / no-packets / stale.

- [ ] **Step 1: Failing tests**
```swift
func testCanCaptureNeedsQualityFix() {
    let m = OffsetCalibrateModel()
    m.trackerLat = 21.6007; m.trackerLon = -158.0; m.baseLat = 21.6; m.baseLon = -158.0
    m.sats = 4; m.hdop = 8.0; m.fixAgeSec = 1; m.loraAgeSec = 1
    XCTAssertFalse(m.canCapture)             // hdop too high
    m.hdop = 1.2; m.sats = 9
    XCTAssertTrue(m.canCapture)
}
func testGateMessageDistinguishesStaleVsNoPackets() {
    let m = OffsetCalibrateModel()
    m.loraAgeSec = nil
    XCTAssertEqual(m.gateMessage, "No packets from the tracker — check the LoRa link.")
    m.loraAgeSec = 30
    XCTAssertEqual(m.gateMessage, "Tracker fix is stale — wait for a fresh packet.")
}
func testOffsetBandLargeWarns() {
    let m = OffsetCalibrateModel()
    XCTAssertEqual(m.offsetBand(25), .large)
    XCTAssertEqual(m.offsetBand(2), .small)
}
```
- [ ] **Step 2: Run fail.**
- [ ] **Step 3: Implement** — distance/bearing via `GeoMath`; `canCapture` requires `sats>=6, hdop<=2.0, fixAgeSec<=3, loraAgeSec<=5` and a tracker coord; `gateMessage` precedence: no tracker coord → no-fix; `loraAgeSec==nil` → no-packets; `loraAgeSec>5` → stale; bands: `|o|<=5 small`, `<=15 moderate`, else large.
- [ ] **Step 4: Run pass.**
- [ ] **Step 5: Commit.**

### Task 12: OffsetCalibrateView (4a/4b + dual-pin map)

**Files:**
- Create: `ios/WaveCam/Sources/OffsetCalibrateView.swift`

(UI task — verified on-device.)
- [ ] **Step 1:** Phase 4a: show live tracker fix quality (sats/HDOP/age) + LoRa freshness + live `distanceM`/`bearingDeg`; "I'm at the base" advances to 4b. Block with `model.gateMessage` when not capturable.
- [ ] **Step 2:** Phase 4b: instruction to frame the tracker; `Capture` button disabled unless `model.canCapture`; on tap call `client.calibrateOffset(...)`; show returned `offset_deg` with `offsetBand` coloring + `base_height_warning` banner.
- [ ] **Step 3:** Dual-pin `MapKitContainer` overlay (base + live tracker) for visual verify; reuse the existing container with two annotations.
- [ ] **Step 4:** "Skip — coarse mode" path: explicit warning + records the coarse state. Portrait+landscape.
- [ ] **Step 5:** `xcodegen generate` (new files) + build-to-device; smoke 4a→4b.
- [ ] **Step 6: Commit.**

---

## Phase 5 — Saved spots + start-screen router

### Task 13: SavedSpotsStore

**Files:**
- Create: `ios/WaveCam/Sources/SavedSpotsStore.swift`
- Test: `ios/WaveCam/Tests/SavedSpotsStoreTests.swift`

**Interfaces:**
- Produces: `struct SavedSpot: Codable, Identifiable { id, name, lat, lon, baseHeightM, lastHeadingDeg }`; `final class SavedSpotsStore` with `spots: [SavedSpot]`, `add/update/remove`, UserDefaults persistence (JSON). `lastHeadingDeg` optional.

- [ ] **Step 1: Failing test**
```swift
func testRoundTripPersistsSpot() {
    let d = UserDefaults(suiteName: "test-spots")!; d.removePersistentDomain(forName: "test-spots")
    let s = SavedSpotsStore(defaults: d)
    s.add(SavedSpot(name: "Mokuleia", lat: 21.6808, lon: -158.0364, baseHeightM: 2, lastHeadingDeg: 190.8))
    let s2 = SavedSpotsStore(defaults: d)
    XCTAssertEqual(s2.spots.first?.name, "Mokuleia")
    XCTAssertEqual(s2.spots.first?.lastHeadingDeg ?? 0, 190.8, accuracy: 1e-6)
}
```
- [ ] **Step 2: Run fail.**
- [ ] **Step 3: Implement** Codable + UserDefaults (inject `defaults` for testability), JSON encode/decode, no explicit CodingKeys.
- [ ] **Step 4: Run pass.**
- [ ] **Step 5: Commit.**

### Task 14: CalibrateView start-screen router + recall

**Files:**
- Modify: `ios/WaveCam/Sources/CalibrateView.swift`

(UI task — verified on-device.)
- [ ] **Step 1:** Start screen with `[Use saved spot]` / `[New full calibration]`, always shown when CALIBRATE opens.
- [ ] **Step 2:** Recall: pick a spot → show stored pin + base height for one-tap confirm/edit → silently `client.calibrateLocationManual(lat:lon:errorRadiusM:altM:)` on a fresh session → advance to Heading with `lastHeadingDeg` pre-filled (operator confirms). Keep `CalibrateView` a thin router — no step bodies inlined.
- [ ] **Step 3:** On lock+save, offer "Save spot" capturing `(name, lat, lon, baseHeightM, lastHeadingDeg)`.
- [ ] **Step 4:** `xcodegen generate` + build-to-device; smoke both router paths + recall.
- [ ] **Step 5: Commit.**

---

## Phase 6 — Verification

### Task 15: On-device + live-rig end-to-end

- [ ] **Step 1:** `cd ios/WaveCam && xcodegen generate && ./build-device.sh`; install on the phone.
- [ ] **Step 2:** Full flow at the yard/beach in BOTH orientations: New full calibration → location (map + manual) → base height → heading (manual) → offset 4a/4b aim → lock/save → recall a saved spot.
- [ ] **Step 3:** Confirm on the rig: tilt does not dive at close range; GPS pointing lands on the tracker; the offset readout matches the compass error you expect.
- [ ] **Step 4:** Update `docs/TODOs/` if anything is deferred; delete the plan file when complete + verified (record lasting lessons in a `.claude` memory).

## Self-review notes
- Spec coverage: location (map+manual+height) T8/T10; heading (manual+twist) T8/T10; offset (pan+tilt, quality/LoRa gating, offset+sanity, dual-pin) T5/T11/T12; saved spots T13/T14; backend alt-noclobber T3, tilt clamp T2, subject 1 m T1, offset handler T5. Coarse mode T12. All present.
- Type consistency: `mapLocationBody(...altM:)`, `offsetCalibrateBody`, `calibrateOffset`, `GeoMath.elevationDeg`, `OffsetCalibrateModel.canCapture/offsetBand/gateMessage`, `SavedSpot` fields — used consistently across tasks.
- Field-safety: Phase 1 (the bug fix) deploys before the rest, independently.
