"""Tests for mempalace.auto_query.formatter."""

from mempalace.auto_query import MCPCall, Signal, SignalSet
from mempalace.auto_query.formatter import (
    MAX_INJECTION_CHARS,
    MAX_PREVIEW_CHARS,
    SENTINEL_CLOSE,
    SENTINEL_OPEN,
    _format_provenance,
    _format_trigger_line,
    _signal_summary,
    _truncate,
    format_injection,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_signals(
    entity=None,
    temporal=None,
    resumption=False,
    explicit=False,
    total_score=0,
    project_wing="",
    query_text="",
):
    """Build a SignalSet with sane defaults."""
    return SignalSet(
        entity=entity or [],
        temporal=temporal or [],
        resumption=resumption,
        explicit=explicit,
        total_score=total_score,
        project_wing=project_wing,
        query_text=query_text,
    )


def _make_search_result(
    text="Some drawer content",
    wing="wing_alice",
    room="people",
    drawer_id="0abc1234",
    created_at="2026-04-18",
    similarity=0.85,
):
    return {
        "drawer_id": drawer_id,
        "text": text,
        "wing": wing,
        "room": room,
        "created_at": created_at,
        "similarity": similarity,
    }


# ===================================================================
# TestSearchResults
# ===================================================================


class TestSearchResults:
    """mempalace_search result formatting."""

    def test_normal_three_results(self):
        """Three results should format correctly with wing/room, drawer_id, date, preview."""
        tool_call = MCPCall(
            tool="mempalace_search",
            args={"query": "alice", "wing": "wing_alice", "limit": 3},
        )
        mcp_result = {
            "results": [
                _make_search_result(
                    text="Alice mentioned the new oncall rotation",
                    drawer_id="0abc1234",
                    created_at="2026-04-18",
                ),
                _make_search_result(
                    text="Decided to move Alice off firefighter rotation",
                    drawer_id="0def5678",
                    created_at="2026-03-22",
                    room="decisions",
                ),
                _make_search_result(
                    text="1:1 with Alice discussed promo packet",
                    drawer_id="0fed9012",
                    created_at="2026-02-10",
                    room="meetings",
                ),
            ]
        }
        signals = _make_signals(
            entity=[Signal(kind="entity", name="Alice", score=3, wing="wing_alice")],
            total_score=6,
        )
        result = format_injection(tool_call, mcp_result, signals, latency_ms=287)

        assert result is not None
        assert "results (3):" in result
        assert "0abc1234" in result
        assert "0def5678" in result
        assert "0fed9012" in result
        assert "wing_alice/people" in result
        assert "wing_alice/decisions" in result
        assert "wing_alice/meetings" in result
        assert "(2026-04-18)" in result

    def test_empty_results_returns_none(self):
        """Empty search results should return None."""
        tool_call = MCPCall(tool="mempalace_search", args={"query": "nothing"})
        result = format_injection(tool_call, {"results": []}, _make_signals(), latency_ms=100)
        assert result is None

    def test_missing_results_key_returns_none(self):
        """Missing 'results' key should return None."""
        tool_call = MCPCall(tool="mempalace_search", args={"query": "nothing"})
        result = format_injection(tool_call, {}, _make_signals(), latency_ms=100)
        assert result is None

    def test_single_result(self):
        """A single search result should format correctly."""
        tool_call = MCPCall(tool="mempalace_search", args={"query": "bob", "limit": 1})
        mcp_result = {"results": [_make_search_result(text="Bob is great")]}
        signals = _make_signals(total_score=4)
        result = format_injection(tool_call, mcp_result, signals, latency_ms=50)

        assert result is not None
        assert "results (1):" in result
        assert "Bob is great" in result

    def test_long_text_truncated(self):
        """Results with text longer than MAX_PREVIEW_CHARS should be truncated."""
        long_text = "A" * 500
        tool_call = MCPCall(tool="mempalace_search", args={"query": "test"})
        mcp_result = {"results": [_make_search_result(text=long_text)]}
        signals = _make_signals(total_score=4)
        result = format_injection(tool_call, mcp_result, signals, latency_ms=50)

        assert result is not None
        # The full 500-char text should not appear; a truncated version should.
        assert long_text not in result
        assert "..." in result
        # The truncated preview should be a prefix of the original.
        assert "A" * MAX_PREVIEW_CHARS in result


# ===================================================================
# TestKGResults
# ===================================================================


class TestKGResults:
    """mempalace_kg_query result formatting."""

    def test_outgoing_relationships(self):
        """Outgoing KG relationships should be formatted as subject->pred->object."""
        tool_call = MCPCall(
            tool="mempalace_kg_query",
            args={"entity": "Alice", "direction": "both"},
        )
        mcp_result = {
            "outgoing": [
                {
                    "subject": "Alice",
                    "predicate": "works_on",
                    "object": "mempalace",
                    "valid_from": "2026-01-01",
                },
            ],
            "incoming": [],
        }
        signals = _make_signals(
            entity=[Signal(kind="entity", name="Alice", score=3)],
            total_score=5,
        )
        result = format_injection(tool_call, mcp_result, signals, latency_ms=120)

        assert result is not None
        assert "Alice -> works_on -> mempalace" in result
        assert "(2026-01-01)" in result

    def test_incoming_relationships(self):
        """Incoming KG relationships should also be formatted."""
        tool_call = MCPCall(tool="mempalace_kg_query", args={"entity": "mempalace"})
        mcp_result = {
            "outgoing": [],
            "incoming": [
                {
                    "subject": "Alice",
                    "predicate": "contributes_to",
                    "object": "mempalace",
                },
            ],
        }
        signals = _make_signals(total_score=4)
        result = format_injection(tool_call, mcp_result, signals, latency_ms=100)

        assert result is not None
        assert "Alice -> contributes_to -> mempalace" in result

    def test_mixed_outgoing_and_incoming(self):
        """Both outgoing and incoming should appear in one block."""
        tool_call = MCPCall(tool="mempalace_kg_query", args={"entity": "Alice"})
        mcp_result = {
            "outgoing": [
                {"subject": "Alice", "predicate": "loves", "object": "chess"},
            ],
            "incoming": [
                {"subject": "Bob", "predicate": "reports_to", "object": "Alice"},
            ],
        }
        signals = _make_signals(total_score=5)
        result = format_injection(tool_call, mcp_result, signals, latency_ms=90)

        assert result is not None
        assert "Alice -> loves -> chess" in result
        assert "Bob -> reports_to -> Alice" in result
        assert "results (2):" in result

    def test_empty_kg_returns_none(self):
        """Empty outgoing and incoming should return None."""
        tool_call = MCPCall(tool="mempalace_kg_query", args={"entity": "Nobody"})
        result = format_injection(
            tool_call,
            {"outgoing": [], "incoming": []},
            _make_signals(),
            latency_ms=50,
        )
        assert result is None

    def test_time_span_with_valid_to(self):
        """KG results with valid_from and valid_to should show both."""
        tool_call = MCPCall(tool="mempalace_kg_query", args={"entity": "Alice"})
        mcp_result = {
            "outgoing": [
                {
                    "subject": "Alice",
                    "predicate": "worked_at",
                    "object": "Acme",
                    "valid_from": "2020-01-01",
                    "valid_to": "2023-06-30",
                },
            ],
            "incoming": [],
        }
        signals = _make_signals(total_score=5)
        result = format_injection(tool_call, mcp_result, signals, latency_ms=80)

        assert result is not None
        assert "(2020-01-01 - 2023-06-30)" in result


# ===================================================================
# TestDiaryResults
# ===================================================================


class TestDiaryResults:
    """mempalace_diary_read result formatting."""

    def test_normal_diary_entries(self):
        """Diary entries should show topic, date, and entry preview."""
        tool_call = MCPCall(
            tool="mempalace_diary_read",
            args={"agent_name": "claude-code", "wing": "wing_familiar", "last_n": 3},
        )
        mcp_result = {
            "entries": [
                {
                    "entry": "Worked on cross-encoder reranking latency today",
                    "topic": "reranking",
                    "timestamp": "2026-05-20T14:00:00Z",
                    "wing": "wing_familiar",
                },
                {
                    "entry": "Concluded recall tradeoff favors speed",
                    "topic": "decisions",
                    "timestamp": "2026-05-19T10:00:00Z",
                    "wing": "wing_familiar",
                },
            ]
        }
        signals = _make_signals(resumption=True, total_score=4)
        result = format_injection(tool_call, mcp_result, signals, latency_ms=234)

        assert result is not None
        assert "results (2):" in result
        assert "[reranking]" in result
        assert "[decisions]" in result
        assert "Worked on cross-encoder" in result
        assert "(2026-05-20T14:00:00Z)" in result

    def test_aaak_compressed_content(self):
        """AAAK-compressed diary entries should be passed through verbatim."""
        aaak_content = (
            "SESSION:2026-05-20|built.palace.graph+diary.tools|ALC.req:agent.diaries.in.aaak|★★★"
        )
        tool_call = MCPCall(
            tool="mempalace_diary_read",
            args={"agent_name": "claude-code", "last_n": 1},
        )
        mcp_result = {
            "entries": [
                {
                    "entry": aaak_content,
                    "topic": "general",
                    "timestamp": "2026-05-20",
                    "wing": "wing_claude",
                },
            ]
        }
        signals = _make_signals(total_score=4)
        result = format_injection(tool_call, mcp_result, signals, latency_ms=100)

        assert result is not None
        # The AAAK content must appear verbatim (it's under 200 chars).
        assert aaak_content in result

    def test_empty_diary_returns_none(self):
        """No diary entries should return None."""
        tool_call = MCPCall(tool="mempalace_diary_read", args={"agent_name": "claude-code"})
        result = format_injection(tool_call, {"entries": []}, _make_signals(), latency_ms=50)
        assert result is None


# ===================================================================
# TestTraverseResults
# ===================================================================


class TestTraverseResults:
    """mempalace_traverse result formatting."""

    def test_connected_rooms_across_wings(self):
        """Traverse results should show rooms with hop distance and connections."""
        tool_call = MCPCall(
            tool="mempalace_traverse",
            args={"start_room": "chromadb-setup", "max_hops": 2},
        )
        mcp_result = {
            "nodes": [
                {"room": "chromadb-setup", "wing": "wing_code", "hop": 0},
                {"room": "database-design", "wing": "wing_myproject", "hop": 1},
                {"room": "performance", "wing": "wing_ops", "hop": 2},
            ],
            "edges": [
                {
                    "from_room": "chromadb-setup",
                    "to_room": "database-design",
                    "via": "shared-embedding",
                },
                {
                    "from_room": "database-design",
                    "to_room": "performance",
                    "via": "index-tuning",
                },
            ],
        }
        signals = _make_signals(total_score=5)
        result = format_injection(tool_call, mcp_result, signals, latency_ms=150)

        assert result is not None
        assert "results (3):" in result
        assert "wing_code/chromadb-setup (hop 0)" in result
        assert "wing_myproject/database-design (hop 1)" in result
        assert "connected via:" in result
        assert "database-design (via shared-embedding)" in result

    def test_empty_traverse_returns_none(self):
        """Empty nodes and edges should return None."""
        tool_call = MCPCall(tool="mempalace_traverse", args={"start_room": "nowhere"})
        result = format_injection(
            tool_call, {"nodes": [], "edges": []}, _make_signals(), latency_ms=50
        )
        assert result is None


# ===================================================================
# TestTokenBudget
# ===================================================================


class TestTokenBudget:
    """Token budget enforcement."""

    def test_large_result_set_stays_within_budget(self):
        """A result set exceeding 6000 chars should be truncated."""
        # 20 results with 500-char text each = ~10K chars before truncation.
        results = [
            _make_search_result(
                text="X" * 500,
                drawer_id="drawer_%04d" % i,
                wing="wing_test",
                room="room_%d" % i,
            )
            for i in range(20)
        ]
        tool_call = MCPCall(tool="mempalace_search", args={"query": "test", "limit": 20})
        mcp_result = {"results": results}
        signals = _make_signals(total_score=6)
        result = format_injection(tool_call, mcp_result, signals, latency_ms=300)

        assert result is not None
        assert len(result) <= MAX_INJECTION_CHARS

    def test_preview_truncation_before_count_reduction(self):
        """Preview should be truncated before dropping results."""
        # 5 results with 300-char text -- should fit by truncating previews,
        # not by dropping results.
        results = [
            _make_search_result(
                text="Y" * 300,
                drawer_id="d_%d" % i,
            )
            for i in range(5)
        ]
        tool_call = MCPCall(tool="mempalace_search", args={"query": "test", "limit": 5})
        mcp_result = {"results": results}
        signals = _make_signals(total_score=4)
        result = format_injection(tool_call, mcp_result, signals, latency_ms=100)

        assert result is not None
        assert len(result) <= MAX_INJECTION_CHARS
        # All 5 results should be present (preview truncation alone suffices).
        assert "results (5):" in result

    def test_count_reduction_when_previews_not_enough(self):
        """When even truncated previews won't fit, reduce result count."""
        # 100 results with long room names -- too many even with 50-char previews.
        results = [
            _make_search_result(
                text="Z" * 500,
                drawer_id="big_%04d" % i,
                room="very_long_room_name_that_adds_overhead_%d" % i,
                wing="wing_with_a_long_name_too",
            )
            for i in range(100)
        ]
        tool_call = MCPCall(tool="mempalace_search", args={"query": "test", "limit": 100})
        mcp_result = {"results": results}
        signals = _make_signals(total_score=6)
        result = format_injection(tool_call, mcp_result, signals, latency_ms=400)

        assert result is not None
        assert len(result) <= MAX_INJECTION_CHARS
        # Should have reduced from 100 to something smaller.
        assert "results (100):" not in result


# ===================================================================
# TestVerbatimInvariant
# ===================================================================


class TestVerbatimInvariant:
    """Every preview in the output must be a verbatim substring of the input."""

    def test_search_preview_is_verbatim(self):
        """Content preview must be a substring of the original text."""
        original_text = "The quick brown fox jumps over the lazy dog"
        tool_call = MCPCall(tool="mempalace_search", args={"query": "fox"})
        mcp_result = {"results": [_make_search_result(text=original_text)]}
        signals = _make_signals(total_score=4)
        result = format_injection(tool_call, mcp_result, signals, latency_ms=50)

        assert result is not None
        # The preview should appear in the output.
        assert original_text in result

    def test_truncated_preview_is_verbatim_prefix(self):
        """Truncated preview must be a prefix of the original text."""
        original_text = "A" * 300
        tool_call = MCPCall(tool="mempalace_search", args={"query": "test"})
        mcp_result = {"results": [_make_search_result(text=original_text)]}
        signals = _make_signals(total_score=4)
        result = format_injection(tool_call, mcp_result, signals, latency_ms=50)

        assert result is not None
        # The full text should not appear (it's truncated).
        assert original_text not in result
        # But a 200-char prefix should.
        prefix = "A" * MAX_PREVIEW_CHARS
        assert prefix in result

    def test_no_rewording(self):
        """The formatter must not reword content."""
        unique_text = "xyzzy plugh 42 is the answer to everything"
        tool_call = MCPCall(tool="mempalace_search", args={"query": "test"})
        mcp_result = {"results": [_make_search_result(text=unique_text)]}
        signals = _make_signals(total_score=4)
        result = format_injection(tool_call, mcp_result, signals, latency_ms=50)

        assert result is not None
        assert unique_text in result


# ===================================================================
# TestSentinels
# ===================================================================


class TestSentinels:
    """Sentinel tokens and required structure."""

    def _get_result(self):
        tool_call = MCPCall(tool="mempalace_search", args={"query": "alice", "limit": 3})
        mcp_result = {"results": [_make_search_result(text="Alice is here")]}
        signals = _make_signals(
            entity=[Signal(kind="entity", name="Alice", score=3)],
            total_score=3,
        )
        return format_injection(tool_call, mcp_result, signals, latency_ms=100)

    def test_starts_with_sentinel(self):
        result = self._get_result()
        assert result is not None
        assert result.startswith(SENTINEL_OPEN)

    def test_ends_with_sentinel(self):
        result = self._get_result()
        assert result is not None
        assert result.strip().endswith(SENTINEL_CLOSE)

    def test_provenance_present(self):
        result = self._get_result()
        assert result is not None
        assert "provenance:" in result

    def test_hint_present(self):
        result = self._get_result()
        assert result is not None
        assert "hint to assistant:" in result
        assert "cite drawer_id" in result


# ===================================================================
# TestProvenance
# ===================================================================


class TestProvenance:
    """Provenance line formatting."""

    def test_tool_name_and_args(self):
        tool_call = MCPCall(
            tool="mempalace_search",
            args={"query": "alice", "wing": "wing_alice", "limit": 3},
        )
        prov = _format_provenance(tool_call, latency_ms=287)
        assert "mempalace_search(" in prov
        assert 'query="alice"' in prov
        assert 'wing="wing_alice"' in prov
        assert "limit=3" in prov

    def test_latency_shown(self):
        tool_call = MCPCall(tool="mempalace_search", args={"query": "x"})
        prov = _format_provenance(tool_call, latency_ms=42)
        assert "latency=42ms" in prov

    def test_args_sorted(self):
        """Args should be sorted alphabetically for deterministic output."""
        tool_call = MCPCall(
            tool="mempalace_search",
            args={"wing": "w", "query": "q", "limit": 5},
        )
        prov = _format_provenance(tool_call, latency_ms=10)
        # limit < query < wing in alphabetical order.
        limit_pos = prov.index("limit=")
        query_pos = prov.index("query=")
        wing_pos = prov.index("wing=")
        assert limit_pos < query_pos < wing_pos


# ===================================================================
# TestTriggerLine
# ===================================================================


class TestTriggerLine:
    """Trigger line formatting from SignalSet."""

    def test_entity_signal(self):
        signals = _make_signals(
            entity=[Signal(kind="entity", name="Alice", score=3, wing="wing_alice")],
            total_score=3,
        )
        tool_call = MCPCall(tool="mempalace_search", args={})
        line = _format_trigger_line(signals, tool_call)
        assert "entity=Alice" in line

    def test_temporal_signal(self):
        signals = _make_signals(
            temporal=[Signal(kind="temporal", name="last time", score=2, phrase="last time")],
            total_score=2,
        )
        tool_call = MCPCall(tool="mempalace_search", args={})
        line = _format_trigger_line(signals, tool_call)
        assert "temporal='last time'" in line

    def test_multiple_signals_comma_separated(self):
        signals = _make_signals(
            entity=[Signal(kind="entity", name="Alice", score=3)],
            temporal=[Signal(kind="temporal", name="yesterday", score=2, phrase="yesterday")],
            resumption=True,
            total_score=9,
        )
        tool_call = MCPCall(tool="mempalace_search", args={})
        line = _format_trigger_line(signals, tool_call)
        assert "entity=Alice" in line
        assert "temporal='yesterday'" in line
        assert "resumption" in line
        # Verify they're comma-separated.
        assert ", " in line

    def test_explicit_signal(self):
        signals = _make_signals(explicit=True, total_score=5)
        tool_call = MCPCall(tool="mempalace_search", args={})
        line = _format_trigger_line(signals, tool_call)
        assert "explicit" in line

    def test_score_and_tool(self):
        signals = _make_signals(total_score=6)
        tool_call = MCPCall(tool="mempalace_kg_query", args={})
        line = _format_trigger_line(signals, tool_call)
        assert "score=6" in line
        assert "tool=mempalace_kg_query" in line

    def test_empty_signals_shows_unknown(self):
        signals = _make_signals(total_score=0)
        summary = _signal_summary(signals)
        assert summary == "unknown"


# ===================================================================
# TestTruncate (unit-level)
# ===================================================================


class TestTruncate:
    """_truncate helper."""

    def test_short_text_unchanged(self):
        assert _truncate("hello", 100) == "hello"

    def test_long_text_truncated_with_ellipsis(self):
        result = _truncate("A" * 300, 200)
        assert result == "A" * 200 + "..."

    def test_newlines_collapsed(self):
        result = _truncate("line1\nline2\nline3", 100)
        assert "\n" not in result
        assert "line1 line2 line3" == result


# ===================================================================
# TestUnknownTool
# ===================================================================


class TestUnknownTool:
    """Unknown tool names should return None."""

    def test_unknown_tool_returns_none(self):
        tool_call = MCPCall(tool="mempalace_status", args={})
        result = format_injection(tool_call, {}, _make_signals(), latency_ms=50)
        assert result is None
