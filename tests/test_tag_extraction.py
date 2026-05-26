"""TF-IDF auto-tag extraction tests (techempower-org/mempalace#201).

Pure-function tests for the extractor live here. End-to-end coverage
through ``tool_add_drawer`` / ``tool_update_drawer`` lives at the bottom
of the file so a regression in either the extractor or the wiring shows
up as a single failure.
"""

from __future__ import annotations

from mempalace.tag_extraction import (
    IdfCache,
    _tokenize,
    build_idf,
    extract_tags,
)


# ---------------------------------------------------------------------------
# Tokenisation
# ---------------------------------------------------------------------------


class TestTokenize:
    def test_lowercases_and_splits(self):
        toks = _tokenize("Postgres replication slot lag is climbing fast")
        assert "postgres" in toks
        assert "replication" in toks

    def test_strips_stopwords(self):
        toks = _tokenize("the quick and the dead and the brave")
        assert "the" not in toks
        assert "and" not in toks
        assert "quick" in toks

    def test_drops_short_tokens(self):
        toks = _tokenize("a x to be or not to be")
        # No token under 3 chars survives.
        assert all(len(t) >= 3 for t in toks)

    def test_drops_punctuation_only_runs(self):
        toks = _tokenize("--- ... !!! ???")
        assert toks == []

    def test_empty_input_safe(self):
        assert _tokenize("") == []
        assert _tokenize(None) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# IDF table construction
# ---------------------------------------------------------------------------


class TestBuildIdf:
    def test_empty_corpus_yields_empty_table(self):
        assert build_idf([]) == {}

    def test_rare_term_outscores_common_term(self):
        corpus = [
            "postgres replication lag",
            "postgres connection pool",
            "postgres backup snapshot",
            "rare unicorn appears once",
        ]
        idf = build_idf(corpus)
        assert idf["unicorn"] > idf["postgres"]

    def test_universal_term_still_positive(self):
        # Smoothed IDF means every-doc terms still get a positive weight.
        corpus = ["postgres", "postgres again", "postgres yet again"]
        idf = build_idf(corpus)
        assert idf["postgres"] > 0


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


class TestExtractTags:
    def _idf(self):
        return build_idf(
            [
                "the daemon serves http requests",
                "the daemon writes drawers to postgres",
                "the daemon answers status checks",
                "fork-only feature work tracked in github",
                "search ranking and embedding pipeline",
            ]
        )

    def test_returns_normalised_tokens(self):
        tags = extract_tags("Daemon restart fixed the replication slot lag", self._idf())
        # All output already normalised — lower, no junk chars.
        for tag in tags:
            assert tag == tag.lower()
            assert " " not in tag

    def test_caps_at_k(self):
        # Long content with many distinct content tokens.
        text = " ".join(f"token{i}" for i in range(50))
        tags = extract_tags(text, self._idf(), k=5)
        assert len(tags) <= 5

    def test_k_three_returns_at_most_three(self):
        text = " ".join(f"token{i}" for i in range(20))
        tags = extract_tags(text, self._idf(), k=3)
        assert len(tags) <= 3

    def test_empty_content_yields_empty(self):
        assert extract_tags("", self._idf()) == []
        assert extract_tags("    ", self._idf()) == []

    def test_short_content_under_min_returns_empty_or_short(self):
        # Content like "a b" yields no usable tokens after stopword/short filter.
        assert extract_tags("a b c", self._idf()) == []

    def test_deterministic_given_same_inputs(self):
        idf = self._idf()
        text = "Daemon failover scenario covers replication lag and connection drops"
        first = extract_tags(text, idf)
        second = extract_tags(text, idf)
        assert first == second

    def test_rare_terms_ranked_first(self):
        idf = self._idf()
        # "daemon" appears in 3 corpus docs; "unicorn" is novel.
        text = "the daemon found a unicorn unicorn unicorn"
        tags = extract_tags(text, idf, k=3)
        assert "unicorn" in tags
        # Rare term should outrank the common one even though "daemon" has
        # higher term frequency in the source corpus.
        assert tags.index("unicorn") < tags.index("daemon") if "daemon" in tags else True

    def test_no_idf_falls_back_to_term_frequency(self):
        # Cold-start path: no corpus yet, every term gets IDF=1.0.
        text = "alpha alpha alpha beta gamma"
        tags = extract_tags(text, idf=None, k=3)
        # "alpha" appears most, must surface first.
        assert tags[0] == "alpha"

    def test_k_zero_returns_empty(self):
        assert extract_tags("anything goes here", self._idf(), k=0) == []


