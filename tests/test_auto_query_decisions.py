"""Tests for mempalace.auto_query.decisions — JSONL decision logger."""

import json
import os
import stat

import pytest

from mempalace.auto_query import Decision, Signal, SignalSet
from mempalace.auto_query.decisions import (
    _LOG_NAME,
    _serialize_decision,
    append_decision,
    read_decisions,
    rotate_log,
)


def _make_decision(**overrides):
    """Build a Decision with sensible defaults; override any field."""
    defaults = dict(
        ts="2026-05-22T12:00:00Z",
        session_id="sess-001",
        turn=1,
        signals={"entity": [], "temporal": [], "resumption": False, "explicit": False,
                 "total_score": 4, "project_wing": "wing_mempalace", "query_text": ""},
        score=4,
        threshold=4,
        mode="balanced",
        decision="fire",
        reason="entity match",
        tool="mempalace_search",
        args={"query": "mempalace", "limit": 3},
        latency_ms=42,
        result_drawers=3,
        injection_tokens=120,
    )
    defaults.update(overrides)
    return Decision(**defaults)


# ── write and read roundtrip ────────────────────────────────────


class TestAppendAndRead:
    def test_write_then_read(self, tmp_path):
        d = _make_decision()
        append_decision(d, log_dir=str(tmp_path))
        results = read_decisions(last_n=10, log_dir=str(tmp_path))
        assert len(results) == 1
        assert results[0]["session_id"] == "sess-001"
        assert results[0]["tool"] == "mempalace_search"

    def test_multiple_writes_read_last_n(self, tmp_path):
        for i in range(10):
            append_decision(_make_decision(turn=i), log_dir=str(tmp_path))
        results = read_decisions(last_n=3, log_dir=str(tmp_path))
        assert len(results) == 3
        assert [r["turn"] for r in results] == [7, 8, 9]

    def test_read_all_when_fewer_than_n(self, tmp_path):
        append_decision(_make_decision(), log_dir=str(tmp_path))
        results = read_decisions(last_n=100, log_dir=str(tmp_path))
        assert len(results) == 1


# ── JSON roundtrip of all Decision fields ────────────────────────


class TestJsonRoundtrip:
    def test_all_fields_present(self, tmp_path):
        d = _make_decision(
            latency_ms=99,
            result_drawers=5,
            injection_tokens=300,
        )
        append_decision(d, log_dir=str(tmp_path))
        row = read_decisions(log_dir=str(tmp_path))[0]
        for field in ("ts", "session_id", "turn", "signals", "score",
                       "threshold", "mode", "decision", "reason",
                       "tool", "args", "latency_ms", "result_drawers",
                       "injection_tokens"):
            assert field in row, f"missing field: {field}"

    def test_set_in_signals_serializes_to_list(self, tmp_path):
        """SessionState.queried_entities is a set; it must become a list."""
        signals_with_set = {
            "queried_entities": {"alice", "bob"},
            "other": "data",
        }
        d = _make_decision(signals=signals_with_set)
        append_decision(d, log_dir=str(tmp_path))
        row = read_decisions(log_dir=str(tmp_path))[0]
        qe = row["signals"]["queried_entities"]
        assert isinstance(qe, list)
        assert sorted(qe) == ["alice", "bob"]

    def test_default_args_empty_dict(self, tmp_path):
        d = _make_decision(tool="", args={})
        append_decision(d, log_dir=str(tmp_path))
        row = read_decisions(log_dir=str(tmp_path))[0]
        assert row["args"] == {}


# ── empty and missing log ────────────────────────────────────────


class TestEmptyAndMissing:
    def test_read_empty_log(self, tmp_path):
        (tmp_path / _LOG_NAME).touch()
        assert read_decisions(log_dir=str(tmp_path)) == []

    def test_read_nonexistent_log(self, tmp_path):
        assert read_decisions(log_dir=str(tmp_path / "nope")) == []


# ── corrupt / partial lines ─────────────────────────────────────


