"""System-level management for the WaveCam control API.

Moved from control_api.py.  SystemManager owns the service-restart state
machine (pending flag, timer, unit name) and the request_service_restart /
request_agent_summon responses.

It calls api.refusal(), api.ok(), api.status_snapshot(), api.bump_revision(),
and api._ptz (via api.prepare_for_restart delegation) — the adapter re-exports
prepare_for_restart as a convenience so callers don't reach into _ptz directly.
"""
from __future__ import annotations

import json
import threading

from fastapi.responses import JSONResponse

from .control_utils import make_request_id, normalized_text
from .ptz_owner import IDLE
from .advisor import AdvisorService, KEYS_PATH
from .agent_session import AgentSession, ArmState
from .supervisor import restart_systemd_unit


class SystemManager:
    """Owns service-restart scheduling and agent-summon responses."""

    _restart_unit = "wavecam.service"

    def __init__(self, pipeline, lock: threading.RLock, api) -> None:
        self.pipeline = pipeline
        self._lock = lock
        self._api = api
        self._restart_timer: threading.Timer | None = None
        self._restart_pending = False
        self.advisor = AdvisorService(self._advisor_context)
        # Interactive acting-agent (Phase 1a): the conversation + arm/KILL bridge.
        # Built only when enabled, so a disabled agent costs nothing and core boots
        # unaffected. The arm gate is the supervise-only floor acting tiers read.
        acfg = getattr(getattr(pipeline, "cfg", None), "agent", None)
        self._agent_enabled = bool(getattr(acfg, "enabled", False))
        self._agent_arm: ArmState | None = (
            ArmState(ttl_sec=float(getattr(acfg, "arm_ttl_sec", 600.0)))
            if self._agent_enabled else None
        )
        self._agent_session: AgentSession | None = (
            AgentSession(keys_path=KEYS_PATH) if self._agent_enabled else None
        )

    def _advisor_context(self) -> dict:
        """Read-only snapshot for the advisor: status + recent events.
        Built on the advisor's worker thread — uses only lock-guarded reads."""
        ctx: dict = {"status": self._api.status_snapshot()}
        ring = getattr(self.pipeline, "events", None)
        if ring is not None:
            try:
                ctx["events"] = ring.since(0)
            except Exception:
                pass
        return ctx

    # ------------------------------------------------------------------
    # Public request handlers
    # ------------------------------------------------------------------

    def request_service_restart(self, req) -> JSONResponse:
        if self.restart_pending:
            return self._api.refusal(
                "restart_pending",
                "A WaveCam restart request is already pending.",
            )
        if self.restart_requires_confirmation() and not req.confirm_moving:
            return self._api.refusal(
                "restart_confirmation_required",
                "Camera control is active; retry with confirm_moving=true to stop PTZ and restart.",
            )
        self._api.prepare_for_restart()
        self.schedule_service_restart(req.delay_seconds)
        self._api.bump_revision()
        return JSONResponse(
            {
                "ok": True,
                "request_id": make_request_id(),
                "action": "restart",
                "unit": self._restart_unit,
                "scheduled": True,
                "delay_seconds": req.delay_seconds,
                "status": self._api.status_snapshot(),
            },
            status_code=202,
        )

    def request_agent_summon(self, req) -> JSONResponse:
        source = normalized_text(req.source, "unknown", 64)
        reason = normalized_text(req.reason, "operator_diagnostics", 256)
        provider = normalized_text(getattr(req, "provider", None), "claude", 16)
        accepted, message = self.advisor.summon(provider)
        return JSONResponse(
            {
                "ok": accepted,
                "request_id": make_request_id(),
                "action": "agent_summon",
                "accepted": accepted,
                "provider": provider,
                "source": source,
                "reason": reason,
                "message": message + (
                    " (supervise-only: no shell, service, or camera command will run)"
                    if accepted else ""
                ),
                "status": self._api.status_snapshot(),
            },
            status_code=202 if accepted else 409,
        )

    def agent_report(self) -> JSONResponse:
        return JSONResponse(
            {"ok": True, "request_id": make_request_id(),
             "report": self.advisor.report()}
        )

    def request_agent_chat(self, message: str) -> JSONResponse:
        if not self._agent_enabled or self._agent_session is None:
            return JSONResponse(
                {"ok": False, "code": "agent_disabled",
                 "message": "Interactive agent is not enabled on this rig."}
            )
        msg = normalized_text(message, "", 4000)
        status_text = json.dumps(self._api.status_snapshot())
        try:
            result = self._agent_session.chat(msg, status_text)
        except Exception as exc:  # surface as a failed turn; the session is preserved
            return JSONResponse(
                {"ok": False, "code": "agent_error", "message": str(exc)[:200]}
            )
        return JSONResponse(
            {"ok": True, "request_id": make_request_id(),
             "reply": result["reply"],
             "armed": self._agent_arm.can_act() if self._agent_arm else False}
        )

    def request_agent_arm(self, armed: bool) -> JSONResponse:
        if not self._agent_enabled or self._agent_arm is None:
            return JSONResponse({"ok": False, "code": "agent_disabled"})
        if armed:
            self._agent_arm.arm()
        else:
            self._agent_arm.disarm()
        return JSONResponse({"ok": True, "armed": self._agent_arm.can_act()})

    def agent_kill(self) -> None:
        """KILL path hook — supervise-only floor: KILL disarms the agent."""
        if self._agent_arm is not None:
            self._agent_arm.kill()

    def agent_resume(self) -> None:
        """RESUME path hook — clear the agent KILL latch so it can be re-armed.
        Stays disarmed; arming is always an explicit operator action afterward."""
        if self._agent_arm is not None:
            self._agent_arm.clear_kill()

    def agent_arm_snapshot(self) -> dict:
        if self._agent_arm is None:
            return {"armed": False, "killed": False, "enabled": False}
        snap = self._agent_arm.snapshot()
        snap["enabled"] = True
        return snap

    # ------------------------------------------------------------------
    # Restart state
    # ------------------------------------------------------------------

    @property
    def restart_pending(self) -> bool:
        with self._lock:
            return self._restart_pending

    def restart_requires_confirmation(self) -> bool:
        if self.pipeline.owner.killed:
            return False
        return self.pipeline.owner.owner != IDLE

    def prepare_for_restart(self) -> None:
        self._api.cancel_manual_deadman()
        self._api.cancel_zoom_deadman()
        self._api.reset_restore_owner()
        self.pipeline.ptz.stop()
        self.pipeline.ptz.zoom("stop")
        current_owner = self.pipeline.owner.owner
        if current_owner != IDLE:
            self.pipeline.owner.release(current_owner)
        self.pipeline._restarting = True
        self.pipeline.state.set_status(state="RESTARTING", cmd="stop")

    def schedule_service_restart(self, delay_seconds: float) -> None:
        with self._lock:
            self._restart_pending = True
        timer = threading.Timer(delay_seconds, self.run_service_restart)
        timer.daemon = True
        with self._lock:
            self._restart_timer = timer
        timer.start()

    def run_service_restart(self) -> None:
        try:
            restart = getattr(self.pipeline, "restart_service", None)
            if callable(restart):
                restart(self._restart_unit)
            else:
                restart_systemd_unit(self._restart_unit)
        finally:
            with self._lock:
                self._restart_pending = False
                self._restart_timer = None
