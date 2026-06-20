# Acting Agent — Phase 1a Implementation Plan (interactive chat + arm/KILL safety bridge)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the WaveCam agent an interactive, multi-turn conversation grounded in live status, with the armed-session safety bridge (arm toggle default-OFF + TTL, KILL supreme) built and tested — so acting tiers (Phase 1b+) plug in safely later.

**Architecture:** A new backend `agent_session` module owns (a) an `ArmState` machine and (b) an `AgentSession` that shells to `claude -p --resume <sid>` (prompt via stdin) injecting a live status snapshot, returning reply + session_id. New operator endpoints `/agent/chat` and `/agent/arm`; KILL clears arm. iOS Agent tab becomes a chat (message list + input + arm toggle + ever-present KILL). No acting tools yet — disarmed/armed only changes a flag the future tool tiers will read.

**Tech Stack:** Python 3 / FastAPI / pytest + mypy (backend); Swift / SwiftUI (iOS). `claude -p` (Claude Code 2.1.183) on the rig.

## Global Constraints

- Backend is **Codex's lane** — claim the agent-collab bus for `orin/wavecam/` before editing; deploy only via `orin/wavecam/deploy.sh`; commit to branch `claude/ios-agent-oauth-knf7ui` (NOT `main`).
- **Supervise-only + KILL-reachable invariants always hold.** KILL is `SAFETY`-role, never an agent capability.
- `claude` CLI: absolute path `/home/zack/.local/bin/claude`; token `claude_code_oauth_token` from `/data/projects/gimbal/agent_keys.json` injected as `CLAUDE_CODE_OAUTH_TOKEN` in the child env — **never on the command line, never logged**.
- `--allowedTools`/`--disallowedTools` are variadic → pass the operator prompt via **stdin**, not as a trailing positional.
- New config section must be added to BOTH the `Config` dataclass AND `_KNOWN_SECTIONS` (silent-vanish gotcha).
- iOS decoders must NOT declare snake_case `CodingKeys` (global `.convertFromSnakeCase` null-out gotcha); tolerant decode via `decodeIfPresent ?? default` in an extension.
- All tests offline (inject the subprocess runner); mypy gate stays green.

---

### Task 1: `AgentCfg` config section

**Files:**
- Modify: `orin/wavecam/wavecam/config.py` (add dataclass + register in `Config` + `_KNOWN_SECTIONS`)
- Test: `orin/wavecam/tests/test_config.py`

**Interfaces:**
- Produces: `AgentCfg(enabled: bool=False, model: str="", arm_ttl_sec: float=600.0, mcp_config_path: str="")`; `Config.agent: AgentCfg`.

- [ ] **Step 1: Write the failing test**

```python
def test_agent_cfg_defaults_and_known_section():
    from wavecam.config import Config, AgentCfg
    cfg = Config()
    assert isinstance(cfg.agent, AgentCfg)
    assert cfg.agent.enabled is False
    assert cfg.agent.arm_ttl_sec == 600.0
    # round-trips through the loader without vanishing
    assert "agent" in Config._KNOWN_SECTIONS  # type: ignore[attr-defined]
```

- [ ] **Step 2: Run test to verify it fails** — `cd orin/wavecam && pytest tests/test_config.py::test_agent_cfg_defaults_and_known_section -v` → FAIL (`AgentCfg` undefined).

- [ ] **Step 3: Implement** — add near the other section dataclasses (mirror `EstimatorCfg`/`SensorsCfg`):

```python
@dataclass
class AgentCfg:
    enabled: bool = False
    model: str = ""
    arm_ttl_sec: float = 600.0
    mcp_config_path: str = ""
```

Add `agent: AgentCfg = field(default_factory=AgentCfg)` to the `Config` dataclass, and `"agent"` to the `_KNOWN_SECTIONS` collection (match its existing form — list/set/tuple). If the loader maps sections explicitly, add the `agent` parse line mirroring `sensors`/`estimator`.

- [ ] **Step 4: Run test to verify it passes** — same command → PASS. Then `pytest tests/test_config.py -q` (no regressions) + `mypy wavecam/config.py`.

- [ ] **Step 5: Commit** — `git add wavecam/config.py tests/test_config.py && git commit -m "feat(agent): AgentCfg config section (off by default)"`

