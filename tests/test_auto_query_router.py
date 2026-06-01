"""Tests for mempalace.auto_query.router — tool routing logic.

Covers each row of the tool-selection table (spec section 2), threshold
behaviour for every mode, priority ordering, entity selection, and edge
cases.
"""

from mempalace.auto_query import SessionState, Signal, SignalSet
from mempalace.auto_query.router import THRESHOLDS, pick_tool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session(turn: int = 1) -> SessionState:
    return SessionState(turn_index=turn, queried_entities=set(), session_id="test-session")


def _empty_signals(score: int = 0, query_text: str = "", project_wing: str = "") -> SignalSet:
    return SignalSet(
        entity=[],
        temporal=[],
        resumption=False,
        explicit=False,
        total_score=score,
        project_wing=project_wing,
        query_text=query_text,
    )


def _entity_signal(name: str = "Alice", score: int = 3, wing: str = "") -> Signal:
    return Signal(kind="entity", name=name, score=score, wing=wing)


def _temporal_signal(phrase: str = "last time", score: int = 2) -> Signal:
    return Signal(kind="temporal", name=phrase, score=score, phrase=phrase)


# ---------------------------------------------------------------------------
# TestThresholds
# ---------------------------------------------------------------------------


class TestThresholds:
    """Each mode's threshold gate."""

    def test_off_never_fires(self):
        signals = _empty_signals(score=100)
        signals.resumption = True
        assert pick_tool(signals, "off", _session()) is None

    def test_dry_run_fires_at_4(self):
        signals = _empty_signals(score=4)
        signals.resumption = True
        result = pick_tool(signals, "dry-run", _session())
        assert result is not None

    def test_dry_run_skips_below_4(self):
        signals = _empty_signals(score=3)
        signals.resumption = True
        assert pick_tool(signals, "dry-run", _session()) is None

    def test_conservative_fires_at_6(self):
        signals = _empty_signals(score=6)
        signals.resumption = True
        result = pick_tool(signals, "conservative", _session())
        assert result is not None

    def test_conservative_skips_below_6(self):
        signals = _empty_signals(score=5)
        signals.resumption = True
        assert pick_tool(signals, "conservative", _session()) is None

    def test_balanced_fires_at_4(self):
        signals = _empty_signals(score=4)
        signals.resumption = True
        result = pick_tool(signals, "balanced", _session())
        assert result is not None

    def test_balanced_skips_below_4(self):
        signals = _empty_signals(score=3)
        signals.resumption = True
        assert pick_tool(signals, "balanced", _session()) is None

    def test_aggressive_fires_at_2(self):
        signals = _empty_signals(score=2)
        signals.resumption = True
        result = pick_tool(signals, "aggressive", _session())
        assert result is not None

    def test_aggressive_skips_below_2(self):
        signals = _empty_signals(score=1)
        signals.resumption = True
        assert pick_tool(signals, "aggressive", _session()) is None

    def test_unknown_mode_never_fires(self):
        signals = _empty_signals(score=100)
        signals.resumption = True
        assert pick_tool(signals, "nonexistent-mode", _session()) is None

    def test_threshold_exact_boundary(self):
        """Score exactly at threshold fires (>=, not >)."""
        for mode, threshold in THRESHOLDS.items():
            if threshold == float("inf"):
                continue
            signals = _empty_signals(score=int(threshold))
            signals.resumption = True
            result = pick_tool(signals, mode, _session())
            assert result is not None, f"mode={mode} should fire at score={threshold}"

    def test_threshold_one_below(self):
        """Score one below threshold does not fire."""
        for mode, threshold in THRESHOLDS.items():
            if threshold == float("inf"):
                continue
            signals = _empty_signals(score=int(threshold) - 1)
            signals.resumption = True
            result = pick_tool(signals, mode, _session())
            assert result is None, f"mode={mode} should skip at score={int(threshold) - 1}"


# ---------------------------------------------------------------------------
# TestToolSelection
# ---------------------------------------------------------------------------


