"""Multi-point offset refine — manager-level accumulate behavior. Each accumulate
aim adds a (pan_enc, bearing) sample, refits the pan anchor, and reports the sample
count + residual. `replace` (default) is the single-aim behavior and clears the buffer;
`reset_offset_samples()` clears it explicitly."""
from tests.test_calibration_offset import _FakePtz, _json, _manager


def _aim(m, mode, target_lat, target_lon):
    return m.offset_calibrate({
        "operator_accepted": True, "mode": mode,
        "target_lat": target_lat, "target_lon": target_lon, "source": "test",
    })


def test_accumulate_grows_samples_and_reports_residual():
    m = _manager(pan=500.0, tilt=-40.0)
    b1 = _json(_aim(m, "accumulate", 21.60072, -158.0))      # ~north
    assert b1["ok"] is True
    assert b1["sample_count"] == 1
    assert "rms_residual_deg" in b1

    m.pipeline.ptz = _FakePtz(1500.0, -42.0)                 # camera now aimed elsewhere
    b2 = _json(_aim(m, "accumulate", 21.6, -157.999))        # ~east
    assert b2["ok"] is True
    assert b2["sample_count"] == 2
    assert m.pipeline.pose.calibrated is True                # multi-point fit applied
    # the refine summary also rides in the session state (so the iOS readout re-renders)
    hl = m._session["heading_lock"]
    assert hl["sample_count"] == 2
    assert "rms_residual_deg" in hl


def test_replace_mode_clears_the_sample_buffer():
    m = _manager()
    _aim(m, "accumulate", 21.60072, -158.0)                  # 1 sample
    _aim(m, "replace", 21.60072, -158.0)                     # single aim resets accumulation
    after = _json(_aim(m, "accumulate", 21.60072, -158.0))
    assert after["sample_count"] == 1


def test_default_mode_is_replace_and_omits_sample_count():
    m = _manager()
    b = _json(m.offset_calibrate({"operator_accepted": True,
                                  "target_lat": 21.60072, "target_lon": -158.0, "source": "test"}))
    assert b["ok"] is True
    assert b.get("sample_count") is None                     # back-compat single-aim payload


def test_reset_clears_samples():
    m = _manager()
    _aim(m, "accumulate", 21.60072, -158.0)
    m.reset_offset_samples()
    after = _json(_aim(m, "accumulate", 21.60072, -158.0))
    assert after["sample_count"] == 1