---

### Task 2: `ArmState` machine

**Files:**
- Create: `orin/wavecam/wavecam/agent_session.py`
- Test: `orin/wavecam/tests/test_agent_session.py`

**Interfaces:**
- Produces: `ArmState(ttl_sec: float, now: Callable[[], float])` with `arm()`, `disarm()`, `kill()`, `clear_kill()`, and properties/methods `armed -> bool` (False once TTL elapsed), `killed -> bool`, `can_act() -> bool` (= `armed and not killed`), `snapshot() -> dict`. `now` is injected (no `time.time()` in logic) so tests are deterministic.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from wavecam.agent_session import ArmState

def test_default_disarmed():
    s = ArmState(ttl_sec=600.0, now=lambda: 0.0)
    assert s.armed is False and s.killed is False and s.can_act() is False

def test_arm_then_ttl_expiry():
    t = {"v": 0.0}
    s = ArmState(ttl_sec=600.0, now=lambda: t["v"])
    s.arm()
    assert s.can_act() is True
    t["v"] = 599.0
    assert s.armed is True
    t["v"] = 601.0
    assert s.armed is False and s.can_act() is False  # auto-expired

def test_kill_disarms_and_blocks_rearm():
    s = ArmState(ttl_sec=600.0, now=lambda: 0.0)
    s.arm(); s.kill()
    assert s.killed is True and s.can_act() is False
    s.arm()                      # re-arm attempt while killed
    assert s.can_act() is False  # refused until clear_kill()
    s.clear_kill(); s.arm()
    assert s.can_act() is True
```

- [ ] **Step 2: Run to verify fail** — `pytest tests/test_agent_session.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement**

```python
"""Interactive agent session: arm-state safety machine + claude -p driver."""
from __future__ import annotations
import json
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Optional

CLAUDE_CLI_PATH = "/home/zack/.local/bin/claude"
REQUEST_TIMEOUT_SEC = 90.0


class ArmState:
    """Operator arm gate. DISARMED by default; ARMED auto-expires after ttl;
    KILL is supreme — disarms and forbids re-arm until clear_kill()."""

    def __init__(self, ttl_sec: float, now: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_sec
        self._now = now
        self._armed_at: Optional[float] = None
        self._killed = False

    def arm(self) -> None:
        if self._killed:
            return
        self._armed_at = self._now()

    def disarm(self) -> None:
        self._armed_at = None

    def kill(self) -> None:
        self._killed = True
        self._armed_at = None

    def clear_kill(self) -> None:
        self._killed = False

    @property
    def killed(self) -> bool:
        return self._killed

    @property
    def armed(self) -> bool:
        if self._killed or self._armed_at is None:
            return False
        return (self._now() - self._armed_at) < self._ttl

    def can_act(self) -> bool:
        return self.armed and not self._killed

    def snapshot(self) -> dict:
        return {"armed": self.armed, "killed": self._killed, "ttl_sec": self._ttl}
```

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_agent_session.py -v` → PASS. `mypy wavecam/agent_session.py`.

- [ ] **Step 5: Commit** — `git add wavecam/agent_session.py tests/test_agent_session.py && git commit -m "feat(agent): ArmState safety machine (default-off, TTL, KILL-supreme)"`

---

### Task 3: `AgentSession.chat()` — claude -p resume driver

**Files:**
- Modify: `orin/wavecam/wavecam/agent_session.py`
- Test: `orin/wavecam/tests/test_agent_session.py`

**Interfaces:**
- Consumes: `ArmState`.
- Produces: module-level `_run_claude_cli(argv, env, stdin_text, timeout) -> str` (monkeypatchable; returns stdout). `AgentSession(keys_path, cli_path=CLAUDE_CLI_PATH, run=_run_claude_cli)` with `chat(message: str, status_text: str) -> dict` returning `{"reply": str, "session_id": str}`. Holds the last `session_id` and passes `--resume` on subsequent turns. Prompt (status snapshot + message) is sent via **stdin**; uses `--output-format json` to read back `session_id`.

- [ ] **Step 1: Write the failing test** (inject the runner; assert resume threading + stdin prompt)

```python
def test_chat_threads_session_id_and_uses_stdin(tmp_path):
    import json as _j
    from wavecam.agent_session import AgentSession
    keys = tmp_path / "k.json"; keys.write_text(_j.dumps({"claude_code_oauth_token": "x"}))
    calls = []
    def fake_run(argv, env, stdin_text, timeout):
        calls.append({"argv": argv, "stdin": stdin_text, "env_token": env.get("CLAUDE_CODE_OAUTH_TOKEN")})
        return _j.dumps({"result": "hi there", "session_id": "SID-1"})
    sess = AgentSession(keys_path=str(keys), run=fake_run)
    r1 = sess.chat("hello", status_text="FPS=27")
    assert r1 == {"reply": "hi there", "session_id": "SID-1"}
    assert "--resume" not in calls[0]["argv"]              # first turn: no resume
    assert calls[0]["env_token"] == "x"                    # token injected via env
    assert "FPS=27" in calls[0]["stdin"] and "hello" in calls[0]["stdin"]  # prompt via stdin
    r2 = sess.chat("again", status_text="FPS=30")
    assert ["--resume", "SID-1"] == calls[1]["argv"][calls[1]["argv"].index("--resume"):calls[1]["argv"].index("--resume")+2]
