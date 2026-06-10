import json, os, re, sys

sys.path.insert(0, os.path.dirname(__file__))

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..")
SNAP = os.path.join(ROOT, "docs", "api", "openapi.snapshot.json")
SWIFT_CLIENTS = [
    os.path.join(ROOT, "ios", "WaveCam", "Sources", "WaveCamClient.swift"),
    os.path.join(ROOT, "ios", "WaveCam", "Sources-Watch", "WatchClient.swift"),
]
# getWithFallback("calibration") / post("safety/kill", ...) / sendCalibrationCapture("calibration/base-lock", ...)
# — literal first args only; dynamic interpolations are accepted scope
CALL_RE = re.compile(r'(?:getWithFallback|post|sendCalibrationCapture)\(\s*"([^"\\]+)"')


def _client_paths():
    found = set()
    for path in SWIFT_CLIENTS:
        if not os.path.exists(path):
            continue
        for m in CALL_RE.finditer(open(path).read()):
            found.add("/api/v1/" + m.group(1))
    return found


def test_every_ios_call_targets_a_real_route():
    with open(SNAP) as f:
        snap = set(json.load(f))
    missing = sorted(p for p in _client_paths() if p not in snap)
    assert not missing, (
        f"iOS/watch clients call routes the backend does not serve: {missing}\n"
        "(This is the base-lock bug class. Fix the client or add the route + regen snapshot.)"
    )


def test_extractor_finds_known_calls():
    paths = _client_paths()
    assert "/api/v1/safety/kill" in paths and "/api/v1/calibration/base-lock" in paths
