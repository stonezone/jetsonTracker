"""/data/shadow was unbounded — one session_<id>.jsonl per pipeline session, never pruned
(161 files / 433 M on the rig, 2026-06-23). prune_shadow_logs keeps only the N most-recent;
ShadowWriter prunes on each new session so the dir self-bounds."""
from wavecam.shadow_writer import ShadowWriter, prune_shadow_logs, SHADOW_KEEP


def _make_sessions(d, n, start=0):
    # zero-padded so lexicographic order == chronological, like session_YYYYMMDDTHHMMSS
    for i in range(n):
        (d / f"session_2026{i + start:04d}.jsonl").write_text("{}\n")


def test_prune_keeps_only_most_recent(tmp_path):
    _make_sessions(tmp_path, 50)
    removed = prune_shadow_logs(str(tmp_path), keep=30)
    remaining = sorted(p.name for p in tmp_path.glob("session_*.jsonl"))
    assert removed == 20
    assert len(remaining) == 30
    assert remaining[0] == "session_20260020.jsonl"   # the newest 30 survive
    assert remaining[-1] == "session_20260049.jsonl"


def test_prune_noop_when_under_limit(tmp_path):
    _make_sessions(tmp_path, 5)
    assert prune_shadow_logs(str(tmp_path), keep=30) == 0
    assert len(list(tmp_path.glob("session_*.jsonl"))) == 5


def test_prune_ignores_non_session_files(tmp_path):
    _make_sessions(tmp_path, 40)
    (tmp_path / "notes.txt").write_text("keep me")
    (tmp_path / "README.md").write_text("keep me too")
    prune_shadow_logs(str(tmp_path), keep=10)
    assert (tmp_path / "notes.txt").exists()
    assert (tmp_path / "README.md").exists()
    assert len(list(tmp_path.glob("session_*.jsonl"))) == 10


def test_shadow_writer_prunes_on_start_and_keeps_new(tmp_path):
    _make_sessions(tmp_path, SHADOW_KEEP + 8)  # over the limit
    w = ShadowWriter(str(tmp_path), "29991231T235959", keep=SHADOW_KEEP)  # newest id
    w.write({"ok": 1})
    w.close()
    files = sorted(p.name for p in tmp_path.glob("session_*.jsonl"))
    assert len(files) <= SHADOW_KEEP + 1                    # bounded
    assert "session_29991231T235959.jsonl" in files          # the active session survived
    assert (tmp_path / "session_29991231T235959.jsonl").read_text().strip() == '{"ok":1}'
