"""Unit tests for PhoneSample dataclass fields — altitude + true heading (Task 1)."""


def test_phone_sample_carries_altitude_and_true_heading():
    from wavecam.sensor_hub import PhoneSample
    s = PhoneSample(heading_deg=100.0, heading_acc=3.0, lat=21.0, lon=-157.0,
                    h_acc=4.0, bump=False, received_at=1.0,
                    true_heading_deg=102.5, alt_m=12.4, alt_acc=6.0, baro_rel_m=0.2)
    assert s.true_heading_deg == 102.5
    assert s.alt_m == 12.4 and s.alt_acc == 6.0 and s.baro_rel_m == 0.2
