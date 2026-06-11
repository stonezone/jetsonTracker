"""Preset store for the WaveCam control API.

Moved from control_api.py.  PresetStore calls api.current_preset_values(),
api.apply_hot_config(), api.stage_restart_config(), api.ok(),
api.refusal(), api.bump_revision(), and api.status_snapshot() — these are
the only ControlApiAdapter touch-points.
"""
from __future__ import annotations

import json
import time
from typing import Any

from fastapi.responses import JSONResponse

from .control_utils import (
    BUILTIN_PRESET_VALUES,
    HOT_CONFIG_KEYS,
    RESTART_REQUIRED_KEYS,
    canonical_preset_values,
    make_request_id,
    normalized_preset_name,
    preset_payload,
    preset_store_path,
    split_preset_values,
)


class PresetStore:
    """Backend-stored Tune presets, with read-only built-ins and JSON custom presets."""

    def __init__(self, api) -> None:
        self.api = api
        self.path = preset_store_path(api.pipeline)
        self._builtins = {
            "Default": api.current_preset_values(),
            **BUILTIN_PRESET_VALUES,
        }

    def list_response(self) -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "request_id": make_request_id(),
                "presets": self.list_presets(),
            }
        )

    def save_response(self, req) -> JSONResponse:
        name = normalized_preset_name(req.name)
        if name is None:
            return self.api.refusal(
                "invalid_request",
                "Preset name must start with a letter or number and contain only letters, numbers, spaces, dots, underscores, or dashes.",
                422,
            )
        if name in self._builtins:
            return self.api.refusal(
                "builtin_preset",
                "Built-in presets are read-only.",
            )
        if req.capture_current and req.values is not None:
            return self.api.refusal(
                "invalid_request",
                "Use either values or capture_current=true, not both.",
                422,
            )
        if not req.capture_current and req.values is None:
            return self.api.refusal(
                "invalid_request",
                "Preset save requires values or capture_current=true.",
                422,
            )

        values = self.api.current_preset_values() if req.capture_current else dict(req.values or {})
        if not values:
            return self.api.refusal("invalid_request", "Preset values may not be empty.", 422)
        refusal = self.validate_values(values)
        if refusal is not None:
            return refusal

        preset = {
            "name": name,
            "values": canonical_preset_values(values),
            "updated_at_unix_ms": int(time.time() * 1000),
        }
        custom = [item for item in self.read_custom() if item["name"] != name]
        custom.append(preset)
        try:
            self.write_custom(custom)
        except OSError as exc:
            return self.api.refusal(
                "preset_store_unavailable",
                f"Preset store is not writable: {exc}",
                503,
            )
        return JSONResponse(
            {
                "ok": True,
                "request_id": make_request_id(),
                "preset": preset_payload(name, preset["values"], builtin=False),
            }
        )

    def apply_response(self, name: str) -> JSONResponse:
        preset = self.find_preset(name)
        if preset is None:
            return self.api.refusal("preset_not_found", "Preset was not found.", 404)
        values = dict(preset["values"])
        refusal = self.validate_values(values)
        if refusal is not None:
            return refusal

        hot_patch, restart_patch = split_preset_values(values)
        if hot_patch:
            refusal = self.api.apply_hot_config(hot_patch)
            if refusal is not None:
                return refusal
        if restart_patch:
            self.api.stage_restart_config(restart_patch)
        if hot_patch or restart_patch:
            self.api.bump_revision()
        return JSONResponse(
            {
                "ok": True,
                "request_id": make_request_id(),
                "name": preset["name"],
                "applied": hot_patch,
                "restart_required": bool(restart_patch),
                "restart_keys": list(restart_patch),
                "status": self.api.status_snapshot(),
            }
        )

    def delete_response(self, name: str) -> JSONResponse:
        clean_name = normalized_preset_name(name)
        if clean_name is None:
            return self.api.refusal("preset_not_found", "Preset was not found.", 404)
        if clean_name in self._builtins:
            return self.api.refusal(
                "builtin_preset",
                "Built-in presets are read-only.",
            )
        custom = self.read_custom()
        kept = [item for item in custom if item["name"] != clean_name]
        if len(kept) == len(custom):
            return self.api.refusal("preset_not_found", "Preset was not found.", 404)
        try:
            self.write_custom(kept)
        except OSError as exc:
            return self.api.refusal(
                "preset_store_unavailable",
                f"Preset store is not writable: {exc}",
                503,
            )
        return JSONResponse(
            {
                "ok": True,
                "request_id": make_request_id(),
                "deleted": clean_name,
                "presets": self.list_presets(custom=kept),
            }
        )

    def list_presets(self, custom: list[dict] | None = None) -> list[dict]:
        presets = [
            preset_payload(name, values, builtin=True)
            for name, values in self._builtins.items()
        ]
        custom_presets = custom if custom is not None else self.read_custom()
        presets.extend(
            preset_payload(item["name"], item["values"], builtin=False)
            for item in sorted(custom_presets, key=lambda entry: entry["name"])
        )
        return presets

    def find_preset(self, name: str) -> dict | None:
        clean_name = normalized_preset_name(name)
        if clean_name is None:
            return None
        if clean_name in self._builtins:
            return {"name": clean_name, "values": self._builtins[clean_name], "builtin": True}
        for item in self.read_custom():
            if item["name"] == clean_name:
                return {"name": clean_name, "values": item["values"], "builtin": False}
        return None

    def validate_values(self, values: dict[str, Any]) -> JSONResponse | None:
        for key, value in values.items():
            if key in HOT_CONFIG_KEYS:
                refusal = self.api.apply_hot_key(key, value, dry_run=True)
                if refusal is not None:
                    return refusal
            elif key not in RESTART_REQUIRED_KEYS:
                return self.api.refusal(
                    "invalid_request",
                    f"{key} is not a supported preset key.",
                    422,
                )
        return None

    def read_custom(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        items = data.get("presets", [])
        if not isinstance(items, list):
            return []
        custom: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = normalized_preset_name(item.get("name"))
            values = item.get("values")
            if name is None or name in self._builtins or not isinstance(values, dict):
                continue
            custom.append(
                {
                    "name": name,
                    "values": canonical_preset_values(values),
                    "updated_at_unix_ms": int(item.get("updated_at_unix_ms") or 0),
                }
            )
        return custom

    def write_custom(self, presets: list[dict]) -> None:
        payload = {"presets": sorted(presets, key=lambda entry: entry["name"])}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f"{self.path.name}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)
