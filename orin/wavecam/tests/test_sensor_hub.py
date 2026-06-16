"""Unit tests for PhoneSample dataclass fields — altitude + true heading (Task 1)."""


def test_phone_sample_carries_altitude_and_true_heading():
    from wavecam.sensor_hub import PhoneSample
    s = PhoneSample(heading_deg=100.0, heading_acc=3.0, lat=21.0, lon=-157.0,
                    h_acc=4.0, bump=False, received_at=1.0,
                    true_heading_deg=102.5, alt_m=12.4, alt_acc=6.0, baro_rel_m=0.2)
    assert s.true_heading_deg == 102.5
    assert s.alt_m == 12.4 and s.alt_acc == 6.0 and s.baro_rel_m == 0.2


# ---------------------------------------------------------------------------
# Task 2 — at-rig co-location gate
# ---------------------------------------------------------------------------

import types


def test_compute_at_rig_near_far_unknown():
    from wavecam.sensor_hub import PhoneSample, SensorHub, compute_at_rig, AT_RIG_M

    base = (21.0, -157.0, 5.0)
    near, d_near, basis_near = compute_at_rig(21.00001, -157.00001, base)
    far, d_far, _ = compute_at_rig(21.05, -157.05, base)       # ~7 km
    unk, _, basis_unk = compute_at_rig(21.0, -157.0, None)
    none_phone, _, basis_np = compute_at_rig(None, None, base)

    assert near is True and d_near < AT_RIG_M and basis_near == "gps_proximity"
    assert far is False and d_far > AT_RIG_M
    assert unk is None and basis_unk == "no_base_fix"
    assert none_phone is None and basis_np == "no_phone_fix"


def _make_fake_events():
    """Return a simple recording stub whose interface matches EventRing.record()."""
    recorded = []

    def record(kind, detail):
        recorded.append((kind, detail))

    stub = types.SimpleNamespace(record=record, recorded=recorded)
    return stub


def _make_fake_cfg(enabled=True, drift_alert_deg=12.0):
    sensors = types.SimpleNamespace(enabled=enabled, drift_alert_deg=drift_alert_deg)
    return types.SimpleNamespace(sensors=sensors)


def _bump_sample(lat, lon, t=100.0):
    from wavecam.sensor_hub import PhoneSample
    return PhoneSample(
        heading_deg=None, heading_acc=None,
        lat=lat, lon=lon, h_acc=5.0,
        bump=True, received_at=t,
    )


def test_bump_suppressed_when_confirmed_off_rig():
    """Phone ~7 km from base → at_rig is False → bump emit must be suppressed."""
    from wavecam.sensor_hub import SensorHub

    fake_events = _make_fake_events()
    fake_cfg = _make_fake_cfg()
    # base at 21.0, -157.0; phone ~7 km away
    base_pos = (21.0, -157.0, 5.0)
    hub = SensorHub(
        events=fake_events,
        cfg=fake_cfg,
        base_pos=lambda: base_pos,
    )
    sample = _bump_sample(lat=21.05, lon=-157.05)
    hub.ingest(sample)

    assert fake_events.recorded == [], (
        f"Expected 0 emits for off-rig phone, got: {fake_events.recorded}"
    )


def test_bump_fires_when_base_pos_unknown():
    """base_pos provider returns None → at_rig is None → monitors must still run."""
    from wavecam.sensor_hub import SensorHub

    fake_events = _make_fake_events()
    fake_cfg = _make_fake_cfg()
    hub = SensorHub(
        events=fake_events,
        cfg=fake_cfg,
        base_pos=lambda: None,   # no base fix available
    )
    sample = _bump_sample(lat=21.0, lon=-157.0)
    hub.ingest(sample)

    assert len(fake_events.recorded) == 1, (
        f"Expected 1 bump emit when base_pos unknown, got: {fake_events.recorded}"
    )
    assert fake_events.recorded[0][0] == "anchor_suspect"
    assert fake_events.recorded[0][1]["reason"] == "bump"
