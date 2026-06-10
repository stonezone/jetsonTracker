# control_api.py Monolith Split — Implementation Plan

**Current state:** 1989 lines, `ControlApiAdapter` = 501 lines / 42 methods, plus 4 helper classes and ~30 standalone functions.

**Goal:** Split by responsibility into separate modules behind the existing FastAPI route groups. API surface, response shapes, auth guards unchanged.

**Guiding principle:** Extract one module at a time, run full test suite after each, commit. If a test breaks, the last extraction is wrong — revert and fix before continuing.

---

## Phase 0 — Safety Net (read-only, no code changes)

### 0.1 — Characterize the test baseline
- Run `pytest orin/wavecam/tests/ -v --tb=short` on the current code
- Record the exact test count and any existing skips/fails
- Save output to `docs/control-api-split-baseline.txt`

### 0.2 — Verify the import graph
- Only two external consumers:
  - `web.py` imports `register_control_api` → unchanged (signature stays)
  - `test_control_api.py` imports `map_axis` → unchanged (pure utility, moves to `control_utils.py`)
- No other modules import anything from `control_api`

### 0.3 — Worktree isolation
- Create a dedicated git worktree for this refactor to avoid collision with other active branches
- `git worktree add ../jetsonTracker-controlapi-split main`
- Work there; the main checkout stays clean for other work

---

## Phase 1 — Extract Pure Utilities (lowest risk)

These functions have no I/O, no FastAPI imports, no dependencies on `ControlApiAdapter`. They only move — no logic changes.

### 1.1 — Create `orin/wavecam/wavecam/control_utils.py`
Move these 18 standalone functions (all at the bottom of control_api.py):

| Function | Lines | What it does |
|---|---|---|
| `normalized_text` | 1486-1491 | String sanitizer |
| `normalized_optional_text` | 1493-1496 | Optional string sanitizer |
| `empty_calibration_state` | 1498-1506 | Default calibration dict |
| `copy_optional_dict` | 1508-1510 | None-safe dict copy |
| `preset_store_path` | 1512-1519 | Preset file path |
| `normalized_preset_name` | 1521-1526 | Preset name validator |
| `canonical_preset_values` | 1528-1530 | Preset value normalizer |
| `split_preset_values` | 1532-1541 | Hot/restart key splitter |
| `preset_payload` | 1543-1552 | Preset response builder |
| `nested_current_value` | 1554-1561 | Config key walker |
| `bounded_log_limit` | 1563-1569 | Log limit clamp |
| `normalized_log_level` | 1571-1576 | Log level validator |
| `normalized_log_source` | 1578-1585 | Log source validator |
| `redact_log_message` | 1587-1592 | Sensitive value redactor |
| `normalize_log_line` | 1594-1614 | Log line normalizer |
| `log_timestamp_ms` | 1616-1631 | Timestamp extractor |
| `make_request_id` | 1633-1636 | UUID generator |
| `nested_set` helpers (`set_float`, `set_int`, `set_bool`) | 1946-1989 | Typed config setters |

- Update `control_api.py` imports: `from .control_utils import ...`
- Run `pytest orin/wavecam/tests/ -v` → must match baseline count
- Commit: `refactor: extract pure utility functions to control_utils.py`

### 1.2 — Create `orin/wavecam/wavecam/control_snapshots.py`
Move the snapshot builders and GPS/media/normalize helpers (pure data assembly, no I/O beyond reading pipeline attributes):

| Function | Lines |
|---|---|
| `build_config_snapshot` | 1683-1757 |
| `build_status_snapshot` | 1759-1773 |
| `merged_status` | 1775-1779 |
| `build_session` | 1781-1787 |
| `session_mode` | 1789-1794 |
| `build_safety` | 1796-1802 |
| `build_ptz` | 1804-1812 |
| `build_tracking` | 1814-1824 |
| `build_gps` | 1826-1833 |
| `gps_snapshot_source` | 1835-1855 |
| `gps_fix_snapshot` | 1857-1868 |
| `normalize_gps` | 1870-1879 |
| `unknown_gps` | 1881-1890 |
| `unknown_media` | 1892-1901 |
| `normalize_media` | 1903-1907 |
| `build_network` | 1909-1915 |
| `map_axis` | 1917-1935 |
| `scaled_speed` | 1937-1940 |
| `zoom_speed` | 1942-1944 |

- Update `control_api.py` imports, update `test_control_api.py` import (`map_axis` now from `control_snapshots`)
- Run tests → must match baseline
- Commit: `refactor: extract snapshot builders to control_snapshots.py`

