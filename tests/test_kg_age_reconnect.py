"""#405: the cached AGE connection must survive one bad query and one dead socket.

Live measurement 2026-09-03: a single slow walk (statement_timeout →
QueryCanceled) or a host suspend/resume left ``KnowledgeGraphAGE._conn``
poisoned for every later caller until the daemon was restarted — three times in
one day. These tests drive the class with a scripted fake psycopg2 module.
"""

from unittest.mock import patch

import pytest

from mempalace import knowledge_graph_age as kga


class _FakePsycopg2Module:
    class Error(Exception):
        pass

    class OperationalError(Error):
        pass

    class InterfaceError(Error):
        pass

    class QueryCanceled(Error):
        pass

    def __init__(self):
        self.connections = []

    def connect(self, dsn):
        conn = _FakeConn(self)
        self.connections.append(conn)
        return conn


class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, *a):
        if self.conn.closed:
            raise self.conn.mod.InterfaceError("connection already closed")
        if self.conn.fail_next:
            exc = self.conn.fail_next.pop(0)
            if isinstance(exc, self.conn.mod.OperationalError):
                self.conn.closed = 1
            raise exc
        self.conn.executed.append(sql)
        self.last_sql = str(sql)

    def fetchall(self):
        return [("row",)]

    def fetchone(self):
        # Bootstrap SQL (graph exists? index counts?) expects ints; Cypher
        # scalars come back agtype-quoted.
        if "cypher(" in getattr(self, "last_sql", ""):
            return ('"v"',)
        return (0,)


class _FakeConn:
    def __init__(self, mod):
        self.mod = mod
        self.closed = 0
        self.autocommit = None
        self.executed = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_next = []  # exceptions to raise on upcoming execute() calls

    def cursor(self):
        if self.closed:
            raise self.mod.InterfaceError("the connection is closed")
        return _FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = 1


@pytest.fixture
def kg():
    mod = _FakePsycopg2Module()
    with patch.object(kga, "_load_psycopg2", return_value=mod):
        graph = kga.KnowledgeGraphAGE("postgresql://fake/db")
        yield graph, mod


def test_bootstrap_uses_one_connection(kg):
    graph, mod = kg
    assert len(mod.connections) == 1
    assert graph._conn is mod.connections[0]


def test_statement_error_rolls_back_and_keeps_connection(kg):
    graph, mod = kg
    conn = graph._conn
    conn.fail_next = [mod.QueryCanceled("canceling statement due to statement timeout")]
    with pytest.raises(mod.QueryCanceled):
        graph._run_cypher("MATCH (n) RETURN n", {}, fetch=True)
    assert conn.rollbacks == 1, "aborted transaction must be rolled back"
    assert graph._conn is conn and not conn.closed, "connection stays cached"
    # the very next query works without any reconnect
    rows = graph._run_cypher("MATCH (n) RETURN n", {}, fetch=True)
    assert rows == [("row",)]
    assert len(mod.connections) == 1


def test_dead_connection_reconnects_once_and_retries(kg):
    graph, mod = kg
    first = graph._conn
    first.fail_next = [mod.OperationalError("server closed the connection unexpectedly")]
    rows = graph._run_cypher("MATCH (n) RETURN n", {}, fetch=True)
    assert rows == [("row",)]
    assert len(mod.connections) == 2, "exactly one reconnect"
    assert graph._conn is mod.connections[1]
    assert first.closed


def test_externally_closed_socket_is_replaced_before_use(kg):
    graph, mod = kg
    graph._conn.closed = 1  # e.g. host suspend/resume killed the TCP session
    val = graph._cypher_scalar("MATCH (n) RETURN count(n)", {})
    assert val == "v"
    assert len(mod.connections) == 2


def test_persistent_connection_failure_raises_after_one_retry(kg):
    graph, mod = kg
    graph._conn.fail_next = [mod.OperationalError("down")]

    real_connect = mod.connect

    def flaky_connect(dsn):
        conn = real_connect(dsn)
        conn.fail_next = [mod.OperationalError("still down")]
        return conn

    mod.connect = flaky_connect
    with pytest.raises(mod.OperationalError):
        graph._run_cypher("MATCH (n) RETURN n", {}, fetch=True)
    assert len(mod.connections) == 2, "retried exactly once"
