"""Log reading facade for the WaveCam control API.

Moved from control_api.py.  LogAdapter calls api.ok() and api.refusal()
through the object it receives in __init__ — no other ControlApiAdapter
coupling beyond those two methods.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any

from fastapi.responses import JSONResponse

from .control_utils import (
    LOG_UNITS,
    bounded_log_limit,
    make_request_id,
    normalize_log_line,
    normalized_log_level,
)


class LogAdapter:
    """Read-scoped log facade; never exposes arbitrary journald output."""

    def __init__(self, api) -> None:
        self.api = api

    def response(
        self,
        level: str | None = None,
        limit: int = 200,
        since: int | None = None,
    ) -> JSONResponse:
        normalized_level = normalized_log_level(level)
        if level is not None and normalized_level is None:
            return self.api.refusal(
                "invalid_request",
                "level must be one of debug, info, warning, error.",
                422,
            )
        limit = bounded_log_limit(limit)
        lines = self.lines(level=normalized_level, limit=limit, since=since)
        return JSONResponse(
            {
                "ok": True,
                "request_id": make_request_id(),
                "lines": lines,
            }
        )

    def lines(self, level: str | None, limit: int, since: int | None) -> list[dict]:
        normalized: list[dict] = []
        for raw in self.raw_lines(limit):
            line = normalize_log_line(raw)
            if line is None:
                continue
            if since is not None and line["ts_unix_ms"] < since:
                continue
            if level is not None and line["level"] != level:
                continue
            normalized.append(line)
        normalized.sort(key=lambda item: (item["ts_unix_ms"], item["source"], item["message"]))
        return normalized[-limit:]

    def raw_lines(self, limit: int) -> list[Any]:
        reader = getattr(self.api.pipeline, "read_logs", None)
        if callable(reader):
            try:
                return list(reader(limit=limit))
            except TypeError:
                try:
                    return list(reader())
                except Exception:
                    return []
            except Exception:
                return []
        log_lines = getattr(self.api.pipeline, "log_lines", None)
        if log_lines is not None:
            return list(log_lines)
        return self.journal_lines(limit)

    def journal_lines(self, limit: int) -> list[dict]:
        cmd = ["journalctl", "--no-pager", "--output=json", "-n", str(limit)]
        for unit in LOG_UNITS:
            cmd.extend(["--unit", unit])
        try:
            proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=1.5)
        except (OSError, subprocess.TimeoutExpired):
            return []
        if proc.returncode != 0:
            return []
        lines: list[dict] = []
        for raw_line in proc.stdout.splitlines():
            try:
                parsed = json.loads(raw_line)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                lines.append(parsed)
        return lines
