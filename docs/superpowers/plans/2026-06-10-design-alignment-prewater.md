# Design Alignment — Pre-Water Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the four zero-field-risk design optimizations (config truth, contract tests, confidence invariants, observability) before the first water session, and define the entry gates for the two field-gated plans (closed-loop pointing, target estimator).

**Architecture:** All backend work in `orin/wavecam/` (FastAPI control API + pipeline thread), iOS in `ios/WaveCam/` (SwiftUI, xcodegen). No control-loop behavior changes anywhere in this plan — only truth-sources, tests, and telemetry. Everything is additive and feature-detected on the iOS side.

**Tech Stack:** Python 3.10+/FastAPI/pytest (backend), Swift/SwiftUI iOS 17+ (xcodegen, build via `ios/WaveCam/build-device.sh`), yaml via PyYAML (already a dep).

**Ground rules (project):** stage files explicitly; never `git add -A`; failing test first; never weaken a test; backend deploys to the rig only via the new deploy script after Zack/agent authorization; commit messages explain why; end commits with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

**Baseline state (verify before starting):** branch from `main` (everything through PR #16/#17 merged). `cd orin/wavecam && python3 -m pytest -q` → `173 passed`. Rig runs `6db99a5`-equivalent main.

---

## Phase A — #3 Config Truth (Tasks 1-4)

### Task 1: `/version` endpoint

**Files:**
- Modify: `orin/wavecam/wavecam/control_api.py` (route registration near `register_guide_routes`, ~line 269)
- Test: `orin/wavecam/tests/test_version.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_version.py
from fastapi.testclient import TestClient
from test_control_api import DummyPipeline
from wavecam.web import build_app


def test_version_reports_unknown_without_stamp(tmp_path, monkeypatch):
    monkeypatch.setenv("WAVECAM_VERSION_PATH", str(tmp_path / "version.json"))
    client = TestClient(build_app(DummyPipeline()))
    r = client.get("/api/v1/version")
    assert r.status_code == 200
    body = r.json()
    assert body["git_sha"] is None and body["deployed_at"] is None


def test_version_reports_stamp(tmp_path, monkeypatch):
    p = tmp_path / "version.json"
    p.write_text('{"git_sha": "abc1234", "branch": "main", "deployed_at": "2026-06-10T00:00:00Z"}')
    monkeypatch.setenv("WAVECAM_VERSION_PATH", str(p))
    client = TestClient(build_app(DummyPipeline()))
    body = client.get("/api/v1/version").json()
    assert body["git_sha"] == "abc1234"
    assert body["branch"] == "main"
```

- [ ] **Step 2: Run it — must fail 404**: `cd orin/wavecam && python3 -m pytest tests/test_version.py -q` → FAIL (404).

- [ ] **Step 3: Implement.** In `control_api.py` add beside the guide routes:

```python
def register_version_routes(app: FastAPI) -> None:
    @app.get("/api/v1/version", dependencies=[Depends(require(READ))])
    def version():
        path = os.environ.get(
            "WAVECAM_VERSION_PATH",
            os.path.join(os.path.dirname(__file__), "..", "version.json"),
        )
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        return {
            "git_sha": data.get("git_sha"),
            "branch": data.get("branch"),
            "deployed_at": data.get("deployed_at"),
        }
```

Call `register_version_routes(app)` next to the existing `register_calibration_routes(app, adapter)` call (~line 265). `os`/`json` are already imported in this module — verify, don't re-import.

- [ ] **Step 4: Tests pass**: same command → 2 passed. Full suite still green: `python3 -m pytest -q` → 175 passed.
- [ ] **Step 5: Commit** (`git add orin/wavecam/wavecam/control_api.py orin/wavecam/tests/test_version.py`): `feat: /version endpoint — kills "committed != deployed" ambiguity`.

### Task 2: Committed deploy script (stamps version, safe rsync, health-gate)

**Files:**
- Create: `orin/wavecam/deploy.sh` (chmod +x)

- [ ] **Step 1: Write the script** (no test framework — Step 2 is its verification):

```bash
#!/usr/bin/env bash
# Deploy orin/wavecam to the rig. The ONLY sanctioned deploy path.
# Usage: ./deploy.sh [--dry-run]
set -euo pipefail
cd "$(dirname "$0")"

HOST=orin
DEST=/data/projects/gimbal/wavecam
SHA=$(git rev-parse --short HEAD)
BRANCH=$(git rev-parse --abbrev-ref HEAD)
DIRTY=$(git status --porcelain -- . | head -1)
[ -n "$DIRTY" ] && { echo "REFUSED: orin/wavecam has uncommitted changes"; exit 1; }

printf '{"git_sha": "%s", "branch": "%s", "deployed_at": "%s"}\n' \
  "$SHA" "$BRANCH" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > version.json

RSYNC_FLAGS=(-av --delete
  --exclude '__pycache__' --exclude '*.pyc'
  --exclude 'camera_pose.json'          # persisted calibration — rig-owned
  --exclude 'auth.json'                 # rig-owned credentials
  --exclude '*.log')
[ "${1:-}" = "--dry-run" ] && RSYNC_FLAGS+=(--dry-run)

ssh $HOST "cp -a $DEST ${DEST}.bak-\$(date +%Y%m%d-%H%M) 2>/dev/null || true"
rsync "${RSYNC_FLAGS[@]}" ./ "$HOST:$DEST/"
rm version.json
[ "${1:-}" = "--dry-run" ] && { echo "dry-run only"; exit 0; }

ssh $HOST 'sudo systemctl restart wavecam.service'
sleep 12
ssh $HOST 'systemctl is-active wavecam.service'
DEPLOYED=$(ssh $HOST "curl -s localhost:8088/api/v1/version" | python3 -c 'import json,sys; print(json.load(sys.stdin)["git_sha"])')
[ "$DEPLOYED" = "$SHA" ] && echo "DEPLOY OK: $SHA live" || { echo "DEPLOY MISMATCH: rig=$DEPLOYED local=$SHA"; exit 1; }
```

- [ ] **Step 2: Verify offline**: `bash -n orin/wavecam/deploy.sh` (syntax) and `./orin/wavecam/deploy.sh --dry-run` AFTER committing (it refuses dirty trees — that's the test of the guard too: run once before committing and expect `REFUSED`).
- [ ] **Step 3: Commit**: `feat: committed deploy script — atomic code+version, rig-owned files excluded`.

### Task 3: Hot-config writes back to the rig yaml

**Files:**
- Modify: `orin/wavecam/wavecam/config.py` (add `source_path` + persist helper)
- Modify: `orin/wavecam/wavecam/control_api.py` (call persist after hot apply)
- Modify: `orin/wavecam/run.py` (~line 70: pass the path)
- Test: `orin/wavecam/tests/test_config_persist.py` (create)

- [ ] **Step 1: Failing test**

```python
# tests/test_config_persist.py
import yaml
from wavecam.config import persist_hot_values


def test_persist_hot_values_round_trips(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("gps:\n  enabled: true\n  stale_threshold_sec: 10\nfusion:\n  gps_boost: 0.2\n")
    persist_hot_values(str(p), {"gps.stale_threshold_sec": 7, "fusion.gps_boost": 0.3})
    data = yaml.safe_load(p.read_text())
    assert data["gps"]["stale_threshold_sec"] == 7
    assert data["fusion"]["gps_boost"] == 0.3
    assert data["gps"]["enabled"] is True          # untouched keys survive


def test_persist_creates_missing_section(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("gps:\n  enabled: true\n")
    persist_hot_values(str(p), {"fusion.gps_boost": 0.25})
    assert yaml.safe_load(p.read_text())["fusion"]["gps_boost"] == 0.25
```

- [ ] **Step 2: Run — fails on import.**
- [ ] **Step 3: Implement in `config.py`:**

```python
import os
import threading

_persist_lock = threading.Lock()


def persist_hot_values(yaml_path: str, values: dict) -> None:
    """Write hot-applied config keys back to the live yaml (atomic replace) so the
    file on the rig is always the single source of truth. ``values`` maps dotted
    keys ("gps.stale_threshold_sec") to plain scalars."""
    import yaml as _yaml
    with _persist_lock:
        with open(yaml_path, encoding="utf-8") as f:
            data = _yaml.safe_load(f) or {}
        for dotted, v in values.items():
            section, key = dotted.split(".", 1)
            data.setdefault(section, {})[key] = v
        tmp = yaml_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp, yaml_path)
```

Also: in `load_config(path)` set `cfg.source_path = path` on the returned object (add the field to the Config dataclass: `source_path: str = ""`).

- [ ] **Step 4: Wire the call site.** In `control_api.py`, find the POST `config/hot` handler (search `"config/hot"`). After the loop that successfully applies keys (it accumulates applied key/value pairs — Sonnet's 2026-06-10 hot-key work added `apply_gps_*` helpers there), add:

```python
        src = getattr(getattr(self.pipeline, "cfg", None), "source_path", "")
        if src and applied:           # applied: the dict of successfully set dotted keys
            try:
                persist_hot_values(src, applied)
            except Exception as e:
                print(f"[control_api] hot-config persist failed (live value still applied): {e}")
```

If the handler doesn't already collect an `applied` dict, add one (key → final coerced value) in the apply loop. Import `persist_hot_values` at the top with the other config imports. **Behavior contract: persist failure must NOT fail the request** — the in-memory apply already succeeded.

- [ ] **Step 5: Integration test** (append to `test_config_persist.py`) — drive the real endpoint with `DummyPipeline` whose `cfg.source_path` points at a tmp yaml, POST a hot key, assert the yaml changed. Copy the request shape from the existing hot-key tests in `tests/test_gps_fusion_cue.py`.
- [ ] **Step 6: Full suite green; commit**: `feat: hot-config persists to the rig yaml — the file is the single truth`.

### Task 4: Unified calibration store (kills the split-brain)

**Files:**
- Create: `orin/wavecam/wavecam/calibration_store.py`
- Modify: `orin/wavecam/wavecam/control_api.py` (adapter `_calibration` dict + `_save_pose` replaced; `calibration_state()` reads the store)
- Test: `orin/wavecam/tests/test_calibration_store.py` (create)

- [ ] **Step 1: Failing tests — the exact historical bug as a test:**

```python
# tests/test_calibration_store.py
from wavecam.calibration_store import CalibrationStore


def test_reference_heading_survives_restart(tmp_path):
    p = str(tmp_path / "calibration.json")
    s = CalibrationStore.load(p)
    s.pose.calibrate_pan_aim(enc=100.0, bearing_deg=247.0, enc_per_deg=4.47)
    s.set_step("heading", {"heading_deg": 247.0})
    s.save()
    s2 = CalibrationStore.load(p)                  # simulated restart
    assert s2.pose.calibrated
    assert s2.reference_heading == 247.0           # was None after restart pre-fix
    assert s2.steps["heading"]["heading_deg"] == 247.0


def test_load_migrates_legacy_pose_only_json(tmp_path):
    import json
    p = tmp_path / "calibration.json"
    p.write_text(json.dumps({"lat": 21.6, "lon": -158.0, "alt_m": 3.0,
                             "pan_anchor_enc": 0.0, "pan_anchor_bearing": 0.0,
                             "pan_enc_per_deg": 4.47, "tilt_anchor_enc": 0.0,
                             "tilt_anchor_elev": 0.0, "tilt_enc_per_deg": 0.0}))
    s = CalibrationStore.load(str(p))
    assert s.pose.has_base and s.pose.calibrated
```

- [ ] **Step 2: Run — fails on import.**
- [ ] **Step 3: Implement `calibration_store.py`:**

```python
"""One persisted document for ALL calibration state — the pose mapping AND the
per-step capture log AND reference_heading. Replaces the adapter's in-memory
_calibration dict + separate camera_pose.json, whose split produced
"gps_calibrated true but reference_heading null" after every restart."""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from .camera_pose import CameraPose

_POSE_FIELDS = set(CameraPose.__dataclass_fields__)


@dataclass
class CalibrationStore:
    path: str
    pose: CameraPose = field(default_factory=CameraPose)
    reference_heading: Optional[float] = None
    steps: dict = field(default_factory=dict)       # step name -> capture entry
    updated_at_unix_ms: Optional[int] = None

    def set_step(self, step: str, entry: dict) -> None:
        now = int(time.time() * 1000)
        self.steps[step] = {**entry, "captured_at_unix_ms": now}
        self.updated_at_unix_ms = now
        if step == "heading" and "heading_deg" in entry:
            self.reference_heading = entry["heading_deg"]

    def save(self) -> None:
        doc = {"pose": asdict(self.pose), "reference_heading": self.reference_heading,
               "steps": self.steps, "updated_at_unix_ms": self.updated_at_unix_ms}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2)
        os.replace(tmp, self.path)

    @classmethod
    def load(cls, path: str) -> "CalibrationStore":
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except Exception:
            return cls(path=path)
        if "pose" not in doc and _POSE_FIELDS & set(doc):
            return cls(path=path, pose=CameraPose(**{k: v for k, v in doc.items()
                                                     if k in _POSE_FIELDS}))   # legacy migration
        return cls(path=path,
                   pose=CameraPose(**doc.get("pose", {})),
                   reference_heading=doc.get("reference_heading"),
                   steps=doc.get("steps", {}),
                   updated_at_unix_ms=doc.get("updated_at_unix_ms"))
```

- [ ] **Step 4: Adapter swap** in `control_api.py` — mechanical but careful:
  - `__init__` (~line 614): replace `self._calibration = empty_calibration_state()` + pose-load block with `self._store = CalibrationStore.load(_pose_path)` and `self.pipeline.pose = self._store.pose` (the pipeline keeps pointing at the SAME pose object).
  - `capture_calibration` (~line 723): replace `self._calibration[step] = entry` bookkeeping with `self._store.set_step(step, values)`; replace every `self._save_pose()` with `self._store.save()`; delete `_save_pose` and `empty_calibration_state` once nothing references them.
  - `calibration_state()` (~line 691): read `self._store.reference_heading`, `self._store.steps.get("heading")` etc. — keep the returned JSON shape IDENTICAL (iOS decodes it; the contract test from Task 6 will hold you to it).
  - Default path: keep honoring `WAVECAM_POSE_PATH` env (now pointing at the unified file; the legacy-migration branch in `load` makes old rig files Just Work).
- [ ] **Step 5: Full suite** — the existing calibration tests in `test_control_api.py` must pass UNCHANGED (shape-compatible is the requirement). New store tests pass. Run `python3 -m pytest -q`.
- [ ] **Step 6: Commit**: `refactor: single persisted calibration document — restart can no longer split pose from reference_heading`.

---

## Phase B — #6 Contract Tests + #5 Confidence Invariants (Tasks 5-7)

### Task 5: OpenAPI snapshot + backend contract test

**Files:**
- Create: `docs/api/openapi.snapshot.json`
- Create: `orin/wavecam/tests/test_api_contract.py`
- Create: `orin/wavecam/tools/regen_api_snapshot.py`

- [ ] **Step 1: The regen tool** (used now and whenever routes change deliberately):

```python
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
```

(Snapshot = sorted path list only — method/shape drift is real but paths are the bug class that has actually bitten; YAGNI.)

- [ ] **Step 2: Generate the first snapshot**, eyeball it (expect ~30-40 paths incl. `/api/v1/calibration/base-lock`, `/api/v1/version`), commit it.
- [ ] **Step 3: The failing-by-design test:**

```python
# tests/test_api_contract.py
import json, os
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
```

- [ ] **Step 4: Verify it passes, then verify it CATCHES** — temporarily comment out `register_version_routes(app)`, run, expect the removed-path failure, restore.
- [ ] **Step 5: Commit**: `test: API route snapshot — route drift is now a commit-time failure`.

### Task 6: iOS/watch client paths checked against the snapshot

**Files:**
- Create: `orin/wavecam/tests/test_ios_contract.py`

- [ ] **Step 1: The test (extractor + assertion in one file):**

```python
# tests/test_ios_contract.py
import json, os, re

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..")
SNAP = os.path.join(ROOT, "docs", "api", "openapi.snapshot.json")
SWIFT_CLIENTS = [
    os.path.join(ROOT, "ios", "WaveCam", "Sources", "WaveCamClient.swift"),
    os.path.join(ROOT, "ios", "WaveCam", "Sources-Watch", "WatchClient.swift"),
]
# getWithFallback("calibration") / post("safety/kill", ...) — literal first args only
CALL_RE = re.compile(r'(?:getWithFallback|post)\(\s*"([^"\\]+)"')


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
```

Dynamic/interpolated paths (e.g. media file URLs) are invisible to the literal regex — that's accepted scope; the second test guards against the extractor silently rotting.

- [ ] **Step 2: Run — both must pass against current code.** Then verify it CATCHES: temporarily change `"calibration/base-lock"` to `"calibration/base-locked"` in `WaveCamClient.swift`, run, expect failure, revert.
- [ ] **Step 3: Commit**: `test: iOS/watch client paths verified against the API snapshot`.

### Task 7: Fusion confidence — named constants + invariant tests

**Files:**
- Modify: `orin/wavecam/wavecam/fusion.py` (constants only — zero behavior change)
- Create: `orin/wavecam/tests/test_fusion_invariants.py`

- [ ] **Step 1: Extract constants** at module level in `fusion.py`, replacing the literals in `_select` (lines ~104-137):

```python
# Confidence model (see test_fusion_invariants.py for the asserted semantics):
CONF_MATCHED_BASE = 0.5     # color+person agree: 0.5 + 0.5*person_conf — acquires
CONF_SUSTAIN = 0.45         # color-only / person-near-track: holds a lock, never starts one
CONF_PERSON_ONLY = 0.2      # person with no color: never holds, never starts
CONF_BOOST_CAP = 0.95
```

- [ ] **Step 2: Invariant tests** — these encode the semantics that silently broke for weeks:

```python
# tests/test_fusion_invariants.py
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
```

- [ ] **Step 3: Run all fusion tests** (`pytest tests/test_fusion_invariants.py tests/test_fusion.py -q`) — green, and full suite count grows with zero failures (constants extraction must be diff-invisible to behavior).
- [ ] **Step 4: Commit**: `test: fusion confidence semantics as invariants — the dead-zone class is now asserted, not discovered`.

---

## Phase C — #4 Observability (Tasks 8-11)

### Task 8: Health registry + `/health`

**Files:**
- Create: `orin/wavecam/wavecam/health.py`
- Modify: `orin/wavecam/wavecam/pipeline.py` (3 beat calls in `run()`), `orin/wavecam/wavecam/control_api.py` (route), `orin/wavecam/run.py` (registry construction)
- Test: `orin/wavecam/tests/test_health.py`

- [ ] **Step 1: Failing test:**

```python
# tests/test_health.py
import time
from wavecam.health import HealthRegistry


def test_beat_and_staleness():
    h = HealthRegistry()
    h.beat("capture", detail={"fps": 30.1})
    snap = h.snapshot(stale_after_sec=5.0)
    assert snap["components"]["capture"]["ok"] is True
    assert snap["components"]["capture"]["detail"]["fps"] == 30.1
    assert snap["ok"] is True


def test_stale_component_flips_overall_not_ok():
    h = HealthRegistry()
    h.beat("capture")
    h._last["capture"] = (time.time() - 99, {})    # simulate silence
    snap = h.snapshot(stale_after_sec=5.0)
    assert snap["components"]["capture"]["ok"] is False and snap["ok"] is False
```

- [ ] **Step 2: Implement `health.py`:**

```python
"""Component heartbeats. Every long-lived loop calls beat(name) each cycle;
/health turns silence into a visible failure. This generalizes the
gps reader_alive retrofit — silent thread death was this project's #1
incident class (wedged API 06-08, dead reader, quiet engine degradation)."""
from __future__ import annotations

import threading
import time


class HealthRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._last: dict[str, tuple[float, dict]] = {}

    def beat(self, name: str, detail: dict | None = None) -> None:
        with self._lock:
            self._last[name] = (time.time(), detail or {})

    def snapshot(self, stale_after_sec: float = 5.0) -> dict:
        now = time.time()
        with self._lock:
            comps = {
                name: {"ok": (now - ts) < stale_after_sec,
                       "age_sec": round(now - ts, 2), "detail": detail}
                for name, (ts, detail) in self._last.items()
            }
        return {"ok": all(c["ok"] for c in comps.values()) if comps else False,
                "components": comps}
```

- [ ] **Step 3: Wire beats.** `Pipeline.__init__` gains `self.health = HealthRegistry()`. In `run()`'s loop add (after the frame read): `self.health.beat("capture", {"fps": round(fps, 1), "connected": self.grab.connected})`; after the YOLO branch: `self.health.beat("detector", {"enabled": self.detector is not None})` (each loop — TTL semantics make per-loop correct); at the end of the loop body: `self.health.beat("loop")`. GPS: the route (next step) reads `pipeline.gps.reader_alive()` directly — don't double-plumb.
- [ ] **Step 4: Route** in `control_api.py`:

```python
    @app.get("/api/v1/health", dependencies=[Depends(require(READ))])
    def health():
        reg = getattr(api.pipeline, "health", None)
        snap = reg.snapshot() if reg else {"ok": False, "components": {}}
        gps = getattr(api.pipeline, "gps", None)
        if gps is not None:
            alive = gps.reader_alive() if callable(getattr(gps, "reader_alive", None)) else None
            age = gps.last_poll_age_sec() if callable(getattr(gps, "last_poll_age_sec", None)) else None
            snap["components"]["gps_reader"] = {"ok": bool(alive), "age_sec": age, "detail": {}}
            snap["ok"] = snap["ok"] and bool(alive)
        try:
            import shutil
            free_gb = shutil.disk_usage(str(api.pipeline.recorder.config.rec_dir)).free / 1e9
            snap["components"]["disk"] = {"ok": free_gb > 5.0, "age_sec": 0,
                                          "detail": {"free_gb": round(free_gb, 1)}}
        except Exception:
            pass
        return snap
```

Register it; `DummyPipeline` in tests gets `self.health = HealthRegistry()` added (one line — same fixture pattern as `pose`/`gps`). Add an endpoint test asserting `capture`/`disk` keys appear after a beat. Jetson thermals: SKIP in this task (YAGNI until someone asks; `/sys` reads differ per L4T release — note for the field).
- [ ] **Step 5: Regen the API snapshot** (Task 5's tool — `/health` + nothing else new), commit both: `feat: /health — every loop heartbeats; silence is now visible`.

### Task 9: Event ring + `/events`

**Files:**
- Create: `orin/wavecam/wavecam/events.py`
- Modify: `orin/wavecam/wavecam/pipeline.py` (transition records), `control_api.py` (route + kill/resume records)
- Test: `orin/wavecam/tests/test_events.py`

- [ ] **Step 1: Failing test:**

```python
# tests/test_events.py
from wavecam.events import EventRing


def test_ring_records_and_filters_by_since():
    r = EventRing(maxlen=4)
    r.record("lock", "acquired", t=100.0)
    r.record("owner", "vision_follow", t=101.0)
    assert [e["kind"] for e in r.since(0)] == ["lock", "owner"]
    assert [e["detail"] for e in r.since(100.5)] == ["vision_follow"]


def test_ring_drops_oldest():
    r = EventRing(maxlen=2)
    for i in range(3):
        r.record("k", str(i), t=float(i))
    assert [e["detail"] for e in r.since(0)] == ["1", "2"]
```

- [ ] **Step 2: Implement `events.py`** (deque(maxlen) + lock; `record(kind, detail, t=None)` stamps `time.time()` when t is None and also `logging.info("[event] %s %s", kind, detail)` so journalctl keeps a permanent copy; `since(ts)` returns `[{"t":…, "kind":…, "detail":…}]`).
- [ ] **Step 3: Wire transitions in `pipeline.run()`** — pipeline already tracks `prev_state`/`self._arbiter_state` (~line 316) and computes `fr.locked` per frame. Keep `self._prev_locked` and `self._prev_gps_viable` attributes; on change, `self.events.record("lock", "acquired"/"lost")`, `("owner", decision.owner)`, `("gps", "viable"/"unviable")`. KILL/RESUME: record in `Pipeline.kill()`. `Pipeline.__init__`: `self.events = EventRing(maxlen=500)`.
- [ ] **Step 4: Route** `GET /api/v1/events?since=<unix_ts>` returning `{"events": pipeline.events.since(since)}`; DummyPipeline gains `self.events = EventRing()`; endpoint test: kill via the API → `/events` contains a kill record. Regen API snapshot.
- [ ] **Step 5: Commit**: `feat: structured event ring — field tuning gets evidence, not vibes`.

### Task 10: iOS — health card + session log

**Files:**
- Modify: `ios/WaveCam/Sources/WaveCamClient.swift` (DTOs + two GETs)
- Modify: `ios/WaveCam/Sources/ConnectionView.swift` (HealthCard)
- Modify: `ios/WaveCam/Sources/ToolsView.swift` (third picker entry "Log")
- Create: `ios/WaveCam/Sources/SessionLogView.swift`

- [ ] **Step 1: Client.** `WCHealth` (`ok: Bool?`, `components: [String: WCComponent]?`; `WCComponent` = `ok/ageSec/detail([String:JSONValue]?)`) and `WCEvent` (`t: Double?`, `kind/detail: String?`) — all optionals, snake_case decoding free. `func health() async -> WCHealth?` and `func events(since: Double) async -> [WCEvent]?` via `getWithFallback("health")` / `getWithFallback("events?since=\(since)")` — note: the contract-test regex reads the literal up to the first quote, so call it as `getWithFallback("events", query: ...)` ONLY if such a helper exists; otherwise use the literal `"health"` pattern and accept `events?since=` is dynamic (the snapshot has `/api/v1/events`; add `"events"` as a separate literal call building the query with URLComponents so the extractor still sees it).
- [ ] **Step 2: HealthCard in ConnectionView** under the status card: one row per component (name, ok dot green/amber, age, fps/free_gb detail when present), feature-detected (`health() == nil` → hidden), refresh on appear + 5s timer. Reuse the `PreflightChecklist` row visual (checkRow pattern from CalibrateView).
- [ ] **Step 3: SessionLogView** — Tools picker gains "Log": a reversed `List` of events (`HH:mm:ss  KIND  detail`, mono font, kind-colored: lock=ok/kill=red/owner=accent), pull-to-refresh + 5s polling with `since` cursor. ~120 lines, match AgentView's list styling.
- [ ] **Step 4: Build + verify**: `./ios/WaveCam/build-device.sh build` → `** BUILD SUCCEEDED **`. Sim screenshot of Connect tab to eyeball the card.
- [ ] **Step 5: Commit**: `ios: health card + session event log — the rig's silent failures get a screen`.

### Task 11: Integration — deploy + on-rig verification (requires Zack/agent authorization)

- [ ] **Step 1:** Full local suite green; `git log --oneline` shows Tasks 1-10.
- [ ] **Step 2:** `./orin/wavecam/deploy.sh --dry-run` → inspect, then `./orin/wavecam/deploy.sh` → expect `DEPLOY OK: <sha> live` (this exercises Task 1+2 end-to-end for real).
- [ ] **Step 3:** On-rig spot-checks: `/api/v1/health` shows capture+gps_reader+disk ok; `/api/v1/events` accumulates owner/lock records while someone walks in frame; `/api/v1/version` sha matches local; hot-set `fusion.gps_boost` to 0.25 via the app, then `ssh orin grep gps_boost /data/projects/gimbal/wavecam/config.orin.servo.yaml` → `0.25` (Task 3 proof); restart the service → `GET /calibration` keeps `reference_heading` (Task 4 proof).
- [ ] **Step 4:** Install iOS build on the phone (`build-device.sh`), verify Health card + Log tab against the live rig.
- [ ] **Step 5:** Update memory (`gps-control-loop-status`) + emit collab status to codex.

---

## Phase D — Gates for the field-dependent plans (no implementation here)

### Plan 2 entry gate — #2 Closed-loop pointing (write `2026-MM-DD-closed-loop-pointing.md` when met)
- Pre-water bundle deployed; ≥1 water session done on the current stack.
- **Bench protocol that the plan will be built from** (1 hour, camera on the bench): a throwaway script blasts `pan_tilt` velocity commands at 10Hz while polling `inquire_pan_tilt` at 2/5/10Hz for 5 minutes; record reply-rate, latency distribution, any reply interleaving/corruption on the shared socket. The poller design (rate, dedicated-socket-vs-shared, backoff) falls out of those numbers — that's why the plan isn't written yet.
- Scope when written: `PtzState` poller class + last-known-encoders cache with staleness + verify-and-resend after absolute moves + `/status.ptz` gains `pan_enc/tilt_enc/enc_age_sec`.

### Plan 3 entry gate — #1 Target estimator (write `2026-MM-DD-target-estimator.md` when met)
- Plan 2 landed (encoder feedback exists). ≥2 water sessions of event-ring + GPS logs captured (#4 provides them).
- Zoom/FOV curve populated by a real zoom-calibration session (current zoom step captures a single point — the estimator's vision-bearing math needs the curve; this is a calibration-data gate, not a code gate).
- **Shadow-mode protocol the plan will mandate:** `estimator.py` (constant-velocity, 2D local-EN frame; GPS fix = position obs with R from fix age; vision = bearing/elevation obs derived from encoders+pixel+FOV) runs in the pipeline loop, NEVER commands, logs `(t, est, est_cov, would_cmd, actual_owner, actual_cmd)` to the event ring + a session file. Flip criterion, decided in advance: across ≥2 shadow sessions, estimator-would-have-pointed tracks the subject (post-hoc against footage) with fewer dropouts than the arbiter did, and no divergence events. Only then: config switch `tracking.mode: estimator|arbiter`, arbiter deleted one session after the flip sticks.

### Hardware ride-alongs (no session needed)
- [ ] 18650 (protected or holder-with-PCB) on the base Wio battery port — polarity-check against silkscreen with a meter FIRST. Kills config wipes + GPS cold-starts through Orin reboots.
- [ ] One supervised cold boot afterward to re-characterize the U-Boot stall ritual (expected: ritual remains, but is now harmless).

---

## Execution map

| Order | Tasks | Est. | Agent pattern |
|---|---|---|---|
| 1 | 1-4 (config truth) | 1 session | Sonnet implements task-by-task, Claude reviews diffs + commits |
| 2 | 5-7 (contracts + invariants) | 0.5 session | same |
| 3 | 8-10 (observability) | 1 session | same; iOS task needs the phone for final install |
| 4 | 11 (deploy + verify) | 0.5 session | Claude-supervised; rig access authorized per session |
| gate | water sessions 1-2 | — | telemetry accrues by itself now |
| 5 | Plan 2 (bench + closed loop) | 1 session | written + executed post-bench |
| 6 | Plan 3 (estimator, shadow→flip) | 3-4 sessions | written post-gate |
