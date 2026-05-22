"""Multi-label tag tests (techempower-org/mempalace#39).

Covers the helper module, the chroma backend's tag post-filter, the MCP
tools that read/write tags, and the searcher integration. Postgres
backend coverage lives in ``tests/test_backends_postgres.py`` and is
gated on the optional dependency.
"""

from __future__ import annotations

import pytest

from mempalace.tags import (
    apply_tags_to_metadata,
    extract_tags_from_metadata,
    metadata_matches_all_tags,
    normalise_tag,
    normalise_tags,
    string_to_tags,
    tags_to_string,
)


# ---------------------------------------------------------------------------
# Helper module
# ---------------------------------------------------------------------------


class TestNormaliseTag:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("important", "important"),
            ("ImPortAnt", "important"),
            ("  spaced  ", "spaced"),
            ("Project X", "project-x"),
            ("foo_bar", "foo_bar"),
            ("v1.2.3", "v1.2.3"),
            ("with-hyphen", "with-hyphen"),
        ],
    )
    def test_canonical_forms(self, raw, expected):
        assert normalise_tag(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", "!@#$", None, 42, [], {}])
    def test_invalid_inputs_return_none(self, raw):
        assert normalise_tag(raw) is None

    def test_strips_disallowed_chars(self):
        assert normalise_tag("foo!bar@baz") == "foobarbaz"


class TestNormaliseTags:
    def test_dedupes_preserving_order(self):
        assert normalise_tags(["a", "b", "A", "B"]) == ["a", "b"]

    def test_drops_invalid_entries(self):
        assert normalise_tags(["good", "", None, "also-good"]) == ["good", "also-good"]

    def test_empty_input(self):
        assert normalise_tags(None) == []
        assert normalise_tags([]) == []


class TestSerialisation:
    def test_tags_to_string_wraps_with_pipes(self):
        assert tags_to_string(["a", "b", "c"]) == "|a|b|c|"

    def test_empty_list_serialises_to_empty_string(self):
        assert tags_to_string([]) == ""

    def test_round_trip(self):
        original = ["important", "project-x", "draft"]
        assert string_to_tags(tags_to_string(original)) == original

    def test_string_to_tags_empty(self):
        assert string_to_tags("") == []
        assert string_to_tags(None) == []


class TestApplyTagsToMetadata:
    def test_writes_both_list_and_string_form(self):
        meta = {"wing": "w"}
        result = apply_tags_to_metadata(meta, ["b", "a"])
        assert result == ["b", "a"]
        assert meta["tags"] == ["b", "a"]
        assert meta["tags_str"] == "|b|a|"

    def test_empty_clears_existing(self):
        meta = {"tags": ["old"], "tags_str": "|old|"}
        apply_tags_to_metadata(meta, [])
        assert "tags" not in meta
        assert "tags_str" not in meta

    def test_none_preserves_existing(self):
        meta = {"tags": ["keep"], "tags_str": "|keep|"}
        returned = apply_tags_to_metadata(meta, None)
        assert returned == ["keep"]
        assert meta["tags"] == ["keep"]


class TestExtractTagsFromMetadata:
    def test_reads_list_form(self):
        assert extract_tags_from_metadata({"tags": ["a", "b"]}) == ["a", "b"]

    def test_falls_back_to_string_form(self):
        assert extract_tags_from_metadata({"tags_str": "|x|y|"}) == ["x", "y"]

    def test_missing_returns_empty(self):
        assert extract_tags_from_metadata({}) == []
        assert extract_tags_from_metadata(None) == []


class TestMetadataMatchesAllTags:
    def test_all_present(self):
        meta = {"tags": ["a", "b", "c"]}
        assert metadata_matches_all_tags(meta, ["a", "c"])

    def test_partial_match_fails(self):
        meta = {"tags": ["a", "b"]}
        assert not metadata_matches_all_tags(meta, ["a", "z"])

    def test_empty_required_always_matches(self):
        assert metadata_matches_all_tags({"tags": []}, [])
        assert metadata_matches_all_tags(None, [])


# ---------------------------------------------------------------------------
# Chroma backend post-filtering (the legacy backend)
# ---------------------------------------------------------------------------


class TestChromaBackendTags:
    """Exercise the chroma collection's tag post-filter behaviour."""

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
            ids=["d1", "d2", "d3", "d4"],
            documents=[
                "alpha document about indexing",
                "beta document about indexing",
                "gamma untagged document",
                "delta tagged with one tag",
            ],
            metadatas=[
                {
                    "wing": "w",
                    "room": "r",
                    "tags": ["important", "project-x"],
                    "tags_str": "|important|project-x|",
                },
                {
                    "wing": "w",
                    "room": "r",
                    "tags": ["important"],
                    "tags_str": "|important|",
                },
                {"wing": "w", "room": "r"},
                {
                    "wing": "w",
                    "room": "r",
                    "tags": ["project-x"],
                    "tags_str": "|project-x|",
                },
            ],
        )
        return col

    def test_get_filters_by_single_tag(self, palace_path):
        col = self._seed(palace_path)
        result = col.get(where={"tags": {"$contains_all": ["important"]}})
        assert set(result.ids) == {"d1", "d2"}

    def test_get_filters_with_and_logic(self, palace_path):
        col = self._seed(palace_path)
        result = col.get(where={"tags": {"$contains_all": ["important", "project-x"]}})
        assert result.ids == ["d1"]

    def test_get_returns_empty_when_no_drawer_has_all(self, palace_path):
        col = self._seed(palace_path)
        result = col.get(where={"tags": {"$contains_all": ["important", "nonexistent"]}})
        assert result.ids == []

    def test_get_combines_tag_filter_with_wing(self, palace_path):
        col = self._seed(palace_path)
        result = col.get(
            where={"$and": [{"wing": "w"}, {"tags": {"$contains_all": ["project-x"]}}]}
        )
        assert set(result.ids) == {"d1", "d4"}

    def test_contains_any(self, palace_path):
        col = self._seed(palace_path)
        result = col.get(where={"tags": {"$contains_any": ["project-x", "nonexistent"]}})
        assert set(result.ids) == {"d1", "d4"}

    def test_query_post_filters_results(self, palace_path):
        col = self._seed(palace_path)
        result = col.query(
            query_texts=["indexing"],
            n_results=10,
            where={"tags": {"$contains_all": ["important"]}},
        )
        # Expect d1+d2 (the indexing-mentioning docs that carry "important").
        returned = set(result.ids[0])
        assert returned <= {"d1", "d2"}
        assert returned, "tag-filtered query should return at least one hit"

    def test_query_without_tags_still_works(self, palace_path):
        col = self._seed(palace_path)
        result = col.query(query_texts=["indexing"], n_results=10)
        # Untagged drawer is included when no tag filter is applied.
        assert "d3" in set(result.ids[0])


