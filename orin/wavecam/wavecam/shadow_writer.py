"""Append-only JSONL writer for per-session estimator shadow records.

One file per session: session_<session_id>.jsonl under log_dir.
Writes are line-buffered so a crash does not lose data.
"""
from __future__ import annotations

import json
import os


class ShadowWriter:
    def __init__(self, log_dir: str, session_id: str):
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, f"session_{session_id}.jsonl")
        self._f = open(path, "a", encoding="utf-8", buffering=1)   # line-buffered

    def write(self, record: dict) -> None:
        self._f.write(json.dumps(record, separators=(",", ":")) + "\n")

    def close(self) -> None:
        self._f.close()
