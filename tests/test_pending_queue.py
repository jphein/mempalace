"""Tests for the pending mine-request queue (power-resilience design)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mempalace import pending_queue


@pytest.fixture
def queue_dir(tmp_path, monkeypatch):
    """Redirect PENDING_DIR to a temp path so tests don't touch ~/.mempalace."""
    monkeypatch.setattr(pending_queue, "PENDING_DIR", tmp_path / "pending")
    return tmp_path / "pending"


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---- enqueue ----------------------------------------------------------------


def test_enqueue_creates_directory_and_writes_line(queue_dir):
    path = pending_queue.enqueue(
        {"dir": "/tmp/x", "wing": "wing_x", "mode": "convos"},
        now=datetime(2026, 5, 21, tzinfo=timezone.utc),
    )
    assert path == queue_dir / "2026-05-21.jsonl"
    rows = _read_lines(path)
    assert len(rows) == 1
    assert rows[0]["dir"] == "/tmp/x"
    assert rows[0]["wing"] == "wing_x"
    assert rows[0]["mode"] == "convos"
    assert rows[0]["ts"].startswith("2026-05-21")


def test_enqueue_rejects_missing_fields(queue_dir):
    with pytest.raises(ValueError, match="missing field"):
        pending_queue.enqueue({"dir": "/tmp/x", "wing": "wing_x"})


def test_enqueue_appends_multiple_lines(queue_dir):
    base = datetime(2026, 5, 21, 12, tzinfo=timezone.utc)
    for i in range(3):
        pending_queue.enqueue(
            {"dir": f"/tmp/x{i}", "wing": "wing_x", "mode": "convos"},
            now=base,
        )
    path = queue_dir / "2026-05-21.jsonl"
    rows = _read_lines(path)
    assert len(rows) == 3
    assert {r["dir"] for r in rows} == {"/tmp/x0", "/tmp/x1", "/tmp/x2"}


# ---- pending_count ----------------------------------------------------------


def test_pending_count_zero_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(pending_queue, "PENDING_DIR", tmp_path / "nope")
    assert pending_queue.pending_count() == 0


def test_pending_count_sums_across_files(queue_dir):
    base = datetime(2026, 5, 20, tzinfo=timezone.utc)
    pending_queue.enqueue({"dir": "/a", "wing": "w", "mode": "convos"}, now=base)
    pending_queue.enqueue(
        {"dir": "/b", "wing": "w", "mode": "convos"},
        now=base.replace(day=21),
    )
    pending_queue.enqueue(
        {"dir": "/c", "wing": "w", "mode": "convos"},
        now=base.replace(day=21),
    )
    assert pending_queue.pending_count() == 3


# ---- replay -----------------------------------------------------------------


def test_replay_empty_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(pending_queue, "PENDING_DIR", tmp_path / "nope")
    report = pending_queue.replay(lambda r: True)
    assert report.attempted == 0
    assert report.is_empty


def test_replay_drains_file_when_all_succeed(queue_dir):
    base = datetime(2026, 5, 21, tzinfo=timezone.utc)
    pending_queue.enqueue({"dir": "/a", "wing": "w", "mode": "convos"}, now=base)
    pending_queue.enqueue({"dir": "/b", "wing": "w", "mode": "convos"}, now=base)
    calls: list[dict] = []

    def post(req):
        calls.append(req)
        return True

    report = pending_queue.replay(post)
    assert report.attempted == 2
    assert report.succeeded == 2
    assert report.failed == 0
    assert report.files_drained == 1
    assert not (queue_dir / "2026-05-21.jsonl").exists()
    assert {c["dir"] for c in calls} == {"/a", "/b"}


def test_replay_keeps_failing_lines(queue_dir):
    base = datetime(2026, 5, 21, tzinfo=timezone.utc)
    pending_queue.enqueue({"dir": "/a", "wing": "w", "mode": "convos"}, now=base)
    pending_queue.enqueue({"dir": "/b", "wing": "w", "mode": "convos"}, now=base)

    def post(req):
        return req["dir"] == "/a"

    report = pending_queue.replay(post)
    assert report.succeeded == 1
    assert report.failed == 1
    remaining = _read_lines(queue_dir / "2026-05-21.jsonl")
    assert len(remaining) == 1
    assert remaining[0]["dir"] == "/b"


