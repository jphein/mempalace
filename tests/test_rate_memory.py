"""Tests for the feedback-rating loop (#159, Tier 1).

Covers three layers:
  * pure metadata read/write + bounded distance adjustment (mempalace.ratings)
  * the mempalace_rate_memory MCP tool (storage, verbatim-preservation, errors)
  * the searcher integration (rating reorders but never excludes — recall)
"""

import pytest

from mempalace import ratings


# ---------------------------------------------------------------------------
# Pure ratings helpers
# ---------------------------------------------------------------------------


class TestRatingMetadata:
    def test_extract_from_empty(self):
        assert ratings.extract_rating_from_metadata(None) == (0, 0)
        assert ratings.extract_rating_from_metadata({}) == (0, 0)

    def test_extract_reads_counters(self):
        meta = {ratings.USEFUL_KEY: 3, ratings.NOT_USEFUL_KEY: 1}
        assert ratings.extract_rating_from_metadata(meta) == (3, 1)

    @pytest.mark.parametrize("bad", ["x", None, -4, 2.9])
    def test_coerce_tolerates_garbage(self, bad):
        meta = {ratings.USEFUL_KEY: bad}
        useful, _ = ratings.extract_rating_from_metadata(meta)
        assert useful >= 0

    def test_apply_increments_useful(self):
        meta = {"wing": "w"}
        ratings.apply_rating_to_metadata(meta, True)
        assert meta[ratings.USEFUL_KEY] == 1
        # Untouched fields preserved.
        assert meta["wing"] == "w"

    def test_apply_increments_not_useful(self):
        meta = {}
        ratings.apply_rating_to_metadata(meta, False)
        assert meta[ratings.NOT_USEFUL_KEY] == 1
        assert meta.get(ratings.USEFUL_KEY) is None

    def test_apply_accumulates(self):
        meta = {}
        for _ in range(3):
            ratings.apply_rating_to_metadata(meta, True)
        ratings.apply_rating_to_metadata(meta, False)
        assert ratings.net_rating(meta) == 2


class TestRatingDistanceAdjustment:
    def test_zero_net_is_noop(self):
        assert ratings.rating_distance_adjustment({}) == 0.0
        assert (
            ratings.rating_distance_adjustment({ratings.USEFUL_KEY: 2, ratings.NOT_USEFUL_KEY: 2})
            == 0.0
        )

    def test_useful_pulls_distance_down(self):
        adj = ratings.rating_distance_adjustment({ratings.USEFUL_KEY: 1})
        assert adj < 0  # added to distance → moves drawer up

    def test_not_useful_pushes_distance_up(self):
        adj = ratings.rating_distance_adjustment({ratings.NOT_USEFUL_KEY: 1})
        assert adj > 0

    def test_adjustment_is_capped_both_directions(self):
        big_useful = ratings.rating_distance_adjustment({ratings.USEFUL_KEY: 999})
        big_penalty = ratings.rating_distance_adjustment({ratings.NOT_USEFUL_KEY: 999})
        assert big_useful == -ratings.RATING_DISTANCE_CAP
        assert big_penalty == ratings.RATING_DISTANCE_CAP

    def test_step_scales_with_net(self):
        one = ratings.rating_distance_adjustment({ratings.USEFUL_KEY: 1})
        two = ratings.rating_distance_adjustment({ratings.USEFUL_KEY: 2})
        assert abs(two) > abs(one)


# ---------------------------------------------------------------------------
# MCP tool integration
# ---------------------------------------------------------------------------


def _patch_mcp_server(monkeypatch, config, kg):
    from mempalace import mcp_server

    monkeypatch.setattr(mcp_server, "_config", config)
    monkeypatch.setattr(mcp_server, "_get_kg", lambda *a, **kw: kg)