class TestCorruptTolerance:
    def test_partial_last_line_skipped(self, tmp_path):
        d = _make_decision(turn=1)
        append_decision(d, log_dir=str(tmp_path))
        # Append a partial (non-JSON) line
        log_file = tmp_path / _LOG_NAME
        with open(str(log_file), "a") as fh:
            fh.write('{"ts":"2026-05-22T12:00:01Z","session_id":"s2","tur')

        results = read_decisions(log_dir=str(tmp_path))
        assert len(results) == 1
        assert results[0]["turn"] == 1

    def test_corrupt_middle_line_skipped(self, tmp_path):
        d1 = _make_decision(turn=1)
        d2 = _make_decision(turn=2)
        append_decision(d1, log_dir=str(tmp_path))
        log_file = tmp_path / _LOG_NAME
        with open(str(log_file), "a") as fh:
            fh.write("NOT VALID JSON\n")
        append_decision(d2, log_dir=str(tmp_path))

        results = read_decisions(log_dir=str(tmp_path))
        assert len(results) == 2
        assert results[0]["turn"] == 1
        assert results[1]["turn"] == 2

    def test_blank_lines_skipped(self, tmp_path):
        log_file = tmp_path / _LOG_NAME
        log_file.write_text("\n\n\n")
        assert read_decisions(log_dir=str(tmp_path)) == []


# ── directory creation ───────────────────────────────────────────


class TestDirectoryCreation:
    def test_creates_dir_on_first_write(self, tmp_path):
        nested = str(tmp_path / "a" / "b" / "c")
        append_decision(_make_decision(), log_dir=nested)
        assert os.path.isfile(os.path.join(nested, _LOG_NAME))

    def test_file_permissions(self, tmp_path):
        append_decision(_make_decision(), log_dir=str(tmp_path))
        path = tmp_path / _LOG_NAME
        mode = stat.S_IMODE(os.stat(str(path)).st_mode)
        # Owner read+write only (0o600); no group/other bits.
        assert mode & 0o077 == 0, f"unexpected permissions: {oct(mode)}"


# ── log rotation ─────────────────────────────────────────────────


class TestRotation:
    def _fill_log(self, tmp_path, size_bytes):
        """Write enough data to exceed size_bytes."""
        path = tmp_path / _LOG_NAME
        # Write a line that is roughly 200 bytes, repeat.
        line = json.dumps({"ts": "x", "data": "a" * 150}) + "\n"
        with open(str(path), "w") as fh:
            written = 0
            while written < size_bytes:
                fh.write(line)
                written += len(line)

    def test_no_rotation_under_threshold(self, tmp_path):
        self._fill_log(tmp_path, 500)
        rotate_log(log_dir=str(tmp_path), max_bytes=1000)
        assert (tmp_path / _LOG_NAME).exists()
        assert not (tmp_path / (_LOG_NAME + ".1")).exists()

    def test_rotation_moves_current_to_dot_1(self, tmp_path):
        self._fill_log(tmp_path, 2000)
        rotate_log(log_dir=str(tmp_path), max_bytes=1000)
        assert (tmp_path / (_LOG_NAME + ".1")).exists()
        # Current file is now gone (will be recreated on next write).
        assert not (tmp_path / _LOG_NAME).exists()

    def test_rotation_cascades(self, tmp_path):
        """Simulate 4 rotations; oldest (.3) should be dropped."""
        for _ in range(4):
            self._fill_log(tmp_path, 2000)
            rotate_log(log_dir=str(tmp_path), max_bytes=1000)

        assert (tmp_path / (_LOG_NAME + ".1")).exists()
        assert (tmp_path / (_LOG_NAME + ".2")).exists()
        assert (tmp_path / (_LOG_NAME + ".3")).exists()
        # No .4 — capped at 3 rotations.
        assert not (tmp_path / (_LOG_NAME + ".4")).exists()

    def test_rotation_on_nonexistent_file(self, tmp_path):
        # Should be a no-op, not an error.
        rotate_log(log_dir=str(tmp_path), max_bytes=1000)

    def test_rotation_preserves_rotated_content(self, tmp_path):
        """Content of .1 should match the pre-rotation current file."""
        d = _make_decision(turn=42)
        append_decision(d, log_dir=str(tmp_path))
        # Inflate the file past threshold.
        self._fill_log(tmp_path, 2000)
        rotate_log(log_dir=str(tmp_path), max_bytes=1000)
        # .1 should contain readable JSONL
        with open(str(tmp_path / (_LOG_NAME + ".1"))) as fh:
            lines = fh.readlines()
        assert len(lines) > 0


# ── serialize helper ─────────────────────────────────────────────


class TestSerialize:
    def test_deterministic_set_order(self):
        d = _make_decision(signals={"entities": {"z", "a", "m"}})
        line = _serialize_decision(d)
        parsed = json.loads(line)
        assert parsed["signals"]["entities"] == ["a", "m", "z"]
