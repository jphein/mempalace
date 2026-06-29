"""Tests for auto-query signal extraction.

Covers the four signal classes (entity, temporal, resumption, explicit),
compounding logic, edge cases, and deduplication.
"""

from mempalace.auto_query import SessionState
from mempalace.auto_query.signals import (
    extract_signals,
    _extract_entity_signals,
    _extract_temporal_signals,
    _check_resumption,
    _check_explicit,
)


def _session(turn=1, queried=None, sid="test-session"):
    """Helper to create a SessionState."""
    return SessionState(
        turn_index=turn,
        queried_entities=queried or set(),
        session_id=sid,
    )


# ---------------------------------------------------------------------------
# Entity signals
# ---------------------------------------------------------------------------


class TestEntitySignals:
    """Tests for _extract_entity_signals and entity scoring."""

    def test_extracts_capitalized_name(self):
        signals = _extract_entity_signals(
            "How is the Alice project going?",
            _session(),
            set(),
            None,
        )
        names = [s.name for s in signals]
        assert "Alice" in names

    def test_extracts_multi_word_entity(self):
        signals = _extract_entity_signals(
            "What happened with MemPalace Server?",
            _session(),
            set(),
            None,
        )
        # Should capture "MemPalace Server" or at least "MemPalace"
        names = [s.name for s in signals]
        assert any("MemPalace" in n for n in names)

    def test_filters_stopwords(self):
        signals = _extract_entity_signals(
            "The time complexity is O(n)",
            _session(),
            set(),
            None,
        )
        names = [s.name for s in signals]
        assert "The" not in names
        assert "What" not in names

    def test_filters_all_stopwords(self):
        """Every word in _STOPWORDS should be filtered."""
        text = "What This That These Those When Where Which Who How Why"
        signals = _extract_entity_signals(text, _session(), set(), None)
        assert len(signals) == 0

    def test_wing_match_scores_3(self):
        signals = _extract_entity_signals(
            "How is MemPalace doing?",
            _session(),
            {"wing_mempalace"},
            None,
        )
        matched = [s for s in signals if s.name == "MemPalace"]
        assert len(matched) == 1
        assert matched[0].score == 3
        assert matched[0].wing == "wing_mempalace"

    def test_known_entity_scores_2(self):
        signals = _extract_entity_signals(
            "Tell me about Alice",
            _session(),
            set(),
            {"Alice"},
        )
        matched = [s for s in signals if s.name == "Alice"]
        assert len(matched) == 1
        assert matched[0].score == 2

    def test_unknown_entity_scores_1(self):
        signals = _extract_entity_signals(
            "Tell me about Xylophone",
            _session(),
            set(),
            None,
        )
        matched = [s for s in signals if s.name == "Xylophone"]
        assert len(matched) == 1
        # Bumped 0 -> 1: a bare capitalized name is a weak recall signal.
        assert matched[0].score == 1

    def test_dedup_queried_entities(self):
        """Entities already in session_state.queried_entities are skipped."""
        signals = _extract_entity_signals(
            "Tell me about Alice and Bob",
            _session(queried={"Alice"}),
            set(),
            None,
        )
        names = [s.name for s in signals]
        assert "Alice" not in names
        assert "Bob" in names

    def test_max_5_entity_signals(self):
        """At most 5 entity signals are returned."""
        text = "Alpha Beta Gamma Delta Epsilon Zeta Eta Theta Iota Kappa"
        signals = _extract_entity_signals(text, _session(), set(), None)
        assert len(signals) <= 5

    def test_lowercase_substring_match(self):
        """Known entities are matched via lowercase substring (searcher.py pattern)."""
        signals = _extract_entity_signals(
            "check the palace_daemon logs",
            _session(),
            set(),
            {"palace_daemon"},
        )
        names = [s.name for s in signals]
        assert "palace_daemon" in names

    def test_short_tokens_filtered(self):
        """Tokens under 3 chars are skipped."""
        signals = _extract_entity_signals(
            "Go to AI",
            _session(),
            set(),
            None,
        )
        names = [s.name for s in signals]
        assert "Go" not in names
        assert "AI" not in names

    def test_wing_slug_generation_with_spaces(self):
        """Multi-word entity generates correct wing slug."""
        signals = _extract_entity_signals(
            "What about Realm Watch?",
            _session(),
            {"wing_realm_watch"},
            None,
        )
        matched = [s for s in signals if "Realm" in s.name]
        assert any(s.score == 3 for s in matched)

    def test_entity_kind_is_entity(self):
        signals = _extract_entity_signals(
            "Tell me about Alice",
            _session(),
            set(),
            None,
        )
        for s in signals:
            assert s.kind == "entity"


