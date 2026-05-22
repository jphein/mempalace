"""Tests for the no-palace error mapping (power-resilience design 2026-05-21).

``_no_palace()`` should distinguish three cases:
  1. Backend connection refused (OperationalError) →
     ``palace.backend_unreachable`` with a "start the container" hint.
  2. Any other backend exception →
     ``palace.backend_error`` with the type+message.
  3. Genuinely empty palace (no recent error) →
     legacy ``"No palace found"`` with the init hint.

The misleading-init-hint bug was the actual diagnostic blocker during
JP's 2026-05-17 power outage: the CLI told him to ``mempalace init``
when the real cause was a stopped ``mempalace-db`` container.
"""

from __future__ import annotations

import time

from mempalace import mcp_server


def _reset_error_state():
    mcp_server._last_backend_error = None


def test_no_palace_default_when_no_recent_error():
    _reset_error_state()
    resp = mcp_server._no_palace()
    assert resp["error"] == "No palace found"
    assert "mempalace init" in resp["hint"]


def test_no_palace_maps_operational_error_to_backend_unreachable():
    mcp_server._last_backend_error = {
        "type": "OperationalError",
        "message": 'connection to server at "10.0.6.120", port 5433 failed: Connection refused',
        "ts": time.time(),
    }
    try:
        resp = mcp_server._no_palace()
        assert resp["error"] == "palace.backend_unreachable"
        assert "postgres backend is unreachable" in resp["message"]
        assert "Connection refused" in resp["message"]
        assert "docker ps mempalace-db" in resp["hint"]
        assert "mempalace init" not in resp.get("hint", "")
    finally:
        _reset_error_state()


def test_no_palace_maps_other_backend_errors():
    mcp_server._last_backend_error = {
        "type": "DataError",
        "message": "schema mismatch",
        "ts": time.time(),
    }
    try:
        resp = mcp_server._no_palace()
        assert resp["error"] == "palace.backend_error"
        assert "DataError" in resp["message"]
        assert "schema mismatch" in resp["message"]
        assert "journalctl" in resp["hint"]
    finally:
        _reset_error_state()


def test_get_collection_postgres_records_operational_error(monkeypatch):
    """When PostgresBackend raises OperationalError, the error is captured."""
    _reset_error_state()
    mcp_server._postgres_backend_cache = None
    mcp_server._collection_cache = None

    class _FakeOperationalError(Exception):
        pass

    _FakeOperationalError.__name__ = "OperationalError"

    class _FakeBackend:
        def __init__(self, *a, **kw):
            pass

        def get_collection(self, **kw):
            raise _FakeOperationalError(
                'connection to server at "10.0.6.120", port 5433 failed: Connection refused'
            )

    import mempalace.backends.postgres as pg_mod

    monkeypatch.setattr(pg_mod, "PostgresBackend", _FakeBackend)

    result = mcp_server._get_collection_postgres()
    assert result is None
    assert mcp_server._last_backend_error is not None
    assert mcp_server._last_backend_error["type"] == "OperationalError"
    assert "Connection refused" in mcp_server._last_backend_error["message"]
    # Backend cache should be cleared so reconnect on recovery picks fresh DSN.
    assert mcp_server._postgres_backend_cache is None
    _reset_error_state()


def test_get_collection_postgres_clears_error_on_success(monkeypatch):
    """A successful open clears any previous error so _no_palace stops lying."""
    mcp_server._last_backend_error = {
        "type": "OperationalError",
        "message": "stale",
        "ts": time.time(),
    }
    mcp_server._postgres_backend_cache = None
    mcp_server._collection_cache = None

    class _FakeBackend:
        def __init__(self, *a, **kw):
            pass

        def get_collection(self, **kw):
            return object()  # any truthy "collection"

    import mempalace.backends.postgres as pg_mod

    monkeypatch.setattr(pg_mod, "PostgresBackend", _FakeBackend)

    result = mcp_server._get_collection_postgres()
    try:
        assert result is not None
        assert mcp_server._last_backend_error is None
    finally:
        _reset_error_state()
        mcp_server._postgres_backend_cache = None
        mcp_server._collection_cache = None
