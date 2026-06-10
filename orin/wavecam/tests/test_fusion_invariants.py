import types
from wavecam import fusion
from wavecam.fusion import Fusion


def _cfg(lock=0.6, unlock=0.35, gps_boost=0.2):
    return types.SimpleNamespace(lock_threshold=lock, unlock_threshold=unlock,
                                 require_person=False, match_dist=120,
                                 person_aim_x=0.5, person_aim_y=0.5, ema_alpha=0.5,
                                 lost_grace_sec=0.8, gps_boost=gps_boost,
                                 gps_boost_radius_frac=0.25)


def _blob(cx=320.0, cy=240.0):
    return types.SimpleNamespace(cx=cx, cy=cy, bbox=(int(cx) - 10, int(cy) - 10, 20, 20))


def test_threshold_ordering_is_a_hysteresis_band():
    c = _cfg()
    assert c.unlock_threshold < fusion.CONF_SUSTAIN < c.lock_threshold, \
        "sustain must sit INSIDE the hysteresis band — above unlock, below lock"


def test_color_only_cannot_acquire_without_gps_cue():
    f = Fusion(_cfg())
    r = f.update([_blob()], [])
    assert r.conf == fusion.CONF_SUSTAIN and not r.locked


def test_color_only_acquires_WITH_gps_cue():
    f = Fusion(_cfg())
    r = f.update([_blob(320, 240)], [], gps_cue_px=(320.0, 240.0, 120.0))
    assert r.conf >= _cfg().lock_threshold and r.locked, \
        "the GPS-cued path is the designed acquisition route for color-only"


def test_person_only_neither_acquires_nor_sustains():
    assert fusion.CONF_PERSON_ONLY < _cfg().unlock_threshold


def test_matched_person_acquires_at_modest_confidence():
    p = types.SimpleNamespace(xywh=(310, 230, 20, 20), center=(320.0, 240.0), conf=0.3)
    f = Fusion(_cfg())
    r = f.update([_blob(320, 240)], [p])
    assert r.matched and r.conf >= _cfg().lock_threshold