# ---------------------------------------------------------------------------
# Temporal signals
# ---------------------------------------------------------------------------


class TestTemporalSignals:
    """Tests for _extract_temporal_signals."""

    def test_last_time(self):
        signals = _extract_temporal_signals("last time we discussed this")
        assert len(signals) >= 1
        assert any("last time" in s.phrase.lower() for s in signals)

    def test_yesterday(self):
        signals = _extract_temporal_signals("yesterday we fixed the bug")
        assert len(signals) >= 1
        assert any("yesterday" in s.phrase.lower() for s in signals)

    def test_last_session(self):
        signals = _extract_temporal_signals("in the last session we talked about X")
        assert len(signals) >= 1
        assert any("last session" in s.phrase.lower() for s in signals)

    def test_last_week(self):
        signals = _extract_temporal_signals("last week we deployed the fix")
        assert len(signals) >= 1

    def test_when_did(self):
        signals = _extract_temporal_signals("when did we add that feature?")
        assert len(signals) >= 1

    def test_previously(self):
        signals = _extract_temporal_signals("previously we had a different approach")
        assert len(signals) >= 1

    def test_before_we(self):
        signals = _extract_temporal_signals("before we changed the config")
        assert len(signals) >= 1

    def test_that_time_we(self):
        signals = _extract_temporal_signals("that time we broke production")
        assert len(signals) >= 1

    def test_earlier_today(self):
        signals = _extract_temporal_signals("earlier today we pushed a fix")
        assert len(signals) >= 1

    # Broadened patterns (spec change 3) ------------------------------------

    def test_recently(self):
        signals = _extract_temporal_signals("we changed that approach recently")
        assert any("recently" in s.phrase.lower() for s in signals)

    def test_a_while_ago(self):
        signals = _extract_temporal_signals("we set this up a while ago")
        assert any("while ago" in s.phrase.lower() for s in signals)

    def test_few_days_ago(self):
        signals = _extract_temporal_signals("we deployed it a few days ago")
        assert any("few days ago" in s.phrase.lower() for s in signals)

    def test_few_days_ago_without_a(self):
        signals = _extract_temporal_signals("merged that fix few days ago")
        assert any("few days ago" in s.phrase.lower() for s in signals)

    def test_last_sprint(self):
        signals = _extract_temporal_signals("we planned this last sprint")
        assert any("last sprint" in s.phrase.lower() for s in signals)

    def test_back_when(self):
        signals = _extract_temporal_signals("back when we used the old schema")
        assert any("back when" in s.phrase.lower() for s in signals)

    def test_used_to(self):
        signals = _extract_temporal_signals("we used to do it differently")
        assert any("used to" in s.phrase.lower() for s in signals)

    def test_no_match_time_complexity(self):
        """'the time complexity' must NOT match temporal signals."""
        signals = _extract_temporal_signals("the time complexity is O(n)")
        assert len(signals) == 0

    def test_no_match_last_commit(self):
        """'last commit' alone does not match — needs 'last time/week/session/...'."""
        signals = _extract_temporal_signals("the last commit message")
        assert len(signals) == 0

    def test_no_match_last_element(self):
        """'last element' does not match temporal patterns."""
        signals = _extract_temporal_signals("get the last element of the array")
        assert len(signals) == 0

    def test_max_3_temporal_signals(self):
        """At most 3 temporal signals are returned."""
        text = (
            "last time we talked, and yesterday we fixed it, "
            "and last week we deployed, and previously we had "
            "a different approach, and before we changed config"
        )
        signals = _extract_temporal_signals(text)
        assert len(signals) <= 3

    def test_temporal_score_is_2(self):
        signals = _extract_temporal_signals("last time we discussed this")
        for s in signals:
            assert s.score == 2

    def test_temporal_kind_is_temporal(self):
        signals = _extract_temporal_signals("yesterday we fixed it")
        for s in signals:
            assert s.kind == "temporal"

    def test_dedup_repeated_phrases(self):
        """Duplicate temporal phrases are not double-counted."""
        signals = _extract_temporal_signals("last time we did X and last time we did Y")
        phrases = [s.phrase.lower() for s in signals]
        assert phrases.count("last time") <= 1

    def test_case_insensitive(self):
        """Temporal patterns match regardless of case."""
        signals = _extract_temporal_signals("LAST TIME we discussed this")
        assert len(signals) >= 1