class TestToolSelection:
    """One test per priority path in the routing table."""

    def test_resumption_routes_to_diary_read(self):
        signals = _empty_signals(score=6, project_wing="wing_familiar")
        signals.resumption = True
        result = pick_tool(signals, "balanced", _session())

        assert result is not None
        assert result.tool == "mempalace_diary_read"
        assert result.args["agent_name"] == "claude-code"
        assert result.args["wing"] == "wing_familiar"
        assert result.args["last_n"] == 3

    def test_explicit_hint_routes_to_search(self):
        signals = _empty_signals(score=5, query_text="remind me about the reranker?")
        signals.explicit = True
        result = pick_tool(signals, "balanced", _session())

        assert result is not None
        assert result.tool == "mempalace_search"
        assert result.args["query"] == "remind me about the reranker?"
        assert result.args["limit"] == 10

    def test_entity_and_temporal_routes_to_kg_query(self):
        signals = _empty_signals(score=5)
        signals.entity = [_entity_signal("Alice", score=3, wing="wing_alice")]
        signals.temporal = [_temporal_signal("last time", score=2)]
        result = pick_tool(signals, "balanced", _session())

        assert result is not None
        assert result.tool == "mempalace_kg_query"
        assert result.args["entity"] == "Alice"
        assert result.args["direction"] == "both"

    def test_entity_only_with_wing_routes_to_search_with_wing(self):
        signals = _empty_signals(score=4)
        signals.entity = [_entity_signal("Alice", score=3, wing="wing_alice")]
        result = pick_tool(signals, "balanced", _session())

        assert result is not None
        assert result.tool == "mempalace_search"
        assert result.args["query"] == "Alice"
        assert result.args["wing"] == "wing_alice"
        assert result.args["limit"] == 5

    def test_entity_only_without_wing_routes_to_search_no_wing(self):
        signals = _empty_signals(score=4)
        signals.entity = [_entity_signal("UnknownThing", score=3, wing="")]
        result = pick_tool(signals, "balanced", _session())

        assert result is not None
        assert result.tool == "mempalace_search"
        assert result.args["query"] == "UnknownThing"
        assert "wing" not in result.args
        assert result.args["limit"] == 5

    def test_temporal_only_routes_to_search_with_project_wing(self):
        signals = _empty_signals(
            score=4,
            query_text="last time we debugged the latency issue",
            project_wing="wing_familiar",
        )
        signals.temporal = [_temporal_signal("last time")]
        result = pick_tool(signals, "balanced", _session())

        assert result is not None
        assert result.tool == "mempalace_search"
        assert result.args["query"] == "last time we debugged the latency issue"
        assert result.args["wing"] == "wing_familiar"
        assert result.args["limit"] == 5

    def test_temporal_only_no_project_wing(self):
        signals = _empty_signals(score=4, query_text="last time we looked at this")
        signals.temporal = [_temporal_signal("last time")]
        result = pick_tool(signals, "balanced", _session())

        assert result is not None
        assert result.tool == "mempalace_search"
        assert "wing" not in result.args

    def test_no_signals_above_threshold_returns_none(self):
        """Score >= threshold but no signal flags set -> None."""
        signals = _empty_signals(score=10)
        result = pick_tool(signals, "balanced", _session())
        assert result is None


# ---------------------------------------------------------------------------
# TestPriority
# ---------------------------------------------------------------------------


class TestPriority:
    """When multiple signal types are present, highest priority wins."""

    def test_resumption_overrides_explicit(self):
        signals = _empty_signals(score=10, query_text="remind me about X?")
        signals.resumption = True
        signals.explicit = True
        result = pick_tool(signals, "balanced", _session())

        assert result is not None
        assert result.tool == "mempalace_diary_read"

    def test_resumption_overrides_entity_temporal(self):
        signals = _empty_signals(score=10, project_wing="wing_test")
        signals.resumption = True
        signals.entity = [_entity_signal("Alice", score=3)]
        signals.temporal = [_temporal_signal("last time")]
        result = pick_tool(signals, "balanced", _session())

        assert result is not None
        assert result.tool == "mempalace_diary_read"

    def test_explicit_overrides_entity_temporal(self):
        signals = _empty_signals(score=10, query_text="remind me about Alice?")
        signals.explicit = True
        signals.entity = [_entity_signal("Alice", score=3)]
        signals.temporal = [_temporal_signal("last time")]
        result = pick_tool(signals, "balanced", _session())

        assert result is not None
        assert result.tool == "mempalace_search"
        assert result.args["limit"] == 10  # explicit uses limit=10

    def test_entity_temporal_overrides_entity_only(self):
        """Entity + temporal -> kg_query, not search."""
        signals = _empty_signals(score=6)
        signals.entity = [_entity_signal("Alice", score=3, wing="wing_alice")]
        signals.temporal = [_temporal_signal("yesterday")]
        result = pick_tool(signals, "balanced", _session())

        assert result is not None
        assert result.tool == "mempalace_kg_query"

    def test_entity_only_overrides_temporal_only(self):
        """Entity without temporal -> search by entity, not by temporal query text."""
        signals = _empty_signals(score=5, query_text="last time we talked about Alice")
        signals.entity = [_entity_signal("Alice", score=3)]
        # temporal list is empty -- entity-only path
        result = pick_tool(signals, "balanced", _session())

        assert result is not None
        assert result.tool == "mempalace_search"
        assert result.args["query"] == "Alice"