class TestRateMemoryTool:
    def test_useful_then_not_useful_accumulate(self, monkeypatch, config, palace_path, kg):
        _patch_mcp_server(monkeypatch, config, kg)
        from mempalace.mcp_server import tool_add_drawer, tool_rate_memory

        added = tool_add_drawer(wing="w", room="r", content="a rated drawer")
        did = added["drawer_id"]

        r1 = tool_rate_memory(drawer_id=did, useful=True)
        assert r1["success"] is True
        assert r1["rating_useful"] == 1
        assert r1["net_rating"] == 1

        r2 = tool_rate_memory(drawer_id=did, useful=True)
        assert r2["rating_useful"] == 2

        r3 = tool_rate_memory(drawer_id=did, useful=False)
        assert r3["rating_not_useful"] == 1
        assert r3["net_rating"] == 1

    def test_rating_does_not_mutate_content(self, monkeypatch, config, palace_path, kg):
        _patch_mcp_server(monkeypatch, config, kg)
        from mempalace.mcp_server import tool_add_drawer, tool_get_drawer, tool_rate_memory

        original = "the exact verbatim words that must never change"
        added = tool_add_drawer(wing="w", room="r", content=original)
        did = added["drawer_id"]

        tool_rate_memory(drawer_id=did, useful=True)
        tool_rate_memory(drawer_id=did, useful=False)

        fetched = tool_get_drawer(did)
        assert fetched["content"] == original

    def test_rating_persists_in_metadata(self, monkeypatch, config, palace_path, kg):
        _patch_mcp_server(monkeypatch, config, kg)
        from mempalace.mcp_server import tool_add_drawer, tool_get_drawer, tool_rate_memory

        added = tool_add_drawer(wing="w", room="r", content="persist my rating")
        did = added["drawer_id"]
        tool_rate_memory(drawer_id=did, useful=True)

        fetched = tool_get_drawer(did)
        assert ratings.extract_rating_from_metadata(fetched["metadata"]) == (1, 0)

    def test_unknown_drawer_errors(self, monkeypatch, config, palace_path, kg):
        _patch_mcp_server(monkeypatch, config, kg)
        from mempalace.mcp_server import tool_add_drawer, tool_rate_memory

        # Seed one drawer so the collection exists; the rated id is still absent.
        tool_add_drawer(wing="w", room="r", content="seed so the collection exists")
        result = tool_rate_memory(drawer_id="does-not-exist", useful=True)
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_non_bool_useful_rejected(self, monkeypatch, config, palace_path, kg):
        _patch_mcp_server(monkeypatch, config, kg)
        from mempalace.mcp_server import tool_add_drawer, tool_rate_memory

        added = tool_add_drawer(wing="w", room="r", content="content")
        result = tool_rate_memory(drawer_id=added["drawer_id"], useful="yes")
        assert result["success"] is False


# ---------------------------------------------------------------------------
# Searcher integration — recall preserved
# ---------------------------------------------------------------------------


class TestSearchRatingSignal:
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
            ids=["d1", "d2", "d3"],
            documents=[
                "python asyncio event loop scheduling",
                "python asyncio coroutine concurrency",
                "python asyncio task cancellation",
            ],
            metadatas=[
                {"wing": "w", "room": "r"},
                {"wing": "w", "room": "r"},
                {"wing": "w", "room": "r"},
            ],
        )
        return col

    def test_rated_drawer_still_present(self, monkeypatch, palace_path):
        # Even a heavily down-rated drawer must remain in the result set —
        # a rating reorders, never excludes (100% recall requirement).
        from mempalace import searcher
        from mempalace.ratings import NOT_USEFUL_KEY

        col = self._seed(palace_path)
        col.update(ids=["d1"], metadatas=[{"wing": "w", "room": "r", NOT_USEFUL_KEY: 50}])

        monkeypatch.setenv("PALACE_RATING_BOOST", "1")
        result = searcher.search_memories("python asyncio", palace_path=palace_path, n_results=5)
        ids = {h["drawer_id"] for h in result["results"]}
        assert "d1" in ids

    def test_rating_score_surfaced_on_hits(self, monkeypatch, palace_path):
        from mempalace import searcher
        from mempalace.ratings import USEFUL_KEY

        col = self._seed(palace_path)
        col.update(ids=["d2"], metadatas=[{"wing": "w", "room": "r", USEFUL_KEY: 4}])

        result = searcher.search_memories("python asyncio", palace_path=palace_path, n_results=5)
        by_id = {h["drawer_id"]: h for h in result["results"]}
        assert by_id["d2"]["rating_score"] == 4
        assert by_id["d1"]["rating_score"] == 0

    def test_disable_flag_zeroes_adjustment(self, monkeypatch, palace_path):
        from mempalace import searcher
        from mempalace.ratings import USEFUL_KEY

        col = self._seed(palace_path)
        col.update(ids=["d3"], metadatas=[{"wing": "w", "room": "r", USEFUL_KEY: 10}])

        monkeypatch.setenv("PALACE_RATING_BOOST", "0")
        result = searcher.search_memories("python asyncio", palace_path=palace_path, n_results=5)
        # rating_score is still reported (transparency) but no drawer's
        # effective_distance moved below its raw distance from the rating.
        for h in result["results"]:
            if h["drawer_id"] == "d3":
                # closet_boost is 0 here (no closets seeded), so effective
                # distance should equal raw distance when boost is disabled.
                assert h["effective_distance"] == pytest.approx(h["distance"], abs=1e-3)