### 1.3 — Clean up `build_network` dead field
- Remove `"cloudflare": None` from `build_network()` return dict (dead since iOS PR #9)
- Run tests → unchanged count
- Commit separately: `fix: remove dead cloudflare field from network status`

---

## Phase 2 — Extract Self-Contained Adapter Classes (low risk)

These classes already have their own state and limited coupling to `ControlApiAdapter`. They need only a decoupled interface for the few `ControlApiAdapter` methods they call.

### 2.1 — Extract `MediaAdapter` → `orin/wavecam/wavecam/control_media.py`
- `MediaAdapter` (lines 1372-1470) only depends on `recorder` (passed in `__init__`) — zero ControlApiAdapter coupling
- `MediaUnavailable` and `MediaNotFound` exceptions move with it
- `media_ok()` helper moves with it
- Update `control_api.py` import
- Run tests → must match
- Commit: `refactor: extract MediaAdapter to control_media.py`

### 2.2 — Extract `LogAdapter` → `orin/wavecam/wavecam/control_logs.py`
- `LogAdapter` (lines 1291-1351) calls `api.ok()` and `api.refusal()` — create a thin `LogResponder` protocol with just those two methods
- `ControlApiAdapter` already implements them → pass `self` as the responder
- Move log-related utilities from `control_utils.py` if not already there
- Run tests → must match
- Commit: `refactor: extract LogAdapter to control_logs.py`

### 2.3 — Extract `PresetStore` → `orin/wavecam/wavecam/control_presets.py`
- `PresetStore` (lines 1085-1289) calls `api.current_preset_values()` and `api.ok()` / `api.refusal()`
- Create a `PresetResponder` protocol: `current_preset_values()`, `ok()`, `refusal()`
- ControlApiAdapter implements it
- Move preset-related utilities from `control_utils.py`
- Run tests → must match
- Commit: `refactor: extract PresetStore to control_presets.py`

---

## Phase 3 — Extract Config Management (medium risk)

### 3.1 — Create `orin/wavecam/wavecam/control_config.py`
Extract into a `ConfigManager` class:

| Method | Lines | What it does |
|---|---|---|
| `config_snapshot` | 617-623 | Config snapshot (delegates to `build_config_snapshot`) |
| `current_preset_values` | 636-647 | Current value gathering |
| `stage_restart_config` | 649-651 | Restart config staging |
| `apply_hot_config` | 893-902 | Hot config entry point |
| `validate_hot_config_request` | 904-917 | Input validation |
| `apply_hot_key` | 919-967 | Single key hot-apply dispatch |
| `apply_color_preset` | 968-981 | Color preset application |
| `apply_morph_kernel` | 983-993 | Morph kernel application |
| `restart_pending` | 1043-1045 | Restart flag |
| `restart_requires_confirmation` | 1047-1050 | Confirmation gate |

**Dependencies:** `pipeline`, `color_presets` module. No FastAPI, no auth.
**Interface:** `ConfigManager(pipeline)` — takes pipeline ref, exposes above methods.

- Replace `ControlApiAdapter` methods with delegation to `self._config: ConfigManager`
- Run tests → must match
- Commit: `refactor: extract ConfigManager to control_config.py`

---

## Phase 4 — Extract PTZ / Owner Management

### 4.1 — Create `orin/wavecam/wavecam/control_ptz.py`
Extract into a `PtzDispatcher` class:

| Method | Lines |
|---|---|
| `claim_manual` | 704-716 |
| `release_manual_owner` | 718-730 |
| `start_autonomous` | 732-746 |
| `stop_ptz` | 748-758 |
| `home_ptz` | 760-772 |
| `hold_manual_owner` | 774-783 |
| `send_manual_velocity` | 785-804 |
| `send_manual_zoom_velocity` | 806-811 |
| `send_manual_zoom` | 813-823 |
| `manual_pan_tilt_active` | 825-827 |
| `schedule_manual_deadman` | 829-842 |
| `cancel_manual_deadman` | 844-849 |
| `schedule_zoom_deadman` | 851-864 |
| `cancel_zoom_deadman` | 866-871 |
| `zoom_deadman_expired` | 873-879 |
| `manual_deadman_expired` | 881-891 |

**Dependencies:** `pipeline` (for `.ptz`, `.owner`, `.suppress_cinematic_zoom()`, `.cfg.ptz`), `threading` (for deadman timers).
**Interface:** `PtzDispatcher(pipeline)`.

- Replace `ControlApiAdapter` methods with delegation to `self._ptz: PtzDispatcher`
- Deadman timers use `threading.Timer` — these stay in the dispatcher
- Run tests → must match
- Commit: `refactor: extract PtzDispatcher to control_ptz.py`

---

## Phase 5 — Extract Calibration Management

### 5.1 — Create `orin/wavecam/wavecam/control_calibration.py`
Extract into a `CalibrationManager` class:

| Method | Lines |
|---|---|
| `calibration_ok` | 653-662 |
| `calibration_state` | 664-672 |
| `validate_calibration_capture` | 674-681 |
| `capture_calibration` | 683-690 |

**Dependencies:** `pipeline` (for `.cfg`).
**Interface:** `CalibrationManager(pipeline)`.

- Run tests → must match
- Commit: `refactor: extract CalibrationManager to control_calibration.py`

---

## Phase 6 — Extract System / Agent Management

### 6.1 — Create `orin/wavecam/wavecam/control_system.py`
Extract into a `SystemManager` class:

| Method | Lines |
|---|---|
| `resume_without_autostart` | 692-702 |
| `request_service_restart` | 995-1020 |
| `request_agent_summon` | 1022-1041 |
| `prepare_for_restart` | 1052-1061 |
| `schedule_service_restart` | 1063-1070 |
| `run_service_restart` | 1072-1083 |
| `revision` | 606-608 |
| `bump_revision` | 610-612 |

**Dependencies:** `pipeline`, `subprocess`, `supervisor.restart_systemd_unit`.
**Interface:** `SystemManager(pipeline)`.

- Run tests → must match
- Commit: `refactor: extract SystemManager to control_system.py`

---

## Phase 7 — Slim Down ControlApiAdapter

At this point all business logic is in separate modules. `ControlApiAdapter` becomes a thin coordinator:

```python
class ControlApiAdapter:
    def __init__(self, pipeline, frames):
        self._config = ConfigManager(pipeline)
        self._ptz = PtzDispatcher(pipeline)
        self._calibration = CalibrationManager(pipeline)
        self._system = SystemManager(pipeline)
        self._presets = PresetStore(self)   # still needs ok/refusal
        self._logs = LogAdapter(self)       # still needs ok/refusal
        self._media = MediaAdapter(pipeline.recorder)
        self._frames = frames

    # Thin delegation — each method is 1-2 lines
    def status_snapshot(self) -> dict: ...
    def ok(self) -> JSONResponse: ...
    def refusal(self, code, message, status_code=409) -> JSONResponse: ...
    # ... remaining ~12 methods are all 1-line delegations
```

Expected result: `ControlApiAdapter` drops from ~500 lines to ~80 lines.

### 7.1 — Commit
- Run full test suite one final time
- Commit: `refactor: slim ControlApiAdapter to coordinator (~80 lines)`

---

## Phase 8 — Re-export and Verify

### 8.1 — Keep `register_control_api` as the single entry point
- `web.py` imports `register_control_api` from `control_api` — no change needed
- All route registration functions stay in `control_api.py` (they're already organized by domain)
- Route functions are thin wrappers that call the adapter's delegation methods — they still work

### 8.2 — Final verification
- `pytest orin/wavecam/tests/ -v --tb=short` → compare to baseline
- `python3 -m compileall orin/wavecam/wavecam/` → no import errors
- `python3 ~/.claude/skills/anti-vibe-engineering/scripts/python_quality_scan.py orin/wavecam/wavecam/` → no new findings
- `git diff --stat main...` → verify no unexpected file changes

---

## Safety Rules (applied throughout)

1. **One extraction per commit.** If a commit breaks tests, `git revert` it and re-think.
2. **Never change logic during extraction.** If a method has a bug, note it and fix in a separate commit AFTER the split.
3. **Auth guards stay on the routes, not the handlers.** Route registration functions keep `Depends(require(...))` — the extracted classes have no auth awareness.
4. **No deploy.** This is a pure refactor. All API response shapes, status codes, and behavior are unchanged. Verified by tests.
5. **Worktree, not the main checkout.** Keeps the main checkout free for other work and eliminates collision risk.

---

## Expected Outcome

| File | Before | After |
|---|---|---|
| `control_api.py` | 1989 lines, 42 methods | ~300 lines, route registration + thin adapter |
| `control_utils.py` | — | ~200 lines, pure helpers |
| `control_snapshots.py` | — | ~300 lines, status/config builders |
| `control_media.py` | — | ~120 lines, MediaAdapter |
| `control_logs.py` | — | ~100 lines, LogAdapter |
| `control_presets.py` | — | ~220 lines, PresetStore |
| `control_config.py` | — | ~250 lines, ConfigManager |
| `control_ptz.py` | — | ~200 lines, PtzDispatcher |
| `control_calibration.py` | — | ~80 lines, CalibrationManager |
| `control_system.py` | — | ~120 lines, SystemManager |

Each new module is under 300 lines. Each has a single responsibility. Test suite passes unchanged.

---

## Time Estimate

| Phase | Steps | Risk | Est. Time |
|---|---|---|---|
| 0 — Safety net | 0.1–0.3 | None | 10 min |
| 1 — Pure utilities | 1.1–1.3 | Very low | 20 min |
| 2 — Adapter classes | 2.1–2.3 | Low | 25 min |
| 3 — Config manager | 3.1 | Medium | 20 min |
| 4 — PTZ dispatcher | 4.1 | Medium | 25 min |
| 5 — Calibration | 5.1 | Low | 15 min |
| 6 — System/agent | 6.1 | Low | 15 min |
| 7 — Slim adapter | 7.1 | Medium | 15 min |
| 8 — Verify | 8.1–8.2 | None | 15 min |
| **Total** | | | **~2.5 hours** |