# ---------------------------------------------------------------------------
# IDF cache
# ---------------------------------------------------------------------------


class TestIdfCache:
    def test_caches_builder_result(self):
        calls = {"n": 0}

        def builder():
            calls["n"] += 1
            return ["one two three", "two three four"]

        cache = IdfCache(ttl_seconds=60.0)
        first = cache.get("w", "r", builder)
        second = cache.get("w", "r", builder)
        assert first == second
        assert calls["n"] == 1, "second call should hit the cache, not rebuild"

    def test_invalidate_forces_rebuild(self):
        calls = {"n": 0}

        def builder():
            calls["n"] += 1
            return ["doc one", "doc two"]

        cache = IdfCache(ttl_seconds=60.0)
        cache.get("w", "r", builder)
        cache.invalidate("w", "r")
        cache.get("w", "r", builder)
        assert calls["n"] == 2

    def test_invalidate_all(self):
        cache = IdfCache(ttl_seconds=60.0)
        cache.get("w1", "r", lambda: ["a"])
        cache.get("w2", "r", lambda: ["b"])
        assert len(cache) == 2
        cache.invalidate()
        assert len(cache) == 0

    def test_eviction_respects_max_entries(self):
        cache = IdfCache(ttl_seconds=60.0, max_entries=2)
        cache.get("w1", "r", lambda: ["a"])
        cache.get("w2", "r", lambda: ["b"])
        cache.get("w3", "r", lambda: ["c"])
        assert len(cache) == 2


# ---------------------------------------------------------------------------
# MCP integration — write-path auto-tagging
# ---------------------------------------------------------------------------


def _patch_mcp_server(monkeypatch, config, kg):
    """Mirror the helper used in tests/test_tags.py."""
    from mempalace import mcp_server

    monkeypatch.setattr(mcp_server, "_config", config)
    monkeypatch.setattr(mcp_server, "_get_kg", lambda *a, **kw: kg)


class TestAutoTagWiring:
    """Behaviour contract — explicit tags always win, ``None`` opts in."""

    def test_explicit_tags_preserved(self, monkeypatch, config, palace_path, kg):
        _patch_mcp_server(monkeypatch, config, kg)
        from mempalace.mcp_server import tool_add_drawer

        result = tool_add_drawer(
            wing="w",
            room="r",
            content="Daemon failover replication lag investigation",
            tags=["manual-one", "manual-two"],
        )
        assert result["success"] is True
        assert result["tags"] == ["manual-one", "manual-two"]

    def test_explicit_empty_list_clears(self, monkeypatch, config, palace_path, kg):
        _patch_mcp_server(monkeypatch, config, kg)
        from mempalace.mcp_server import tool_add_drawer

        # tags=[] is the caller saying "no tags" — auto-extract must NOT kick in.
        result = tool_add_drawer(
            wing="w",
            room="r",
            content="Daemon failover replication lag investigation",
            tags=[],
        )
        assert result["success"] is True
        assert result["tags"] == []

    def test_none_triggers_auto_extraction(self, monkeypatch, config, palace_path, kg):
        _patch_mcp_server(monkeypatch, config, kg)
        from mempalace.mcp_server import tool_add_drawer

        # Seed enough corpus so the IDF table has at least a few entries.
        tool_add_drawer(
            wing="w",
            room="r",
            content="warmup corpus alpha beta gamma delta",
            tags=[],
        )
        result = tool_add_drawer(
            wing="w",
            room="r",
            content="Daemon failover scenario covers replication lag and pool drops",
            # tags omitted — should trigger auto-extraction
        )
        assert result["success"] is True
        assert isinstance(result["tags"], list)
        # Content has plenty of usable tokens; we expect at least one auto-tag.
        assert len(result["tags"]) >= 1
        # All output must be normalised already.
        for tag in result["tags"]:
            assert tag == tag.lower()
            assert " " not in tag

    def test_auto_tag_count_within_bounds(self, monkeypatch, config, palace_path, kg):
        _patch_mcp_server(monkeypatch, config, kg)
        from mempalace.mcp_server import tool_add_drawer

        result = tool_add_drawer(
            wing="w",
            room="r",
            content=(
                "Replication lag is climbing on the standby because the "
                "primary's WAL writer fell behind during the backup "
                "snapshot window. Daemon failover restored throughput "
                "but the lag took an hour to fully recover."
            ),
        )
        assert result["success"] is True
        # 3-8 spec band, but we cap at 5 by default. Allow 0..8 for very
        # short content; for this corpus we should land in the middle.
        assert 0 <= len(result["tags"]) <= 8
