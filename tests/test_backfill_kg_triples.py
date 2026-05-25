"""Unit tests for scripts/backfill_kg_triples.py.

The backfill driver is a thin wrapper around the worker CLI — these
tests pin the load-bearing contract:

1. Progress log format is stable (operators grep against it).
2. SIGTERM handler fires the release-in-flight SQL with the expected
   shape (rows the worker had claimed get re-queued).
3. The progress query batch is the four counters callers expect.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from unittest.mock import MagicMock, patch


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DRIVER_PATH = REPO_ROOT / "scripts" / "backfill_kg_triples.py"


def _load_driver():
    """Load the script as a module without requiring it on sys.path."""
    spec = importlib.util.spec_from_file_location(
        "backfill_kg_triples", DRIVER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["backfill_kg_triples"] = module
    spec.loader.exec_module(module)
    return module


# ──────────────────────────────────────────────────────────────────────
# Test doubles — psycopg2 connection/cursor shaped like the real one
# ──────────────────────────────────────────────────────────────────────


class _FakeCursor:
    def __init__(self, fetch_values=None):
        self.executes: list[tuple[str, tuple | None]] = []
        self._fetch_values = list(fetch_values or [])
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.executes.append((sql, params))
        # Simulate UPDATE rowcount when the SQL is the release statement.
        if "UPDATE mempalace_kg_extraction_queue" in sql and "started_at = NULL" in sql:
            self.rowcount = 3

    def fetchone(self):
        if self._fetch_values:
            return (self._fetch_values.pop(0),)
        return (0,)


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.autocommit = False
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def close(self):
        pass


# ──────────────────────────────────────────────────────────────────────
# Test 1 — progress log format is stable
# ──────────────────────────────────────────────────────────────────────


def test_progress_logging_format():
    """Operators grep against this format. Pin it explicitly."""
    driver = _load_driver()

    line = driver._format_progress(
        completed=12345,
        pending=350000,
        in_flight=7,
        errors=12,
        rate_per_min=24.6,
        elapsed_sec=1800.0,
    )

    # All seven fields appear, in order, with the documented separators.
    assert "drawers_completed=12345" in line
    assert "in_flight=7" in line
    assert "pending=350000" in line
    assert "rate=24.6/min" in line
    assert "errors=12" in line
    assert "eta=" in line
    assert "elapsed=1800s" in line

    # Field order matters — docs/kg-extraction.md shows this exact shape.
    assert line.index("drawers_completed") < line.index("in_flight")
    assert line.index("in_flight") < line.index("pending")
    assert line.index("pending") < line.index("rate")
    assert line.index("rate") < line.index("errors")
    assert line.index("errors") < line.index("eta")


def test_progress_logging_eta_unknown_when_zero_rate():
    """Zero throughput should not divide-by-zero."""
    driver = _load_driver()

    line = driver._format_progress(
        completed=0,
        pending=100,
        in_flight=0,
        errors=0,
        rate_per_min=0.0,
        elapsed_sec=10.0,
    )
    assert "eta=unknown" in line


def test_progress_logging_eta_scales_to_hours_and_days():
    driver = _load_driver()

    # 100 pending @ 1/min = 100m = 1.7h
    line = driver._format_progress(
        completed=0, pending=100, in_flight=0, errors=0,
        rate_per_min=1.0, elapsed_sec=0.0,
    )
    assert "eta=1.7h" in line

    # 100000 pending @ 1/min = ~69d
    line = driver._format_progress(
        completed=0, pending=100000, in_flight=0, errors=0,
        rate_per_min=1.0, elapsed_sec=0.0,
    )
    assert "eta=69.4d" in line


# ──────────────────────────────────────────────────────────────────────
# Test 2 — SIGTERM handler releases in-flight rows
# ──────────────────────────────────────────────────────────────────────


def test_release_in_flight_runs_expected_sql():
    """SIGTERM hook must reset started_at on rows the worker had claimed
    so a restart re-queues them. The released rowcount must come back to
    the caller (used in the WARNING log)."""
    driver = _load_driver()

    cursor = _FakeCursor()
    conn = _FakeConn(cursor)

    fake_psycopg2 = MagicMock()
    fake_psycopg2.connect.return_value = conn

    with patch.object(driver, "_release_in_flight", wraps=driver._release_in_flight):
        with patch.dict(
            sys.modules,
            {"mempalace.backends.postgres": MagicMock(
                _load_psycopg2=lambda: (fake_psycopg2, None)
            )},
        ):
            released = driver._release_in_flight("postgresql://test")

    # The release SQL ran with the documented shape.
    assert len(cursor.executes) == 1
    sql, _params = cursor.executes[0]
    assert "UPDATE mempalace_kg_extraction_queue" in sql
    assert "started_at = NULL" in sql
    assert "started_at IS NOT NULL" in sql
    assert "completed_at IS NULL" in sql
    # Race guard — only release rows older than 1 second.
    assert "INTERVAL '1 second'" in sql

    # And the rowcount comes back so the warning log can include it.
    assert released == 3


def test_release_in_flight_swallows_db_errors():
    """SIGTERM cleanup must never crash the shutdown path."""
    driver = _load_driver()

    fake_psycopg2 = MagicMock()
    fake_psycopg2.connect.side_effect = RuntimeError("db down")

    with patch.dict(
        sys.modules,
        {"mempalace.backends.postgres": MagicMock(
            _load_psycopg2=lambda: (fake_psycopg2, None)
        )},
    ):
        # No exception escapes, returns 0.
        assert driver._release_in_flight("postgresql://test") == 0


# ──────────────────────────────────────────────────────────────────────
# Test 3 — progress counters query the four expected queue states
# ──────────────────────────────────────────────────────────────────────


def test_read_counters_queries_all_four_states():
    """The progress logger needs pending + in_flight + completed +
    errors counts in a single round-trip per tick."""
    driver = _load_driver()

    # Each fetchone() returns the next value in this list.
    cursor = _FakeCursor(fetch_values=[10, 5, 100, 2])
    conn = _FakeConn(cursor)

    fake_psycopg2 = MagicMock()
    fake_psycopg2.connect.return_value = conn

    with patch.dict(
        sys.modules,
        {"mempalace.backends.postgres": MagicMock(
            _load_psycopg2=lambda: (fake_psycopg2, None)
        )},
    ):
        counters = driver._read_counters("postgresql://test")

    assert counters == {
        "pending": 10,
        "in_flight": 5,
        "completed": 100,
        "errors": 2,
    }
    # Four SELECTs ran, one per counter.
    assert len(cursor.executes) == 4
    seen_sql = " ".join(sql for sql, _ in cursor.executes)
    assert "completed_at IS NULL AND error IS NULL" in seen_sql  # pending shape
    assert "started_at IS NOT NULL" in seen_sql  # in-flight shape
    assert "completed_at IS NOT NULL" in seen_sql
    assert "error IS NOT NULL" in seen_sql


def test_read_counters_returns_none_on_db_failure():
    """Transient DB failures should be soft — the logger keeps trying
    on the next tick."""
    driver = _load_driver()

    fake_psycopg2 = MagicMock()
    fake_psycopg2.connect.side_effect = RuntimeError("db blip")

    with patch.dict(
        sys.modules,
        {"mempalace.backends.postgres": MagicMock(
            _load_psycopg2=lambda: (fake_psycopg2, None)
        )},
    ):
        assert driver._read_counters("postgresql://test") is None


# ──────────────────────────────────────────────────────────────────────
# Test 4 — CLI arg parsing
# ──────────────────────────────────────────────────────────────────────


def test_cli_defaults_match_documented_values():
    """docs/kg-extraction.md says: --workers 24, --batch-size 100,
    --poll-interval 30, --progress-interval 60. Pin these so the docs
    aren't lying."""
    driver = _load_driver()

    args, extras = driver._parse_args(["--dsn", "postgresql://x"])
    assert args.workers == 24
    assert args.batch_size == 100
    assert args.poll_interval == 30
    assert args.progress_interval == 60.0
    assert extras == []


def test_cli_forwards_unknown_flags_to_worker():
    """Any flag not recognized by the driver should be forwarded to the
    underlying worker — operator escape hatch."""
    driver = _load_driver()

    args, extras = driver._parse_args(
        ["--workers", "8", "--model", "phi-4-mini", "--temperature", "0.1"]
    )
    assert args.workers == 8
    assert extras == ["--model", "phi-4-mini", "--temperature", "0.1"]


def test_cli_requires_dsn():
    """No DSN, no env — exit 2."""
    driver = _load_driver()

    with patch.dict("os.environ", {}, clear=True):
        rc = driver.main([])
    assert rc == 2
