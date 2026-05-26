"""Stopword-filter tests for the cheap NER and AGE graph-expand paths.

Regression for a daemon-wide /search wedge observed 2026-05-26: when a
user's question started with a capitalized wh-word ("What does X mean?",
"How many drawers..."), ``_ner_from_query`` extracted the wh-word as a
candidate entity and ``_graph_expand_from_entities`` fed it into a Cypher
query of the form ``WHERE a.name = 'What' OR a.name =~ '(?i).*What.*'``.
The regex branch is a full sequential scan over every Entity vertex —
six concurrent scans wedged Postgres for 75+ minutes and pinned four
backends at 99.9% CPU.

The fix adds ``_QUERY_NER_STOPWORDS`` and filters both at the NER
extraction site and defensively at the graph-expand site.
"""

from mempalace.searcher import _QUERY_NER_STOPWORDS, _ner_from_query


class TestQueryNerStopwordFilter:
    def test_wh_word_filtered_from_ner_output(self):
        """Wh-questions don't leak their interrogative as an entity."""
        assert _ner_from_query("What is MemPalace?") == ["MemPalace"]
        assert _ner_from_query("How does Postgres work?") == ["Postgres"]
        assert _ner_from_query("Where does Familiar live?") == ["Familiar"]

    def test_wh_word_phrase_also_filtered(self):
        """_ENTITY_REGEX greedily captures multi-word capitalized phrases,
        so "Which Drawer" arrives as a single token. The filter rejects
        the whole phrase rather than try to recover the tail — vector
        and BM25 still cover the trailing words via their own paths."""
        assert _ner_from_query("Which Drawer holds it?") == []
        assert _ner_from_query("What MemPalace version is current?") == []

    def test_only_wh_words_returns_empty(self):
        """A query that's purely interrogatives + lowercase verbs has no
        entity candidates — better than returning ``["What"]`` and DoSing
        the graph."""
        assert _ner_from_query("What does it mean?") == []
        assert _ner_from_query("How are you?") == []

    def test_common_capitalized_starters_filtered(self):
        """Sentence-starting non-entities like "The", "This", "They"
        also pass the regex and need filtering — same phrase-rejection
        rule as wh-words. ``BM25`` and vector retrieval still cover the
        downstream tokens, so the lost-trailing-word cost is bounded."""
        assert _ner_from_query("The Drawer is here.") == []
        assert _ner_from_query("This works fine.") == []
        assert _ner_from_query("They are running.") == []

    def test_non_stopword_entity_preserved(self):
        """Legitimate capitalized entities pass through unchanged."""
        out = _ner_from_query("MemPalace Daemon runs on Familiar")
        assert "MemPalace Daemon" in out or "MemPalace" in out
        assert "Familiar" in out

    def test_stopword_set_includes_all_wh_words(self):
        """Lock the wh-word coverage so a future edit can't silently
        drop one and reintroduce the DoS for that interrogative."""
        for w in ("what", "which", "where", "when", "why", "who", "how"):
            assert w in _QUERY_NER_STOPWORDS, f"{w!r} missing from stopwords"

    def test_stopword_match_is_case_insensitive(self):
        """NER produces capitalized tokens; the filter normalizes."""
        assert _ner_from_query("WHAT happens next?") == []
        assert _ner_from_query("How HOW hOw") == []

    def test_known_entities_substring_match_still_works(self):
        """The known-entities lowercase substring match is independent
        of the stopword filter and must keep working."""
        out = _ner_from_query(
            "the palace_daemon is running",
            known_entities={"palace_daemon"},
        )
        assert "palace_daemon" in out


class TestGraphExpandCypherSafety:
    """Static checks that the AGE expand template stays in the safe shape.

    The original DoS wedge was caused by an `a.name =~ '(?i).*X.*'` regex
    branch that seq-scanned every Entity vertex. The follow-up hotfix
    removed that branch and added a 3s `statement_timeout` guard. These
    tests fail loudly if either guard is regressed.
    """

    def test_expand_cypher_has_no_regex_branch(self):
        """The `=~` operator must not appear in the AGE expand template."""
        import inspect

        from mempalace.searcher import _graph_expand_from_entities

        src = inspect.getsource(_graph_expand_from_entities)
        # Strip Python comment lines so explanatory text mentioning the
        # forbidden operator doesn't trip the guard.
        code_only = "\n".join(
            line for line in src.splitlines() if not line.lstrip().startswith("#")
        )
        assert "=~" not in code_only, (
            "AGE Cypher regex (`=~`) reintroduced in _graph_expand_from_entities — "
            "this caused production-scale seq-scan wedges (see PR following #227)."
        )

    def test_expand_cypher_sets_statement_timeout(self):
        """Per-statement timeout must be set before any Cypher executes."""
        import inspect

        from mempalace.searcher import _graph_expand_from_entities

        src = inspect.getsource(_graph_expand_from_entities)
        assert "statement_timeout" in src, (
            "_graph_expand_from_entities must SET LOCAL statement_timeout "
            "so misfires self-cancel rather than wedging the daemon."
        )
