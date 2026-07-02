"""Tests for the depth-fire injection TTL cache.

The periodic depth refresh issues a deterministic query ("session context
<wing>") every fire, and the daemon search behind it costs most of a
second — well over the hook latency budget. Because the query is identical
and its results barely change within a session, repeat fires are served
from a small on-disk TTL cache instead of re-querying the daemon.
"""

import json
import time

from mempalace.auto_query.depth_cache import (
    cache_key,
    load_cached_injection,
    store_injection,
)


def _cache_path(tmp_path):
    return str(tmp_path / "depth_cache.json")


class TestCacheKey:
    def test_key_includes_query_wing_limit(self):
        a = cache_key("session context x", "x", 3)
        assert cache_key("session context x", "x", 3) == a
        assert cache_key("session context y", "x", 3) != a
        assert cache_key("session context x", "y", 3) != a
        assert cache_key("session context x", "x", 5) != a


class TestRoundTrip:
    def test_store_then_load_within_ttl(self, tmp_path):
        p = _cache_path(tmp_path)
        key = cache_key("session context w", "w", 3)
        store_injection(key, "[mempalace:auto-query] cached block", p)
        got = load_cached_injection(key, ttl_seconds=900, cache_path=p)
        assert got == "[mempalace:auto-query] cached block"

    def test_expired_entry_returns_none(self, tmp_path):
        p = _cache_path(tmp_path)
        key = cache_key("q", "w", 3)
        store_injection(key, "old block", p)
        # age the entry past the TTL
        data = json.load(open(p))
        data[key]["ts"] = time.time() - 1000
        json.dump(data, open(p, "w"))
        assert load_cached_injection(key, ttl_seconds=900, cache_path=p) is None

    def test_missing_cache_file_returns_none(self, tmp_path):
        key = cache_key("q", "w", 3)
        assert load_cached_injection(key, ttl_seconds=900, cache_path=_cache_path(tmp_path)) is None

    def test_zero_ttl_disables_reads(self, tmp_path):
        p = _cache_path(tmp_path)
        key = cache_key("q", "w", 3)
        store_injection(key, "block", p)
        assert load_cached_injection(key, ttl_seconds=0, cache_path=p) is None

    def test_corrupt_cache_file_returns_none(self, tmp_path):
        p = _cache_path(tmp_path)
        with open(p, "w") as f:
            f.write("{not json")
        key = cache_key("q", "w", 3)
        assert load_cached_injection(key, ttl_seconds=900, cache_path=p) is None
        # and storing over a corrupt file recovers it
        store_injection(key, "fresh", p)
        assert load_cached_injection(key, ttl_seconds=900, cache_path=p) == "fresh"


class TestRunnerIntegration:
    def _run(self, monkeypatch, tmp_path, turn, calls):
        from mempalace.auto_query import runner as runner_mod

        def fake_call_mcp(tool_call, config):
            calls.append(tool_call)
            return {"results": [{"drawer_id": "d1", "text": "hello", "wing": "w", "room": "r"}]}

        monkeypatch.setattr(runner_mod, "_call_mcp", fake_call_mcp)
        monkeypatch.setenv("AUTO_QUERY_ENABLED", "true")
        monkeypatch.setenv("AUTO_QUERY_MODE", "balanced")
        monkeypatch.setenv("AUTO_QUERY_DEPTH_CACHE_TTL", "900")
        monkeypatch.setenv("AUTO_QUERY_DEPTH_CACHE_PATH", str(tmp_path / "depth_cache.json"))
        return runner_mod.run_auto_query(
            prompt="ok",
            session_id="s",
            turn=turn,
            project_wing="wingx",
            known_wings={"wingx"},
            log_dir=str(tmp_path),
        )

    def test_second_depth_fire_serves_from_cache(self, monkeypatch, tmp_path):
        calls = []
        r1 = self._run(monkeypatch, tmp_path, turn=10, calls=calls)
        assert r1.injection
        assert len(calls) == 1
        r2 = self._run(monkeypatch, tmp_path, turn=20, calls=calls)
        assert r2.injection == r1.injection
        assert len(calls) == 1  # served from cache — no second MCP call
        assert "cache" in r2.decision.reason

    def test_non_depth_fire_not_cached(self, monkeypatch, tmp_path):
        calls = []
        from mempalace.auto_query import runner as runner_mod

        def fake_call_mcp(tool_call, config):
            calls.append(tool_call)
            return {"results": [{"drawer_id": "d1", "text": "hello", "wing": "w", "room": "r"}]}

        monkeypatch.setattr(runner_mod, "_call_mcp", fake_call_mcp)
        monkeypatch.setenv("AUTO_QUERY_ENABLED", "true")
        monkeypatch.setenv("AUTO_QUERY_MODE", "balanced")
        monkeypatch.setenv("AUTO_QUERY_DEPTH_CACHE_TTL", "900")
        monkeypatch.setenv("AUTO_QUERY_DEPTH_CACHE_PATH", str(tmp_path / "depth_cache.json"))
        for _ in range(2):
            runner_mod.run_auto_query(
                prompt="remind me what we decided about the schema?",
                session_id="s",
                turn=5,
                project_wing="wingx",
                known_wings={"wingx"},
                log_dir=str(tmp_path),
            )
        assert len(calls) == 2  # explicit-recall queries always hit the daemon
