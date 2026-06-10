# tools/regen_api_snapshot.py — python3 tools/regen_api_snapshot.py from orin/wavecam
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_control_api import DummyPipeline
from wavecam.web import build_app

spec = build_app(DummyPipeline()).openapi()
out = os.path.join(os.path.dirname(__file__), "..", "..", "..", "docs", "api", "openapi.snapshot.json")
with open(out, "w") as f:
    json.dump(sorted(spec["paths"].keys()), f, indent=1)
print(f"wrote {len(spec['paths'])} paths")