# ---------------------------------------------------------------------------
# TestEntitySelection
# ---------------------------------------------------------------------------


class TestEntitySelection:
    """When multiple entities exist, picks the highest-scored one."""

    def test_picks_highest_score(self):
        signals = _empty_signals(score=6)
        signals.entity = [
            _entity_signal("Bob", score=2),
            _entity_signal("Alice", score=3),
            _entity_signal("Carol", score=1),
        ]
        result = pick_tool(signals, "balanced", _session())

        assert result is not None
        assert result.args["query"] == "Alice"

    def test_tied_score_picks_first(self):
        """When scores tie, max() returns the first encountered (stable)."""
        signals = _empty_signals(score=6)
        signals.entity = [
            _entity_signal("Alpha", score=3),
            _entity_signal("Beta", score=3),
        ]
        result = pick_tool(signals, "balanced", _session())

        assert result is not None
        assert result.args["query"] == "Alpha"

    def test_entity_temporal_picks_highest_for_kg(self):
        signals = _empty_signals(score=6)
        signals.entity = [
            _entity_signal("Low", score=1),
            _entity_signal("High", score=5),
        ]
        signals.temporal = [_temporal_signal("yesterday")]
        result = pick_tool(signals, "balanced", _session())

        assert result is not None
        assert result.tool == "mempalace_kg_query"
        assert result.args["entity"] == "High"


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Boundary conditions and unusual inputs."""

    def test_empty_signal_set_returns_none(self):
        signals = _empty_signals(score=0)
        assert pick_tool(signals, "balanced", _session()) is None

    def test_long_query_text_truncated(self):
        long_text = "a" * 500
        signals = _empty_signals(score=5, query_text=long_text)
        signals.explicit = True
        result = pick_tool(signals, "balanced", _session())

        assert result is not None
        assert len(result.args["query"]) == 200

    def test_temporal_long_query_truncated(self):
        long_text = "last time we " + "x" * 500
        signals = _empty_signals(score=4, query_text=long_text)
        signals.temporal = [_temporal_signal("last time")]
        result = pick_tool(signals, "balanced", _session())

        assert result is not None
        assert len(result.args["query"]) == 200

    def test_whitespace_query_stripped(self):
        signals = _empty_signals(score=5, query_text="   remind me about X?   ")
        signals.explicit = True
        result = pick_tool(signals, "balanced", _session())

        assert result is not None
        assert result.args["query"] == "remind me about X?"

    def test_empty_query_text_explicit(self):
        signals = _empty_signals(score=5, query_text="")
        signals.explicit = True
        result = pick_tool(signals, "balanced", _session())

        assert result is not None
        assert result.args["query"] == ""

    def test_resumption_with_empty_wing(self):
        signals = _empty_signals(score=6, project_wing="")
        signals.resumption = True
        result = pick_tool(signals, "balanced", _session())

        assert result is not None
        assert result.tool == "mempalace_diary_read"
        assert result.args["wing"] == ""

    def test_entity_wing_used_for_search_scoping(self):
        """Entity with a matched wing scopes the search to that wing."""
        signals = _empty_signals(score=4)
        signals.entity = [_entity_signal("Realmwatch", score=3, wing="wing_realmwatch")]
        result = pick_tool(signals, "balanced", _session())

        assert result is not None
        assert result.args["wing"] == "wing_realmwatch"

    def test_mcp_call_is_dataclass(self):
        """MCPCall returned is a proper dataclass instance."""
        signals = _empty_signals(score=4)
        signals.resumption = True
        result = pick_tool(signals, "balanced", _session())

        assert result is not None
        assert hasattr(result, "tool")
        assert hasattr(result, "args")
        assert isinstance(result.args, dict)