# ---------------------------------------------------------------------------
# Resumption
# ---------------------------------------------------------------------------


class TestResumption:
    """Tests for _check_resumption."""

    def test_positive_all_conditions(self):
        """Turn 1, known wing, has recent drawers -> True."""
        result = _check_resumption(
            _session(turn=1),
            "wing_myproject",
            {"wing_myproject"},
            has_recent_drawers=True,
        )
        assert result is True

    def test_negative_not_first_turn(self):
        """Turn 2 -> False."""
        result = _check_resumption(
            _session(turn=2),
            "wing_myproject",
            {"wing_myproject"},
            has_recent_drawers=True,
        )
        assert result is False

    def test_negative_unknown_wing(self):
        """Turn 1, unknown wing -> False."""
        result = _check_resumption(
            _session(turn=1),
            "wing_unknown",
            {"wing_myproject"},
            has_recent_drawers=True,
        )
        assert result is False

    def test_negative_no_recent_drawers(self):
        """Turn 1, known wing, no recent drawers -> False."""
        result = _check_resumption(
            _session(turn=1),
            "wing_myproject",
            {"wing_myproject"},
            has_recent_drawers=False,
        )
        assert result is False

    def test_negative_turn_0(self):
        """Turn 0 (no turn yet) -> False."""
        result = _check_resumption(
            _session(turn=0),
            "wing_myproject",
            {"wing_myproject"},
            has_recent_drawers=True,
        )
        assert result is False

    def test_negative_empty_wings(self):
        """Empty known_wings set -> False."""
        result = _check_resumption(
            _session(turn=1),
            "wing_myproject",
            set(),
            has_recent_drawers=True,
        )
        assert result is False


# ---------------------------------------------------------------------------
# Explicit hints
# ---------------------------------------------------------------------------


class TestExplicit:
    """Tests for _check_explicit."""

    def test_positive_remind_me(self):
        assert _check_explicit("remind me what we did with the metadata bug?")

    def test_positive_do_we_have(self):
        assert _check_explicit("do we have any notes on that?")

    def test_positive_did_we_ever(self):
        assert _check_explicit("did we ever resolve the auth issue?")

    def test_positive_what_did_we(self):
        assert _check_explicit("what did we decide about the schema?")

    def test_positive_history_of(self):
        assert _check_explicit("what's the history of this feature?")

    def test_positive_have_we_ever(self):
        assert _check_explicit("have we ever dealt with this before?")

    def test_negative_no_question_mark(self):
        """Explicit hint without question mark -> False."""
        assert not _check_explicit("remind me what we did")

    def test_negative_no_explicit_keyword(self):
        """Question mark without explicit keyword -> False."""
        assert not _check_explicit("what's the time?")

    def test_negative_plain_question(self):
        """Regular question without memory-request hint -> False."""
        assert not _check_explicit("how do I fix this error?")

    def test_case_insensitive(self):
        assert _check_explicit("REMIND ME what we discussed?")


# ---------------------------------------------------------------------------
# Compounding & total score
# ---------------------------------------------------------------------------


class TestCompounding:
    """Tests for compound signal scoring."""

    def test_entity_temporal_compound_bonus(self):
        """Entity + temporal signals together get +1 compound bonus."""
        result = extract_signals(
            "last time Alice mentioned the bug",
            _session(),
            "wing_test",
            set(),
            {"Alice"},
        )
        # Alice: score 2 (known entity), last time: score 2, compound: +1
        assert result.total_score == 5

    def test_total_score_entity_only(self):
        """Entity only — no compound bonus."""
        result = extract_signals(
            "Tell me about Alice",
            _session(),
            "wing_test",
            set(),
            {"Alice"},
        )
        # "Alice": score 2 (known entity); "Tell": score 1 (unknown
        # capitalized word, post score-0->1 bump). No temporal/resumption/
        # explicit, no compound bonus. 2 + 1 = 3.
        assert result.total_score == 3

    def test_total_score_temporal_only(self):
        """Temporal only — no compound bonus."""
        result = extract_signals(
            "last time we discussed the thing",
            _session(),
            "wing_test",
            set(),
        )
        # temporal: score 2, no compound
        assert result.total_score == 2

    def test_total_score_resumption(self):
        """Resumption adds +4."""
        result = extract_signals(
            "hello",
            _session(turn=1),
            "wing_myproject",
            {"wing_myproject"},
            has_recent_drawers=True,
        )
        assert result.total_score >= 4

    def test_total_score_explicit(self):
        """Explicit adds +5."""
        result = extract_signals(
            "remind me what we did with the schema?",
            _session(),
            "wing_test",
            set(),
        )
        assert result.total_score >= 5

    def test_all_signals_combined(self):
        """All four signal types firing at once."""
        result = extract_signals(
            "remind me what did we do last time with Alice?",
            _session(turn=1),
            "wing_myproject",
            {"wing_myproject"},
            {"Alice"},
            has_recent_drawers=True,
        )
        # entity(Alice=2) + temporal(last time=2) + resumption(4) +
        # explicit(5) + compound(1) = 14
        assert result.total_score >= 14
        assert result.resumption is True
        assert result.explicit is True
        assert len(result.entity) >= 1
        assert len(result.temporal) >= 1