def test_replay_dedupes_same_target(queue_dir):
    base = datetime(2026, 5, 21, 12, tzinfo=timezone.utc)
    # Same (dir, wing, mode) enqueued 5 times (typical outage pile-up).
    for _ in range(5):
        pending_queue.enqueue(
            {"dir": "/same", "wing": "w", "mode": "convos"},
            now=base,
        )
    calls: list[dict] = []

    def post(req):
        calls.append(req)
        return True

    report = pending_queue.replay(post)
    assert len(calls) == 1, "expected dedupe to collapse to one call"
    assert report.attempted == 1
    assert report.succeeded == 1


def test_replay_post_raises_treated_as_failure(queue_dir):
    base = datetime(2026, 5, 21, tzinfo=timezone.utc)
    pending_queue.enqueue({"dir": "/a", "wing": "w", "mode": "convos"}, now=base)

    def post(req):
        raise RuntimeError("daemon explosion")

    report = pending_queue.replay(post)
    assert report.succeeded == 0
    assert report.failed == 1
    # Line should still be there for retry.
    assert _read_lines(queue_dir / "2026-05-21.jsonl")[0]["dir"] == "/a"


def test_replay_skips_malformed_lines(queue_dir):
    queue_dir.mkdir(parents=True, exist_ok=True)
    path = queue_dir / "2026-05-21.jsonl"
    path.write_text(
        '{"dir": "/a", "wing": "w", "mode": "convos", "ts": "2026-05-21T00:00:00+00:00"}\n'
        "this-is-not-json\n"
        '{"dir": "/b", "wing": "w", "mode": "convos", "ts": "2026-05-21T00:00:01+00:00"}\n'
    )
    calls: list[dict] = []

    def post(req):
        calls.append(req)
        return True

    report = pending_queue.replay(post)
    # Two valid lines were drained; malformed one was dropped.
    assert {c["dir"] for c in calls} == {"/a", "/b"}
    assert report.attempted == 2
    assert report.succeeded == 2
    assert not path.exists(), "file should be removed once all valid lines drained"


def test_replay_atomic_rewrite_preserves_remaining(queue_dir):
    """Sanity: after a partial drain, file content is exactly the unconsumed lines."""
    base = datetime(2026, 5, 21, tzinfo=timezone.utc)
    pending_queue.enqueue({"dir": "/a", "wing": "w", "mode": "convos"}, now=base)
    pending_queue.enqueue({"dir": "/b", "wing": "w", "mode": "convos"}, now=base)
    pending_queue.enqueue({"dir": "/c", "wing": "w", "mode": "convos"}, now=base)

    pending_queue.replay(lambda r: r["dir"] in {"/a", "/c"})

    remaining = _read_lines(queue_dir / "2026-05-21.jsonl")
    assert [r["dir"] for r in remaining] == ["/b"]


def test_replay_processes_files_in_date_order(queue_dir):
    pending_queue.enqueue(
        {"dir": "/new", "wing": "w", "mode": "convos"},
        now=datetime(2026, 5, 21, tzinfo=timezone.utc),
    )
    pending_queue.enqueue(
        {"dir": "/old", "wing": "w", "mode": "convos"},
        now=datetime(2026, 5, 18, tzinfo=timezone.utc),
    )
    order: list[str] = []

    def post(req):
        order.append(req["dir"])
        return True

    pending_queue.replay(post)
    assert order == ["/old", "/new"]


# ---- CLI integration --------------------------------------------------------


def test_cmd_replay_no_daemon_short_circuits(monkeypatch, capsys):
    """`mempalace replay` without PALACE_DAEMON_URL is a no-op (no transmit)."""
    from mempalace import cli

    monkeypatch.delenv("PALACE_DAEMON_URL", raising=False)
    monkeypatch.setattr(cli, "_daemon_strict", lambda: False)

    rc = cli.cmd_replay(object())
    assert rc == 0
    captured = capsys.readouterr()
    assert "nothing to do" in captured.err


def test_cmd_replay_drains_queue(monkeypatch, capsys, tmp_path):
    """`mempalace replay` drains the queue when daemon is reachable."""
    from mempalace import cli, pending_queue as pq

    monkeypatch.setattr(pq, "PENDING_DIR", tmp_path / "pending")
    monkeypatch.setattr(cli, "_daemon_strict", lambda: True)
    posted: list[dict] = []

    def fake_post(directory, wing, mode="convos"):
        posted.append({"dir": directory, "wing": wing, "mode": mode})
        return True

    monkeypatch.setattr(cli, "_post_daemon_mine_cli", fake_post)
    pq.enqueue({"dir": "/a", "wing": "wing_a", "mode": "convos"})
    pq.enqueue({"dir": "/b", "wing": "wing_b", "mode": "projects"})

    rc = cli.cmd_replay(object())
    assert rc == 0
    out = capsys.readouterr().out
    assert "attempted=2" in out
    assert "succeeded=2" in out
    assert {p["dir"] for p in posted} == {"/a", "/b"}


