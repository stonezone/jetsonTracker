# WaveCam Backend Anti-Vibe Review Findings

Date: 2026-06-03
Scope: `orin/wavecam/wavecam`, `orin/wavecam/tests`
Output: findings only; no live Orin mutation.

## Reviewed

- `orin/wavecam/wavecam/control_api.py`
- `orin/wavecam/wavecam/web.py`
- `orin/wavecam/wavecam/pipeline.py`
- `orin/wavecam/wavecam/fusion.py`
- `orin/wavecam/wavecam/overlay.py`
- `orin/wavecam/wavecam/ptz_owner.py`
- `orin/wavecam/wavecam/auth.py`
- `orin/wavecam/wavecam/recorder.py`
- Backend tests under `orin/wavecam/tests`

## Findings

### P1: Auth-enabled API leaves legacy control routes unauthenticated

Status: validated.
Confidence: high.
Fix feasibility: 9/10.

`install_auth()` protects the `/api/v1` routes through explicit `Depends(require(...))` dependencies in `control_api.py`, but the legacy web-console command routes in `web.py` have no equivalent guard:

- `orin/wavecam/wavecam/web.py:282` `/kill`
- `orin/wavecam/wavecam/web.py:287` `/resume`
- `orin/wavecam/wavecam/web.py:292` `/ptz/stop`
- `orin/wavecam/wavecam/web.py:301` `/ptz/zin`
- `orin/wavecam/wavecam/web.py:308` `/ptz/zout`
- `orin/wavecam/wavecam/web.py:315` `/ptz/zstop`
- `orin/wavecam/wavecam/web.py:320` `/tune`

Validation:

```text
Auth enabled, no bearer token:
/kill 200 {"killed":true}
/resume 200 {"killed":false}
/ptz/zin 200 {"ok":true}
/tune 200 {"ok":true,"patch":{"ptz.deadzone":0.1}}
/api/v1/status 401
```

Impact: when auth is enabled, unauthenticated LAN callers can still kill/resume the system, send zoom commands, and mutate tuning through legacy routes. That defeats the operator/viewer/supervisor role gate for the highest-risk bring-up endpoints.

Recommended fix: either add auth dependencies to the legacy routes or disable legacy mutation routes when auth is enabled. Keep `/` and `/stream.mjpg` policy explicit. Add tests that enable auth and assert unauthenticated legacy mutation routes return 401 or 403.

### P1: Failed hot-config batch can still mutate live config

Status: validated.
Confidence: high.
Fix feasibility: 8/10.

`apply_hot_config()` applies patch keys sequentially and returns on the first refusal:

- `orin/wavecam/wavecam/control_api.py:469`
- `orin/wavecam/wavecam/control_api.py:476`

The route bumps revision only after the full patch returns no refusal:

- `orin/wavecam/wavecam/control_api.py:290`
- `orin/wavecam/wavecam/control_api.py:295`

Validation:

```text
POST /api/v1/config/hot
patch={"ptz.deadzone": 0.11, "camera.source": "rtsp://x"}

response 422 invalid_request
revision_before_after 0 0
deadzone_before_after 0.08 0.11
```

Impact: a client can receive `422 invalid_request` and no revision increment while one or more earlier keys have already changed live runtime behavior. The iOS app and telemetry can miss the change because revision stays stable.

Recommended fix: make hot-config application atomic. Validate the entire patch before mutating, or apply to a temporary config copy and commit only if every key passes. Add regression coverage for mixed valid+invalid patches and invalid value-after-valid-key patches.

### P1: Manual zoom under Auto owner bypasses the deadman stop

Status: validated.
Confidence: high.
Fix feasibility: 8/10.

`POST /api/v1/ptz/zoom` accepts `deadman_ms`, but when an autonomous owner currently holds PTZ, the route sends zoom and returns before scheduling any deadman timer:

- `orin/wavecam/wavecam/control_api.py:236`
- `orin/wavecam/wavecam/control_api.py:244`
- `orin/wavecam/wavecam/control_api.py:245`
- `orin/wavecam/wavecam/control_api.py:247`

