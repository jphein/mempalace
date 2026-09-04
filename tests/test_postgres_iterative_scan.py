"""pgvector filtered-kNN under-return: the backend must enable hnsw.iterative_scan.

Measured 2026-09-03 on the production palace (pgvector 0.8.2, 757K drawers): a
wing-scoped prose query returned 0 rows because the HNSW scan hands back its
global nearest ~ef_search rows and the wing filter discards them all
(EXPLAIN: ``Rows Removed by Filter: 47``). The same query unscoped returned 20;
an exact scan returned 5; ``SET hnsw.iterative_scan = relaxed_order`` made the
index scan return 5. The GUC is session-scoped, so it must be issued on every
new connection.
"""

import logging
from unittest.mock import patch

import pytest

from mempalace.backends import postgres as pg


class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, *a):
        self.conn.executed.append(str(sql))
        if self.conn.fail_on_set and "hnsw.iterative_scan" in str(sql):
            raise RuntimeError('unrecognized configuration parameter "hnsw.iterative_scan"')

    def fetchall(self):
        return []


class _FakeConn:
    def __init__(self, fail_on_set=False):
        self.executed = []
        self.autocommit = None
        self.closed = 0
        self.fail_on_set = fail_on_set

    def cursor(self):
        return _FakeCursor(self)


class _FakeMod:
    def __init__(self, conn):
        self._conn = conn

    def connect(self, dsn):
        return self._conn


def _owner():
    return next(
        c
        for c in vars(pg).values()
        if isinstance(c, type) and hasattr(c, "_apply_session_settings")
    )


def _backend(conn):
    b = _owner().__new__(_owner())
    b.dsn = "postgresql://fake/db"
    b._conn = None
    return b


def test_new_connection_enables_iterative_scan(monkeypatch):
    monkeypatch.delenv("MEMPALACE_PG_HNSW_ITERATIVE_SCAN", raising=False)
    conn = _FakeConn()
    with patch.object(pg, "_load_psycopg2", return_value=(_FakeMod(conn), None)):
        b = _backend(conn)
        assert b._get_conn() is conn
    sets = [s for s in conn.executed if "hnsw.iterative_scan" in s]
    assert sets == ["SET hnsw.iterative_scan = relaxed_order"]
    assert conn.autocommit is True


def test_reused_connection_does_not_reissue(monkeypatch):
    monkeypatch.delenv("MEMPALACE_PG_HNSW_ITERATIVE_SCAN", raising=False)
    conn = _FakeConn()
    with patch.object(pg, "_load_psycopg2", return_value=(_FakeMod(conn), None)):
        b = _backend(conn)
        b._get_conn()
        b._get_conn()
    assert sum("hnsw.iterative_scan" in s for s in conn.executed) == 1


def test_env_off_disables(monkeypatch):
    monkeypatch.setenv("MEMPALACE_PG_HNSW_ITERATIVE_SCAN", "off")
    conn = _FakeConn()
    with patch.object(pg, "_load_psycopg2", return_value=(_FakeMod(conn), None)):
        _backend(conn)._get_conn()
    assert not any("hnsw.iterative_scan" in s for s in conn.executed)


def test_strict_order_passthrough_and_bad_value_falls_back(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setenv("MEMPALACE_PG_HNSW_ITERATIVE_SCAN", "strict_order")
    with patch.object(pg, "_load_psycopg2", return_value=(_FakeMod(conn), None)):
        _backend(conn)._get_conn()
    assert "SET hnsw.iterative_scan = strict_order" in conn.executed
    conn2 = _FakeConn()
    monkeypatch.setenv("MEMPALACE_PG_HNSW_ITERATIVE_SCAN", "bogus; DROP TABLE x")
    with patch.object(pg, "_load_psycopg2", return_value=(_FakeMod(conn2), None)):
        _backend(conn2)._get_conn()
    assert "SET hnsw.iterative_scan = relaxed_order" in conn2.executed, "never pass through junk"


def test_old_pgvector_without_guc_is_tolerated(monkeypatch, caplog):
    monkeypatch.delenv("MEMPALACE_PG_HNSW_ITERATIVE_SCAN", raising=False)
    conn = _FakeConn(fail_on_set=True)
    with patch.object(pg, "_load_psycopg2", return_value=(_FakeMod(conn), None)):
        b = _backend(conn)
        with caplog.at_level(logging.INFO, logger="mempalace.postgres"):
            assert b._get_conn() is conn  # no raise
            b._conn = None  # force a second connect → warn only once
            b._get_conn()
    assert sum("iterative_scan unavailable" in r.message for r in caplog.records) == 1