# ---------------------------------------------------------------------------
# Depth signal (periodic refresh)
# ---------------------------------------------------------------------------


class TestDepthSignal:
    """Tests for the periodic depth-refresh signal — fires every 15 turns."""

    def _result(self, turn):
        # Content-free prompt so only the turn count can set depth_fire.
        return extract_signals("ok", _session(turn=turn), "wing_test", set())

    def test_fires_on_turn_15(self):
        assert self._result(15).depth_fire is True

    def test_fires_on_turn_30(self):
        assert self._result(30).depth_fire is True

    def test_fires_on_turn_45(self):
        assert self._result(45).depth_fire is True

    def test_no_fire_turn_1(self):
        assert self._result(1).depth_fire is False

    def test_no_fire_turn_14(self):
        assert self._result(14).depth_fire is False

    def test_no_fire_turn_16(self):
        assert self._result(16).depth_fire is False

    def test_no_fire_turn_0(self):
        """Turn 0 is the pre-session sentinel: 0 % 15 == 0 but the > 0 guard
        must keep depth_fire False."""
        assert self._result(0).depth_fire is False

    def test_depth_adds_4_to_score(self):
        """A depth turn with no other signal scores exactly 4 (balanced gate)."""
        result = self._result(15)
        assert result.total_score == 4

    def test_depth_default_false(self):
        """Non-depth turns leave depth_fire at its default False."""
        assert self._result(7).depth_fire is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Tests for edge cases and robustness."""

    def test_empty_string(self):
        result = extract_signals("", _session(), "", set())
        assert result.total_score == 0
        assert result.entity == []
        assert result.temporal == []
        assert result.resumption is False
        assert result.explicit is False

    def test_very_long_text(self):
        """10K chars should not hang or crash."""
        text = "Alice said hello. " * 600  # ~10800 chars
        result = extract_signals(
            text,
            _session(),
            "wing_test",
            set(),
            {"Alice"},
        )
        # Should complete without error, entity count capped at 5
        assert len(result.entity) <= 5
        assert isinstance(result.total_score, int)

    def test_unicode_text(self):
        """Unicode text should not crash."""
        result = extract_signals(
            "Café üöä 你好 Alice 🚀",
            _session(),
            "wing_test",
            set(),
        )
        names = [s.name for s in result.entity]
        assert "Alice" in names

    def test_signal_set_fields(self):
        """SignalSet has all expected fields populated."""
        result = extract_signals(
            "tell me about Alice",
            _session(),
            "wing_test",
            set(),
        )
        assert result.project_wing == "wing_test"
        assert result.query_text == "tell me about Alice"

    def test_newlines_in_text(self):
        """Multi-line text works correctly."""
        result = extract_signals(
            "first line\nlast time we discussed\nAlice was there",
            _session(),
            "wing_test",
            set(),
            {"Alice"},
        )
        assert len(result.entity) >= 1
        assert len(result.temporal) >= 1

    def test_no_known_wings_or_entities(self):
        """Works with empty known sets."""
        result = extract_signals(
            "Tell me about Alice",
            _session(),
            "",
            set(),
            set(),
        )
        names = [s.name for s in result.entity]
        assert "Alice" in names

    def test_session_state_preserved(self):
        """Session state is not mutated by extract_signals."""
        state = _session(queried={"Bob"})
        original_queried = state.queried_entities.copy()
        extract_signals(
            "Tell me about Alice and Bob",
            state,
            "wing_test",
            set(),
        )
        assert state.queried_entities == original_queried