Validation:

```text
owner=testbed
POST /api/v1/ptz/zoom value=0.5 deadman_ms=100
calls_after_post [('zoom', 'tele', 4)]
calls_after_deadman [('zoom', 'tele', 4)]
```

Impact: if the app or network drops before sending a zero-value zoom command, the continuous VISCA zoom command can keep running until another stop command or camera limit. Pan/tilt deadman behavior is safer than this zoom path.

Recommended fix: schedule a zoom-stop deadman for nonzero manual zoom even when autonomous pan/tilt owner remains `testbed`, or route manual zoom through a dedicated zoom deadman independent of pan/tilt ownership. Add a regression test mirroring `test_api_v1_safety_kill_cancels_manual_deadman_before_resume`.

### P2: Hot-config request exposes unused `revision` and `persist` fields

Status: validated.
Confidence: high.
Fix feasibility: 9/10.

`HotConfigRequest` accepts `revision` and `persist`, but `config_hot()` ignores both:

- `orin/wavecam/wavecam/control_api.py:134`
- `orin/wavecam/wavecam/control_api.py:135`
- `orin/wavecam/wavecam/control_api.py:137`
- `orin/wavecam/wavecam/control_api.py:290`

Validation:

```text
initial revision 0
POST /api/v1/config/hot revision=999 persist=true patch={"ptz.deadzone": 0.11}
status 200 ok=True revision_after 1
cfg deadzone 0.11
```

Impact: clients can believe stale-write protection or persistence exists when it does not. This is lower severity than the partial-mutation bug, but it is still a contract defect.

Recommended fix: either implement revision conflict handling and persistence, or remove/reject those fields. The fastest honest fix is rejecting `persist=true` with `422` and documenting that `revision` is response-only until optimistic concurrency is implemented.

### P3: `control_api.py` is doing too many jobs

Status: validated structural smell, not a failing behavior by itself.
Confidence: medium.
Refactor feasibility: 6/10.

The scanner flagged `control_api.py` as 816 logical lines, with `ControlApiAdapter` at 323 lines and 30 methods. Direct inspection confirms it combines route registration, PTZ ownership orchestration, manual deadman behavior, config validation/mutation, media facade, restart scheduling, status shaping, GPS normalization, and helper validation.

Impact: current tests cover important behavior, but future changes will keep touching the same file for unrelated reasons. That raises the odds of hidden behavior changes, especially around safety, config, and status shape.

Recommended split, in this order:

1. Move hot-config key definitions, validation, and atomic apply logic into a `config_api.py` or `hot_config.py` module.
2. Move status/config snapshot builders into a `snapshots.py` module.
3. Keep FastAPI route registration thin in `control_api.py`.
4. Keep PTZ/deadman orchestration in a dedicated command adapter with explicit tests.

Do not start this split until the three P1 defects above are fixed or explicitly deferred.

## Non-Findings

- `fusion.py:update()` is long but cohesive. It owns target selection state and remains cv2-free under tests.
- `overlay.py:annotate()` is long but isolated rendering code. It is a cleanup target, not a current behavior risk.
- `pipeline.py:run()` is long but is the expected capture/detect/fusion/servo loop. The code is readable enough for now; extract only after safety/API defects are closed.

## Verification Commands

```text
python3 ~/.codex/skills/anti-vibe-engineering/scripts/python_quality_scan.py orin/wavecam/wavecam orin/wavecam/tests
PYTHONPATH=orin/wavecam python3 - <<'PY' ... auth bypass validation ... PY
PYTHONPATH=orin/wavecam python3 - <<'PY' ... autonomous zoom deadman validation ... PY
PYTHONPATH=orin/wavecam python3 - <<'PY' ... mixed hot-config mutation validation ... PY
PYTHONPATH=orin/wavecam python3 - <<'PY' ... stale revision/persist validation ... PY
```