# ---------------------------------------------------------------------------
# MCP tool integration
# ---------------------------------------------------------------------------


def _patch_mcp_server(monkeypatch, config, kg):
    """Mirror tests/test_mcp_server.py's helper for module-global patching."""
    from mempalace import mcp_server

    monkeypatch.setattr(mcp_server, "_config", config)
    monkeypatch.setattr(mcp_server, "_get_kg", lambda *a, **kw: kg)


class TestAddDrawerWithTags:
    def test_tags_persisted_and_normalised(self, monkeypatch, config, palace_path, kg):
        _patch_mcp_server(monkeypatch, config, kg)
        from mempalace.mcp_server import tool_add_drawer, tool_get_drawer

        result = tool_add_drawer(
            wing="w",
            room="r",
            content="content for the tagged drawer",
            tags=["ImPortAnt", "Project X", "important"],  # dup + casing
        )
        assert result["success"] is True
        assert result["tags"] == ["important", "project-x"]

        fetched = tool_get_drawer(result["drawer_id"])
        assert fetched["tags"] == ["important", "project-x"]

    def test_no_tags_means_empty_list(self, monkeypatch, config, palace_path, kg):
        _patch_mcp_server(monkeypatch, config, kg)
        from mempalace.mcp_server import tool_add_drawer

        result = tool_add_drawer(wing="w", room="r", content="untagged drawer content")
        assert result["success"] is True
        assert result["tags"] == []


class TestUpdateDrawerTags:
    def test_replace_tag_list(self, monkeypatch, config, palace_path, kg):
        _patch_mcp_server(monkeypatch, config, kg)
        from mempalace.mcp_server import tool_add_drawer, tool_update_drawer

        added = tool_add_drawer(wing="w", room="r", content="content to retag", tags=["original"])
        updated = tool_update_drawer(drawer_id=added["drawer_id"], tags=["new", "other"])
        assert updated["success"] is True
        assert updated["tags"] == ["new", "other"]

    def test_clear_with_empty_list(self, monkeypatch, config, palace_path, kg):
        _patch_mcp_server(monkeypatch, config, kg)
        from mempalace.mcp_server import tool_add_drawer, tool_update_drawer

        added = tool_add_drawer(
            wing="w", room="r", content="content to clear tags from", tags=["x", "y"]
        )
        updated = tool_update_drawer(drawer_id=added["drawer_id"], tags=[])
        assert updated["tags"] == []

    def test_none_preserves_existing(self, monkeypatch, config, palace_path, kg):
        _patch_mcp_server(monkeypatch, config, kg)
        from mempalace.mcp_server import tool_add_drawer, tool_update_drawer

        added = tool_add_drawer(
            wing="w", room="r", content="content with stable tags", tags=["keep-me"]
        )
        # Update only the room — tags must survive.
        updated = tool_update_drawer(drawer_id=added["drawer_id"], room="r2")
        assert updated["tags"] == ["keep-me"]


