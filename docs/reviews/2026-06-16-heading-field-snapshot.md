# Heading-bias field snapshot — 2026-06-16 (captured before rain, rig brought indoors)

Captured live from `GET /api/v1/status` (rev 2253) while the camera was aimed at the
Wio tracker (blue case) ~36 m away, before the rig was moved inside.

## Raw values

| Field | Value | Note |
|---|---|---|
| `session.state` | SEARCHING / mode vision | no lock (color off, person seen, not matched) |
| `gps.source` | direct_lora | |
| `gps.distance_m` | 36.4 | camera→Wio |
| `gps.bearing_deg` | **221.6** | TRUE bearing = ground truth direction to the Wio |
| `gps.target_sats` | 27 | healthy fix |
| `gps.target_battery_mv` | 4087 | |
| `gps.stale` | false | fresh |
| `authority.base_locked` | **false** | base position NOT locked |
| `authority.base_drift_distance_m` | 18.9 | base GPS still wandering ~19 m → not settled |
| `authority.calibration_valid` | **false** | |
| `calibration.reference_heading` | 40.5 | STALE — old ios_native capture (days old) |
| `ptz.pan_enc` / `tilt_enc` | 25 / -27 | not homed (home = 0/0) |
| **`sensors.phone.true_heading_deg`** | **16.4** | |
| `sensors.phone.heading_deg` (mag) | 7.2 | |
| **`sensors.phone.heading_acc`** | **±63.3°** | GARBAGE — magnetometer uncalibrated/disturbed |
| **`sensors.phone.age_sec`** | **447.4** | STALE — phone stopped streaming ~7.5 min ago (app killed/reset) |
| `sensors.co_location.phone_base_dist_m` | 24.0 | phone & base GPS disagree by 24 m |
| `sensors.co_location.at_rig` | **false** | phone not recognized as co-located with base |
| `sensors.heading_bias_deg` | null | not computed |

## Verdict: NO clean heading validation this round

The comparison we wanted (phone true heading vs GPS bearing 221.6°) is **not valid** because:

1. **Phone stream was stale (7.5 min).** The app was killed/reset and the websocket
   wasn't streaming, so `true_heading 16.4°` is an old reading, not "now."
2. **Even the last reading was ±63° accuracy** — an uncalibrated/steel-disturbed
   magnetometer. Unusable regardless of staleness.
3. **base_locked=false, drift 18.9 m** — the base position never settled, so the
   calibration basis isn't established.
4. **at_rig=false, phone/base GPS 24 m apart** — the phone may not have been
   physically on the base (it had fallen earlier), or its GPS was off.

For the record: phone true 16.4° vs GPS 221.6° is ~205° apart — consistent with the
phone having FALLEN / not being seated facing the Wio, not a real heading bias.

## What's needed next dry session for a real validation

1. App **foregrounded + live** the whole time (stream age < ~2 s, watch `Phone POST`).
2. **Figure-8 compass calibration** (wave the phone in a figure-8) until
   `heading_acc` drops below ~15°. ±63° will never give a usable heading.
3. Phone **genuinely seated on the base**, facing the reference target
   (confirm `at_rig=true` and `phone_base_dist_m` small).
4. Let the **base GPS settle until base_locked=true** (drift small) before trusting any bearing.
5. Then compare phone **true** heading to the GPS **bearing_deg** (compare true-vs-true;
   Hawaii declination ≈ +9° E if you ever look at the magnetic value).
6. Range ≥ ~50 m for a tight GPS bearing (36 m gives ±~6° slop).
