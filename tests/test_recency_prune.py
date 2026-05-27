"""Tests for recency weighting + the prune CLI (#158).

Covers four layers:
  * pure age parsing + bounded recency adjustment (mempalace.recency)
  * the searcher integration (recency reorders but never excludes — recall)
  * the `mempalace prune --stale-days` CLI (dry-run default, --confirm gate,
    undated drawers never deleted)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mempalace import recency


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


# ---------------------------------------------------------------------------
# Pure age parsing
# ---------------------------------------------------------------------------


class TestAgeDays:
    def test_none_and_empty(self):
        assert recency.age_days(None) is None
        assert recency.age_days({}) is None
        assert recency.age_days({recency.FILED_AT_KEY: ""}) is None
        assert recency.age_days({recency.FILED_AT_KEY: "not-a-date"}) is None

    def test_parses_naive_isoformat(self):
        meta = {recency.FILED_AT_KEY: _iso(10)}
        age = recency.age_days(meta)
        assert age == pytest.approx(10.0, abs=0.1)

    def test_parses_z_suffix(self):
        # A 'Z' suffix (which 3.9 fromisoformat rejects) must still parse.
        now = datetime.now(timezone.utc).replace(microsecond=0)
        meta = {recency.FILED_AT_KEY: now.isoformat().replace("+00:00", "Z")}
        age = recency.age_days(meta)
        assert age is not None and age >= 0.0

    def test_future_date_clamps_to_zero(self):
        meta = {recency.FILED_AT_KEY: _iso(-5)}  # 5 days in the future
        assert recency.age_days(meta) == 0.0


# ---------------------------------------------------------------------------
# Recency distance adjustment
# ---------------------------------------------------------------------------


class TestRecencyAdjustment:
    def test_undated_is_noop(self):
        assert recency.recency_distance_adjustment({}) == 0.0
        assert recency.recency_distance_adjustment(None) == 0.0

    def test_fresh_drawer_gets_near_max_boost(self):
        meta = {recency.FILED_AT_KEY: _iso(0)}
        adj = recency.recency_distance_adjustment(meta)
        # Negative (pulls distance down); close to the full max for age ~0.
        assert adj == pytest.approx(-recency.RECENCY_DISTANCE_MAX, abs=1e-3)

    def test_one_halflife_is_half_boost(self):
        meta = {recency.FILED_AT_KEY: _iso(recency.RECENCY_HALFLIFE_DAYS)}
        adj = recency.recency_distance_adjustment(meta)
        assert adj == pytest.approx(-recency.RECENCY_DISTANCE_MAX / 2, abs=1e-3)

    def test_two_halflives_is_quarter_boost(self):
        meta = {recency.FILED_AT_KEY: _iso(2 * recency.RECENCY_HALFLIFE_DAYS)}
        adj = recency.recency_distance_adjustment(meta)
        assert adj == pytest.approx(-recency.RECENCY_DISTANCE_MAX / 4, abs=1e-3)

    def test_always_within_bounds(self):
        for d in (0, 1, 30, 365, 10000):
            adj = recency.recency_distance_adjustment({recency.FILED_AT_KEY: _iso(d)})
            assert -recency.RECENCY_DISTANCE_MAX <= adj <= 0.0

    def test_nonpositive_halflife_disables(self):
        meta = {recency.FILED_AT_KEY: _iso(1)}
        assert recency.recency_distance_adjustment(meta, halflife_days=0) == 0.0
        assert recency.recency_distance_adjustment(meta, halflife_days=-5) == 0.0

    def test_nonpositive_max_disables(self):
        meta = {recency.FILED_AT_KEY: _iso(1)}
        assert recency.recency_distance_adjustment(meta, max_shift=0.0) == 0.0


# ---------------------------------------------------------------------------
# Searcher integration
# ---------------------------------------------------------------------------


class TestSearchRecencySignal:
    def _seed(self, palace_path):
        from mempalace.backends import PalaceRef
        from mempalace.backends.chroma import ChromaBackend

        backend = ChromaBackend()
        col = backend.get_collection(
            palace=PalaceRef(id=palace_path, local_path=palace_path),
            collection_name="mempalace_drawers",
            create=True,
        )
        col.upsert(
            ids=["old", "new", "undated"],
            documents=[
                "python asyncio event loop scheduling",
                "python asyncio coroutine concurrency",
                "python asyncio task cancellation",
            ],
            metadatas=[
                {"wing": "w", "room": "r", recency.FILED_AT_KEY: _iso(400)},
                {"wing": "w", "room": "r", recency.FILED_AT_KEY: _iso(0)},
                {"wing": "w", "room": "r"},
            ],
        )
        return col

    def test_recency_preserves_recall(self, monkeypatch, palace_path):
        # Even with recency on, every seeded drawer must remain in the set.
        from mempalace import searcher

        self._seed(palace_path)
        monkeypatch.setenv("PALACE_RECENCY_BOOST", "1")
        result = searcher.search_memories("python asyncio", palace_path=palace_path, n_results=5)
        ids = {h["drawer_id"] for h in result["results"]}
        assert {"old", "new", "undated"} <= ids

    def test_disabled_by_default(self, monkeypatch, palace_path):
        # No env set → recency off → effective distance == raw (no closets).
        from mempalace import searcher

        self._seed(palace_path)
        monkeypatch.delenv("PALACE_RECENCY_BOOST", raising=False)
        result = searcher.search_memories("python asyncio", palace_path=palace_path, n_results=5)
        for h in result["results"]:
            assert h["effective_distance"] == pytest.approx(h["distance"], abs=1e-3)

    def test_enabled_pulls_fresh_drawer_up(self, monkeypatch, palace_path):
        # With recency on, the fresh drawer's effective distance drops below
        # its raw distance; the old one barely moves.
        from mempalace import searcher

        self._seed(palace_path)
        monkeypatch.setenv("PALACE_RECENCY_BOOST", "1")
        result = searcher.search_memories("python asyncio", palace_path=palace_path, n_results=5)
        by_id = {h["drawer_id"]: h for h in result["results"]}
        assert by_id["new"]["effective_distance"] < by_id["new"]["distance"]


# ---------------------------------------------------------------------------
# prune CLI
# ---------------------------------------------------------------------------


class TestPruneCli:
    def _seed(self, palace_path):
        from mempalace.backends import PalaceRef
        from mempalace.backends.chroma import ChromaBackend

        backend = ChromaBackend()
        col = backend.get_collection(
            palace=PalaceRef(id=palace_path, local_path=palace_path),
            collection_name="mempalace_drawers",
            create=True,
        )
        col.upsert(
            ids=["old1", "old2", "fresh", "undated"],
            documents=["a", "b", "c", "d"],
            metadatas=[
                {"wing": "w", "room": "r", recency.FILED_AT_KEY: _iso(200)},
                {"wing": "w", "room": "r", recency.FILED_AT_KEY: _iso(200)},
                {"wing": "w", "room": "r", recency.FILED_AT_KEY: _iso(5)},
                {"wing": "w", "room": "r"},
            ],
        )
        return col

    def _args(self, palace_path, **kw):
        import argparse

        ns = argparse.Namespace(
            palace=palace_path,
            stale_days=kw.get("stale_days", 90),
            wing=kw.get("wing"),
            room=kw.get("room"),
            confirm=kw.get("confirm", False),
            json=True,
            quiet=False,
        )
        return ns

    def test_dry_run_deletes_nothing(self, palace_path, capsys):
        import json

        from mempalace import cli

        col = self._seed(palace_path)
        before = col.count()
        cli.cmd_prune(self._args(palace_path, stale_days=90))
        out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert out["stale"] == 2
        assert out["undated_skipped"] == 1
        assert out["deleted"] == 0
        assert col.count() == before  # nothing removed

    def test_confirm_deletes_stale_only(self, palace_path, capsys):
        import json

        from mempalace import cli

        col = self._seed(palace_path)
        cli.cmd_prune(self._args(palace_path, stale_days=90, confirm=True))
        out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert out["deleted"] == 2
        # fresh + undated survive; the two old ones are gone.
        remaining = set(col.get(include=[])["ids"])
        assert remaining == {"fresh", "undated"}

    def test_undated_never_pruned(self, palace_path, capsys):
        import json

        from mempalace import cli

        col = self._seed(palace_path)
        # Tiny threshold so every dated drawer qualifies — undated must still
        # survive, because we never delete a drawer we can't date.
        cli.cmd_prune(self._args(palace_path, stale_days=1, confirm=True))
        json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert "undated" in set(col.get(include=[])["ids"])

    def test_wing_scope_limits_prune(self, palace_path, capsys):
        import json

        from mempalace import cli

        col = self._seed(palace_path)
        col.upsert(
            ids=["other"],
            documents=["e"],
            metadatas=[{"wing": "other", "room": "r", recency.FILED_AT_KEY: _iso(200)}],
        )
        cli.cmd_prune(self._args(palace_path, stale_days=90, wing="other", confirm=True))
        out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert out["deleted"] == 1
        ids = set(col.get(include=[])["ids"])
        assert "other" not in ids
        assert {"old1", "old2"} <= ids  # wing=w untouched

    def test_nonpositive_stale_days_rejected(self, palace_path, capsys):
        import json

        from mempalace import cli

        self._seed(palace_path)
        cli.cmd_prune(self._args(palace_path, stale_days=0))
        out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert "error" in out

    def test_nothing_stale_is_clean(self, palace_path, capsys):
        import json

        from mempalace import cli

        self._seed(palace_path)
        cli.cmd_prune(self._args(palace_path, stale_days=100000, confirm=True))
        out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert out["stale"] == 0
        assert out["deleted"] == 0