class TestListTags:
    def test_lists_tags_with_counts(self, monkeypatch, config, palace_path, kg):
        _patch_mcp_server(monkeypatch, config, kg)
        from mempalace.mcp_server import tool_add_drawer, tool_list_tags

        tool_add_drawer(wing="w", room="r", content="d1 content alpha", tags=["a", "b"])
        tool_add_drawer(wing="w", room="r", content="d2 content beta", tags=["a"])
        tool_add_drawer(wing="w", room="r", content="d3 content gamma", tags=["c"])

        result = tool_list_tags()
        by_tag = {item["tag"]: item["count"] for item in result["tags"]}
        assert by_tag == {"a": 2, "b": 1, "c": 1}
        assert result["total_unique_tags"] == 3

    def test_min_count_filter(self, monkeypatch, config, palace_path, kg):
        _patch_mcp_server(monkeypatch, config, kg)
        from mempalace.mcp_server import tool_add_drawer, tool_list_tags

        tool_add_drawer(wing="w", room="r", content="d1 content one", tags=["popular"])
        tool_add_drawer(wing="w", room="r", content="d2 content two", tags=["popular"])
        tool_add_drawer(wing="w", room="r", content="d3 content three", tags=["rare"])

        result = tool_list_tags(min_count=2)
        tags_returned = [item["tag"] for item in result["tags"]]
        assert tags_returned == ["popular"]

    def test_scoped_to_wing(self, monkeypatch, config, palace_path, kg):
        _patch_mcp_server(monkeypatch, config, kg)
        from mempalace.mcp_server import tool_add_drawer, tool_list_tags

        tool_add_drawer(wing="w1", room="r", content="content w1 one", tags=["a"])
        tool_add_drawer(wing="w2", room="r", content="content w2 one", tags=["b"])

        result = tool_list_tags(wing="w1")
        tags_returned = [item["tag"] for item in result["tags"]]
        assert tags_returned == ["a"]


class TestListDrawersWithTags:
    def test_filter_by_tags(self, monkeypatch, config, palace_path, kg):
        _patch_mcp_server(monkeypatch, config, kg)
        from mempalace.mcp_server import tool_add_drawer, tool_list_drawers

        tool_add_drawer(wing="w", room="r", content="match for tag filter alpha", tags=["a", "b"])
        tool_add_drawer(wing="w", room="r", content="no match for tag filter beta", tags=["b"])
        tool_add_drawer(wing="w", room="r", content="alternate match gamma", tags=["a", "b", "c"])

        result = tool_list_drawers(tags=["a", "b"])
        assert result["total"] == 2
        returned_tags = [set(d["tags"]) for d in result["drawers"]]
        assert all({"a", "b"} <= tags for tags in returned_tags)


# ---------------------------------------------------------------------------
# Searcher
# ---------------------------------------------------------------------------


class TestSearcherBuildWhere:
    def test_tags_added_with_and(self):
        from mempalace.searcher import build_where_filter

        where = build_where_filter(wing="w", room="r", tags=["a", "b"])
        # Wing + room + tags → three AND clauses.
        assert where == {
            "$and": [
                {"wing": "w"},
                {"room": "r"},
                {"tags": {"$contains_all": ["a", "b"]}},
            ]
        }

    def test_only_tags(self):
        from mempalace.searcher import build_where_filter

        where = build_where_filter(tags=["solo"])
        assert where == {"tags": {"$contains_all": ["solo"]}}

    def test_empty_tags_omits_clause(self):
        from mempalace.searcher import build_where_filter

        assert build_where_filter(tags=[]) == {}


class TestSearcherWithTags:
    def test_search_returns_only_tagged_drawers(self, monkeypatch, config, palace_path, kg):
        _patch_mcp_server(monkeypatch, config, kg)
        from mempalace.mcp_server import tool_add_drawer, tool_search

        tool_add_drawer(
            wing="w",
            room="r",
            content="JWT tokens and authentication flow notes one",
            tags=["auth"],
        )
        tool_add_drawer(
            wing="w",
            room="r",
            content="JWT tokens and authentication flow notes two",
            tags=["other"],
        )

        result = tool_search(query="JWT tokens authentication", tags=["auth"], limit=10)
        hits = result.get("results") or []
        assert hits, "expected at least one auth-tagged hit"
        for hit in hits:
            # ``tool_search`` doesn't echo tags per hit today (the searcher's
            # public shape predates tags); the assertion is that filtering
            # discarded the non-auth drawer.
            assert "two" not in hit["text"], "search returned a drawer that lacks the required tag"
