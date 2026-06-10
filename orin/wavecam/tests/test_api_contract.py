import json, os, sys

sys.path.insert(0, os.path.dirname(__file__))
from test_control_api import DummyPipeline
from wavecam.web import build_app

SNAP = os.path.join(os.path.dirname(__file__), "..", "..", "..", "docs", "api", "openapi.snapshot.json")


def test_routes_match_committed_snapshot():
    live = sorted(build_app(DummyPipeline()).openapi()["paths"].keys())
    with open(SNAP) as f:
        snap = json.load(f)
    assert live == snap, (
        "API routes drifted from docs/api/openapi.snapshot.json.\n"
        f"added={sorted(set(live)-set(snap))}\nremoved={sorted(set(snap)-set(live))}\n"
        "If deliberate: python3 tools/regen_api_snapshot.py and commit both sides."
    )
