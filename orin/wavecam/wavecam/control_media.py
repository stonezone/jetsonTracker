"""Media recording facade for the WaveCam control API.

Moved from control_api.py.  MediaAdapter only depends on the recorder
object passed in __init__ — zero ControlApiAdapter coupling.  The
exceptions and media_ok helper move with it.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.responses import JSONResponse

from .control_snapshots import normalize_media, unknown_media
from .control_utils import make_request_id


class MediaUnavailable(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class MediaNotFound(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class MediaAdapter:
    """Small recorder facade used by /api/v1 status and media routes."""

    def __init__(self, recorder) -> None:
        self.recorder = recorder

    def status(self) -> dict:
        if self.recorder is None:
            return unknown_media()
        try:
            return normalize_media(self.recorder.status())
        except Exception as exc:
            media = unknown_media()
            media["error"] = str(exc)
            return media

    def start(self, segment_seconds: int | None) -> dict:
        if self.recorder is None:
            raise MediaUnavailable("Recorder is not configured.")
        return self.recorder.start(segment_seconds=segment_seconds)

    def stop(self) -> dict:
        if self.recorder is None:
            raise MediaUnavailable("Recorder is not configured.")
        return self.recorder.stop()

    def list_files(self) -> list[dict]:
        rec_dir = self.rec_dir()
        if not rec_dir.exists():
            return []
        files: list[dict] = []
        for path in rec_dir.iterdir():
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            files.append(
                {
                    "name": path.name,
                    "size_bytes": stat.st_size,
                    "ctime_unix_ms": int(stat.st_ctime * 1000),
                }
            )
        return sorted(files, key=lambda item: (item["ctime_unix_ms"], item["name"]), reverse=True)

    def download_path(self, name: str) -> Path:
        rec_dir = self.rec_dir().resolve()
        if (
            not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or Path(name).name != name
        ):
            raise MediaNotFound("Media file was not found.")
        path = (rec_dir / name).resolve()
        try:
            path.relative_to(rec_dir)
        except ValueError as exc:
            raise MediaNotFound("Media file was not found.") from exc
        if not path.is_file():
            raise MediaNotFound("Media file was not found.")
        return path

    def delete_file(self, name: str) -> dict:
        path = self.download_path(name)
        try:
            freed_bytes = path.stat().st_size
            path.unlink()
        except FileNotFoundError as exc:
            raise MediaNotFound("Media file was not found.") from exc
        except OSError as exc:
            raise MediaUnavailable(f"Media file could not be deleted: {exc}") from exc
        return {"ok": True, "name": path.name, "freed_bytes": freed_bytes}

    def rec_dir(self) -> Path:
        if self.recorder is None:
            raise MediaUnavailable("Recorder is not configured.")
        config = getattr(self.recorder, "config", None)
        rec_dir = getattr(config, "rec_dir", None)
        if rec_dir is None:
            raise MediaUnavailable("Recorder directory is not configured.")
        return Path(rec_dir)

    def stop_for_safety(self) -> None:
        if self.recorder is not None:
            self.recorder.stop()


def media_ok(api, result: dict) -> JSONResponse:
    return JSONResponse(
        {
            "ok": bool(result.get("ok", True)),
            "request_id": make_request_id(),
            "media": result,
            "status": api.status_snapshot(),
        }
    )