```

- [ ] **Step 2: Run to verify fail** — `pytest tests/test_agent_session.py::test_chat_threads_session_id_and_uses_stdin -v` → FAIL.

- [ ] **Step 3: Implement** — append to `agent_session.py`:

```python
def _run_claude_cli(argv: list[str], env: dict, stdin_text: str, timeout: float) -> str:
    proc = subprocess.run(argv, env=env, input=stdin_text,
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: "
                           f"{(proc.stderr or proc.stdout or '').strip()[:200]}")
    return proc.stdout


def _load_token(keys_path: str) -> str:
    with open(keys_path) as fh:
        token = json.load(fh).get("claude_code_oauth_token")
    if not token:
        raise RuntimeError("claude_code_oauth_token missing from agent_keys.json")
    return str(token)


@dataclass
class AgentSession:
    keys_path: str
    cli_path: str = CLAUDE_CLI_PATH
    run: Callable[..., str] = _run_claude_cli
    session_id: Optional[str] = None

    def chat(self, message: str, status_text: str) -> dict:
        token = _load_token(self.keys_path)
        env = {**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": token}
        argv = [self.cli_path, "--output-format", "json"]
        if self.session_id:
            argv += ["--resume", self.session_id]
        argv += ["-p"]   # -p terminates any variadic flag; prompt arrives on stdin
        prompt = (f"You are the WaveCam onboard assistant. Live system status:\n"
                  f"{status_text}\n\nOperator: {message}")
        out = self.run(argv, env, prompt, REQUEST_TIMEOUT_SEC)
        data = json.loads(out)
        self.session_id = data.get("session_id") or self.session_id
        return {"reply": data.get("result", ""), "session_id": self.session_id or ""}
```

> Note: when no positional prompt is given, `-p` reads the prompt from stdin (verified behavior). Keep `--output-format json` to recover `session_id`.

- [ ] **Step 4: Run to verify pass** — the named test → PASS; `pytest tests/test_agent_session.py -q`; `mypy wavecam/agent_session.py`.

- [ ] **Step 5: Commit** — `git add wavecam/agent_session.py tests/test_agent_session.py && git commit -m "feat(agent): AgentSession claude -p resume driver (stdin prompt, json session_id)"`

---

### Task 4: Endpoints `/agent/chat` + `/agent/arm` + KILL integration

**Files:**
- Modify: `orin/wavecam/wavecam/control_system.py` (own an `AgentSession` + `ArmState`; build only when `agent.enabled`)
- Modify: `orin/wavecam/wavecam/control_api.py` (routes + request models; KILL handler calls `arm.kill()`)
- Test: `orin/wavecam/tests/test_control_api.py`

**Interfaces:**
- Consumes: `AgentSession`, `ArmState`, the existing status-snapshot builder used by summon.
- Produces: `POST /api/v1/agent/chat` (role `SERVICE`, body `{"message": str}`) → `{"ok": true, "reply": str, "armed": bool}`; `POST /api/v1/agent/arm` (role `CONFIG`, body `{"armed": bool}`) → `{"ok": true, "armed": bool}`; both → `{"ok": false, "code": "agent_disabled"}` when `agent.enabled` is false. `/config` `supported.agent` reflects enabled. Existing KILL route additionally calls `self._agent_arm.kill()` when the agent exists.

- [ ] **Step 1: Write the failing tests** (use the existing test client + a stubbed `AgentSession`)

```python
def test_agent_chat_disabled_returns_code(client_agent_disabled):
    r = client_agent_disabled.post("/api/v1/agent/chat", json={"message": "hi"},
                                   headers=SERVICE_HEADERS)
    assert r.status_code == 200 and r.json()["ok"] is False
    assert r.json()["code"] == "agent_disabled"

def test_agent_arm_then_kill_clears(client_agent_enabled):
    a = client_agent_enabled.post("/api/v1/agent/arm", json={"armed": True}, headers=CONFIG_HEADERS)
    assert a.json() == {"ok": True, "armed": True}
    client_agent_enabled.post("/api/v1/safety/kill", headers=SAFETY_HEADERS)
    s = client_agent_enabled.get("/api/v1/status", headers=READ_HEADERS).json()
    assert s["agent"]["armed"] is False  # KILL cleared arm
```

(Add fixtures `client_agent_enabled`/`client_agent_disabled` mirroring existing client fixtures, with `agent.enabled` toggled and `AgentSession.chat` monkeypatched to a stub returning a fixed reply.)

- [ ] **Step 2: Run to verify fail** — `pytest tests/test_control_api.py -k agent -v` → FAIL.

- [ ] **Step 3: Implement**

In `control_system.py`, build (guarded) in `__init__`:
```python
self._agent_arm = None
self._agent_session = None
if cfg.agent.enabled:
    self._agent_arm = ArmState(ttl_sec=cfg.agent.arm_ttl_sec)
    self._agent_session = AgentSession(keys_path=AGENT_KEYS_PATH,
                                       cli_path=(cfg.agent.model_cli_path or CLAUDE_CLI_PATH))
```
Add methods `request_agent_chat(message)` (returns `{"ok": False, "code": "agent_disabled"}` if disabled; else builds the status snapshot via the same helper summon uses, calls `self._agent_session.chat(message, status_text)`, returns `{"ok": True, "reply": ..., "armed": self._agent_arm.can_act()}`) and `request_agent_arm(armed)`. In the existing KILL path add: `if self._agent_arm: self._agent_arm.kill()`. Expose `self._agent_arm.snapshot()` under status key `"agent"`.

In `control_api.py` add (near the existing agent routes ~705) tolerant request models and:
```python
@app.post("/api/v1/agent/chat", dependencies=[Depends(require(SERVICE))])
def agent_chat(req: AgentChatRequest):
    return api.request_agent_chat(req.message)

@app.post("/api/v1/agent/arm", dependencies=[Depends(require(CONFIG))])
def agent_arm(req: AgentArmRequest):
    return api.request_agent_arm(req.armed)
```
Set `supported["agent"] = cfg.agent.enabled` in the `/config` builder.

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_control_api.py -k agent -v` → PASS; full `pytest -q`; `mypy wavecam`.

- [ ] **Step 5: Commit** — `git add wavecam/control_system.py wavecam/control_api.py tests/test_control_api.py && git commit -m "feat(agent): /agent/chat + /agent/arm endpoints, KILL clears arm, optionality"`

---

### Task 5: iOS — Agent tab becomes a chat with arm toggle

**Files:**
- Modify: `ios/WaveCam/Sources/WaveCamClient.swift` (add `sendAgentChat`, `setAgentArm`; models)
- Modify: `ios/WaveCam/Sources/AgentView.swift` (chat list + input + arm toggle; KILL stays)
- Verify on device: `ios/WaveCam/build-device.sh`

**Interfaces:**
- Consumes: `/agent/chat`, `/agent/arm`, `supported.agent`.
- Produces: `func sendAgentChat(_ message: String) async -> WCAgentChat?` (POST, operator auth, via `getWithFallback`-style POST); `func setAgentArm(_ armed: Bool) async -> Bool`. `struct WCAgentChat: Decodable { var reply: String; var armed: Bool }` with tolerant `init(from:)` in an **extension** and **no** snake_case `CodingKeys`.

- [ ] **Step 1: Add client methods + model** — in `WaveCamClient.swift`, mirror `summonAgent`'s POST plumbing:

```swift
struct WCAgentChat: Decodable { var reply: String = ""; var armed: Bool = false }
extension WCAgentChat {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        reply = try c.decodeIfPresent(String.self, forKey: .reply) ?? ""
        armed = try c.decodeIfPresent(Bool.self, forKey: .armed) ?? false
    }
}
func sendAgentChat(_ message: String) async -> WCAgentChat? { /* POST /agent/chat {"message":message}; decode WCAgentChat */ }
func setAgentArm(_ armed: Bool) async -> Bool { /* POST /agent/arm {"armed":armed}; return ok */ }
```

- [ ] **Step 2: Build the chat UI** — in `AgentView.swift`, behind `supported.agent == true || mode == .mock`: a `ScrollViewReader` message list (`[(role, text)]` in `@State`), a `TextField` + send button calling `sendAgentChat` and appending the reply, and an **arm toggle** (`@State armed`, default false) calling `setAgentArm` with a confirmation; force the toggle visibly OFF when status shows killed. Keep the existing deterministic report + KILL button reachable. Provider picker keeps `claudeCode` default. Cancel any poll `Task` in `onDisappear`.

- [ ] **Step 3: Build** — `cd ios/WaveCam && ./build-device.sh build` → expect `** BUILD SUCCEEDED **` (ignore SourceKit cross-file noise).

- [ ] **Step 4: Verify on device** — install, Agent tab: send a message → reply renders; toggle arm on/off; KILL reachable; check portrait + landscape.

- [ ] **Step 5: Commit** — `git add ios/WaveCam/Sources/WaveCamClient.swift ios/WaveCam/Sources/AgentView.swift && git commit -m "feat(ios): Agent tab chat + arm toggle over /agent/chat"`

---

### Task 6: Deploy + live verify (gated)

- [ ] **Step 1:** Full backend suite — `cd orin/wavecam && pytest -q && mypy wavecam` → all green.
- [ ] **Step 2:** Enable `agent` in `config.local.yaml` on the rig (or via hot path) with `enabled: true`, `arm_ttl_sec: 600`.
- [ ] **Step 3:** Deploy via `orin/wavecam/deploy.sh` (tests-gate + version stamp). **Requires explicit operator "go" — live shared rig.**
- [ ] **Step 4:** Verify: rig `/config` shows `supported.agent=true`; `/agent/chat` round-trips a multi-turn exchange (second message remembers the first); `/agent/arm` toggles; KILL clears arm; **vision still `fps>0` while LOCKED** (no zombie).
- [ ] **Step 5:** Update memory `agent-claude-code-harness` with the chat+arm bridge; do not call it "live" until Step 4 passes.

---

## Self-Review

**Spec coverage:** §2 safety floors → ArmState (1,2,5-as-future) + KILL integration (Task 4) + audit deferred to acting-tools phase (no mutating tools yet, so nothing to audit beyond chat). §3 engine → Task 3 (resume/stdin/json). §4 components → AgentCfg (T1), agent_session (T2-3), endpoints (T4), iOS (T5). Optionality (§4) → T4 disabled-path test. Testing (§7) → arm matrix (T2), resume continuity (T3), optionality + KILL (T4), iOS decode/KILL/orientation (T5), live fps>0 (T6). **Deferred to Phase 1b (explicitly out of scope here):** MCP tool server, `--allowedTools` wiring, camera/deploy tiers, arbiter ownership for agent moves, web terminal — they require the acting tools that this phase deliberately omits.

**Placeholder scan:** Task 5 Steps 1-2 describe the SwiftUI body in prose with the critical signatures/gotchas rather than full view code — acceptable because the UI is exploratory and device-verified, but the data-layer (model + decoder + method signatures) is given concretely. All backend steps carry complete code.

**Type consistency:** `ArmState` API (`arm/disarm/kill/clear_kill/armed/killed/can_act/snapshot`) consistent across T2/T4. `AgentSession.chat(message, status_text) -> {reply, session_id}` consistent T3/T4. `WCAgentChat{reply,armed}` consistent T5.
