"""Append-only JSONL writer for per-session estimator shadow records.

One file per session: session_<session_id>.jsonl under log_dir.
Writes are line-buffered so a crash does not lose data.
"""
from __future__ import annotations

import glob
import json
import os

# /data/shadow holds one append-only session_<id>.jsonl per pipeline session and was otherwise
# never pruned (161 files / 433 M on the rig, 2026-06-23). Retain this many most-recent session
# logs; older ones are removed when a new session starts. ~30 keeps recent estimator traces
# without unbounded growth (each session is a few MB at most).
SHADOW_KEEP = 30


def prune_shadow_logs(log_dir: str, keep: int = SHADOW_KEEP) -> int:
    """Keep only the ``keep`` most-recent ``session_*.jsonl`` files in ``log_dir`` (names are
    timestamped, so lexicographic order is chronological). Best-effort — a failed unlink is
    logged, not raised, so log retention never aborts a shadow session. Returns the count removed.
    """
    try:
        files = sorted(glob.glob(os.path.join(log_dir, "session_*.jsonl")))
    except OSError as e:
        print(f"[shadow_writer] prune list failed (non-fatal): {e}")
        return 0
    stale = files if keep <= 0 else files[:-keep]
    removed = 0
    for path in stale:
        try:
            os.remove(path)
            removed += 1
        except OSError as e:
            print(f"[shadow_writer] prune unlink failed for {path} (non-fatal): {e}")
    return removed


class ShadowWriter:
    def __init__(self, log_dir: str, session_id: str, keep: int = SHADOW_KEEP):
        os.makedirs(log_dir, exist_ok=True)
        # Bound the directory before adding this session's file — pruning first guarantees the
        # active file (created below) is never itself a deletion candidate.
        prune_shadow_logs(log_dir, keep)
        path = os.path.join(log_dir, f"session_{session_id}.jsonl")
        self._f = open(path, "a", encoding="utf-8", buffering=1)   # line-buffered

    def write(self, record: dict) -> None:
        self._f.write(json.dumps(record, separators=(",", ":")) + "\n")

    def close(self) -> None:
        self._f.close()