def test_cmd_replay_returns_1_on_partial_failure(monkeypatch, capsys, tmp_path):
    """If any request fails to drain, cmd_replay exits 1 so cron can alert."""
    from mempalace import cli, pending_queue as pq

    monkeypatch.setattr(pq, "PENDING_DIR", tmp_path / "pending")
    monkeypatch.setattr(cli, "_daemon_strict", lambda: True)

    def fake_post(directory, wing, mode="convos"):
        return directory == "/ok"

    monkeypatch.setattr(cli, "_post_daemon_mine_cli", fake_post)
    pq.enqueue({"dir": "/ok", "wing": "w", "mode": "convos"})
    pq.enqueue({"dir": "/fail", "wing": "w", "mode": "convos"})

    rc = cli.cmd_replay(object())
    assert rc == 1


# ---- Concurrency fixes (Gemini PR #104 review) ------------------------------


def test_replay_claims_file_so_concurrent_enqueue_survives(queue_dir):
    """Critical race fix: an enqueue racing replay must not be silently lost.

    Gemini's review correctly identified that the prior atomic-rewrite
    pattern would clobber any line appended between replay's read and
    its os.replace. The fix is to claim the file by renaming it; any
    concurrent enqueue then writes to a fresh file with the original
    name.
    """
    base = datetime(2026, 5, 21, tzinfo=timezone.utc)
    pending_queue.enqueue({"dir": "/initial", "wing": "w", "mode": "convos"}, now=base)

    enqueue_during_replay: list[str] = []

    def post(req):
        # Simulate a Stop hook firing mid-drain: append a new line to the
        # original (now-vacated) queue file via the public enqueue API.
        if req["dir"] == "/initial":
            new_path = pending_queue.enqueue(
                {"dir": "/raced", "wing": "w", "mode": "convos"}, now=base
            )
            enqueue_during_replay.append(str(new_path))
        return True

    pending_queue.replay(post)

    # The /initial line was drained successfully. The /raced line should
    # have survived in the live queue file — NOT been silently overwritten.
    live = queue_dir / "2026-05-21.jsonl"
    assert live.exists(), "concurrent enqueue must still be on disk"
    rows = _read_lines(live)
    assert any(r["dir"] == "/raced" for r in rows), f"raced enqueue lost! file contains: {rows}"


def test_replay_failed_lines_appended_back_not_rewritten(queue_dir):
    """When replay fails some lines AND a concurrent enqueue happens,
    both the failed-replay lines AND the new enqueue must survive."""
    base = datetime(2026, 5, 21, tzinfo=timezone.utc)
    pending_queue.enqueue({"dir": "/ok", "wing": "w", "mode": "convos"}, now=base)
    pending_queue.enqueue({"dir": "/fail", "wing": "w", "mode": "convos"}, now=base)

    def post(req):
        if req["dir"] == "/ok":
            # Simulate concurrent enqueue between read and write.
            pending_queue.enqueue({"dir": "/raced", "wing": "w", "mode": "convos"}, now=base)
            return True
        return False  # /fail stays pending

    pending_queue.replay(post)

    live = queue_dir / "2026-05-21.jsonl"
    assert live.exists()
    rows = _read_lines(live)
    dirs = {r["dir"] for r in rows}
    assert "/fail" in dirs, "failed replay line lost"
    assert "/raced" in dirs, "concurrent enqueue lost"
    assert "/ok" not in dirs, "successfully replayed line should be gone"


def test_replay_respects_deadline(queue_dir):
    """``deadline=`` caps total wall time — unfinished lines stay queued."""
    import time as time_mod

    base = datetime(2026, 5, 21, tzinfo=timezone.utc)
    for i in range(20):
        pending_queue.enqueue({"dir": f"/lots{i}", "wing": "w", "mode": "convos"}, now=base)

    call_count = 0

    def slow_post(req):
        nonlocal call_count
        call_count += 1
        time_mod.sleep(0.05)  # 50ms each
        return True

    deadline = time_mod.monotonic() + 0.12  # ~2-3 calls fit
    pending_queue.replay(slow_post, deadline=deadline)

    # Should have called post at most a handful of times — not 20.
    assert call_count <= 5, f"deadline ignored, called {call_count} times"
    # Unfinished entries must still be in the live queue.
    live = queue_dir / "2026-05-21.jsonl"
    remaining = _read_lines(live)
    assert len(remaining) >= 15, f"too many drained ({len(remaining)}/20 left)"
