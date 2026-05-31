"""Tests for KG predicate normalization (kg_predicate_norm.py, issue #50).

Covers the three contamination classes from the issue:
  1. code tokens treated as predicates  → dropped (None)
  2. near-synonyms not collapsed         → canonicalized
  3. grammatical fragments / negation    → punctuation stripped, polarity
                                            denormalized to not_<base>

Plus folding edge cases and idempotency.

Run with::

    .venv/bin/python -m pytest tests/test_kg_predicate_norm.py -q
"""

import unittest

from mempalace.kg_predicate_norm import (
    CODE_TOKEN_BLOCKLIST,
    SYNONYM_MAP,
    normalize_predicate,
)


class TestClass1CodeTokens(unittest.TestCase):
    """Code-token predicates from the issue must be dropped (None)."""

    def test_issue_examples_dropped(self):
        for tok in (
            "appendchild",
            "createelement",
            "executemany",
            "setattribute",
            "getelementbyid",
        ):
            self.assertIsNone(normalize_predicate(tok), tok)

    def test_camelcase_form_dropped(self):
        # The extractor may emit either case; both fold to the blocklist form.
        for tok in (
            "appendChild",
            "createElement",
            "getElementById",
            "setAttribute",
            "executeMany",
        ):
            self.assertIsNone(normalize_predicate(tok), tok)

    def test_digit_identifier_heuristic_dropped(self):
        # single lowercase token with embedded digit → code noise
        for tok in ("utf8decode", "sha256hash", "base64encode"):
            self.assertIsNone(normalize_predicate(tok), tok)

    def test_legitimate_verb_with_no_digit_survives(self):
        # heuristic must not eat real single-word verbs
        for verb in ("uses", "owns", "contains", "created"):
            self.assertIsNotNone(normalize_predicate(verb), verb)

    def test_snake_phrase_survives_heuristic(self):
        # an underscore means it is a phrase, not a code identifier
        self.assertEqual(normalize_predicate("works_on"), "works_on")

    def test_negated_code_token_drops_whole_predicate(self):
        # "doesn't appendChild" should still drop, not become not_append_child
        self.assertIsNone(normalize_predicate("doesnt_appendchild"))


class TestClass2Synonyms(unittest.TestCase):
    """Near-synonyms collapse to a single canonical relation type."""

    def test_identity_family_collapses_to_is_a(self):
        for raw in (
            "is",
            "is_a",
            "is_an",
            "was_a",
            "is_an_instance_of",
            "is_a_kind_of",
            "is_a_type_of",
            "instance_of",
        ):
            self.assertEqual(normalize_predicate(raw), "is_a", raw)

    def test_part_of_family(self):
        for raw in ("is_part_of", "is_a_part_of", "belongs_to", "member_of"):
            self.assertEqual(normalize_predicate(raw), "part_of", raw)

    def test_reference_family(self):
        for raw in ("is_a_reference", "refers_to", "reference", "is_reference_to"):
            self.assertEqual(normalize_predicate(raw), "references", raw)

    def test_distinct_relations_not_merged(self):
        # part_of must NOT collapse into is_a — they are different edges
        self.assertNotEqual(normalize_predicate("part_of"), normalize_predicate("is_a"))
        self.assertEqual(normalize_predicate("part_of"), "part_of")
        self.assertEqual(normalize_predicate("created_by"), "created_by")

    def test_case_and_spacing_variation_collapse(self):
        # surface variants fold then hit the synonym map
        self.assertEqual(normalize_predicate("Is A"), "is_a")
        self.assertEqual(normalize_predicate("is-a"), "is_a")
        self.assertEqual(normalize_predicate("IS_AN_INSTANCE_OF"), "is_a")


class TestClass3Negation(unittest.TestCase):
    """Negation/punctuation fragments → stripped + polarity denormalized."""

    def test_issue_apostrophe_examples(self):
        # don't_adapt → not_adapt (base `adapt` not a synonym, passes through)
        self.assertEqual(normalize_predicate("don't_adapt"), "not_adapt")
        # aren't_merged: peel `arent_`, base `merged` now canonicalizes to
        # `adds` (issue #45 add-family synonym), then re-prefix → not_adds.
        # This is the documented peel-then-canonicalize design applied to the
        # expanded synonym map, not a leaf passthrough.
        self.assertEqual(normalize_predicate("aren't_merged"), "not_adds")
        self.assertEqual(normalize_predicate("'doesn't_appear'"), "not_appear")

    def test_apostrophe_stripped_from_endpoints(self):
        # leading/trailing quotes removed
        self.assertEqual(normalize_predicate("'uses'"), "uses")
        self.assertEqual(normalize_predicate('"contains"'), "contains")

    def test_negation_prefix_variants_unify(self):
        # both contraction and expanded form land on the same not_<base>
        self.assertEqual(normalize_predicate("doesnt_appear"), "not_appear")
        self.assertEqual(normalize_predicate("does_not_appear"), "not_appear")

    def test_negation_applied_after_synonym_collapse(self):
        # negation peels first, then the *base* is canonicalized, then we
        # re-prefix not_. "is not a part of" → base "a_part_of" → part_of
        # → not_part_of.
        self.assertEqual(normalize_predicate("is_not_a_part_of"), "not_part_of")
        # "isn't a" strips the "isnt_" prefix leaving base "a", which is now a
        # content-free stopword (issue #45 STOPWORD_BLOCKLIST). A negation of a
        # contentless word carries no relation, so the whole predicate drops to
        # None — the stopword check applies to the negation-stripped base too.
        self.assertIsNone(normalize_predicate("isnt_a"))

    def test_bare_negation_token_drops(self):
        self.assertIsNone(normalize_predicate("not_"))
        self.assertIsNone(normalize_predicate("doesnt_"))

    def test_positive_predicate_unaffected(self):
        self.assertEqual(normalize_predicate("adapts"), "adapts")


class TestFoldingEdgeCases(unittest.TestCase):
    def test_empty_and_whitespace(self):
        self.assertIsNone(normalize_predicate(""))
        self.assertIsNone(normalize_predicate("   "))

    def test_punctuation_only(self):
        self.assertIsNone(normalize_predicate("'''"))
        self.assertIsNone(normalize_predicate("___"))

    def test_non_string_input(self):
        self.assertIsNone(normalize_predicate(None))  # type: ignore[arg-type]
        self.assertIsNone(normalize_predicate(123))  # type: ignore[arg-type]

    def test_multi_underscore_collapsed(self):
        self.assertEqual(normalize_predicate("works___on"), "works_on")

    def test_leading_trailing_underscores_trimmed(self):
        self.assertEqual(normalize_predicate("_uses_"), "uses")


class TestIdempotency(unittest.TestCase):
    """normalize_predicate(normalize_predicate(x)) == normalize_predicate(x)."""

    def test_idempotent_over_samples(self):
        samples = [
            "is",
            "is_a",
            "appendChild",
            "don't_adapt",
            "is_an_instance_of",
            "uses",
            "part_of",
            "doesnt_appear",
            "references",
            "works on",
        ]
        for raw in samples:
            once = normalize_predicate(raw)
            if once is None:
                continue
            twice = normalize_predicate(once)
            self.assertEqual(once, twice, f"not idempotent: {raw!r}")

    def test_canonical_values_are_fixed_points(self):
        # every canonical target must normalize to itself
        for canonical in set(SYNONYM_MAP.values()):
            self.assertEqual(normalize_predicate(canonical), canonical)

    def test_blocklist_entries_all_drop(self):
        for tok in CODE_TOKEN_BLOCKLIST:
            self.assertIsNone(normalize_predicate(tok), tok)


class TestIssue45ShellAndStopwordDrops(unittest.TestCase):
    """Issue #45: shell commands and content-free function words drop.

    These accounted for a large slice of the production `other` bucket —
    single all-lowercase words with no digit, invisible to the camelCase /
    digit heuristics, so they had to be enumerated.
    """

    def test_shell_commands_dropped(self):
        for tok in ("grep", "cd", "ls", "echo", "diff", "find", "curl", "ssh"):
            self.assertIsNone(normalize_predicate(tok), tok)

    def test_stopwords_dropped(self):
        for tok in ("can", "will", "should", "does", "for", "on", "had", "the"):
            self.assertIsNone(normalize_predicate(tok), tok)

    def test_real_verbs_that_double_as_commands_survive_via_synonym(self):
        # `run` / `set` / `add` / `push` / `merge` / `make` double as verbs and
        # CLI subcommands. They are deliberately NOT in the shell blocklist;
        # the synonym map routes them to canonicals instead of dropping.
        self.assertEqual(normalize_predicate("run"), "runs")
        self.assertEqual(normalize_predicate("set"), "writes")
        self.assertEqual(normalize_predicate("add"), "adds")
        self.assertEqual(normalize_predicate("make"), "creates")
        self.assertEqual(normalize_predicate("merged"), "adds")

    def test_is_copula_routes_to_is_a_not_dropped(self):
        # `is` / `are` / `was` express a copular relation → is_a, NOT dropped.
        for tok in ("is", "are", "was"):
            self.assertEqual(normalize_predicate(tok), "is_a", tok)


class TestIssue45SynonymFamilies(unittest.TestCase):
    """Issue #45: high-frequency `other`-bucket paraphrases → canonicals.

    Each assertion is a real production predicate that previously scored below
    the 0.45 embedding threshold and fell to `other`; the deterministic
    synonym short-circuit now binds it exactly.
    """

    def test_read_family(self):
        for raw in ("get", "gets", "fetches", "loads"):
            self.assertEqual(normalize_predicate(raw), "reads", raw)

    def test_write_family(self):
        for raw in ("set", "sets", "is_set_to", "stores", "saves"):
            self.assertEqual(normalize_predicate(raw), "writes", raw)

    def test_modify_family(self):
        for raw in ("update", "updated", "changed", "fixes", "has_been_updated"):
            self.assertEqual(normalize_predicate(raw), "modifies", raw)

    def test_run_family(self):
        for raw in ("run", "executed", "started", "launches"):
            self.assertEqual(normalize_predicate(raw), "runs", raw)

    def test_send_family(self):
        for raw in ("sent", "emits", "fires", "publishes"):
            self.assertEqual(normalize_predicate(raw), "sends", raw)

    def test_provide_family(self):
        for raw in ("offers", "exposes", "supplies"):
            self.assertEqual(normalize_predicate(raw), "provides", raw)

    def test_location_family(self):
        for raw in ("is_in", "located_in", "stored_in", "found_in"):
            self.assertEqual(normalize_predicate(raw), "located_at", raw)

    def test_property_family(self):
        for raw in ("has_method", "has_feature", "has_version", "has_id"):
            self.assertEqual(normalize_predicate(raw), "has_property", raw)

    def test_describe_family(self):
        for raw in ("reports", "says", "states", "suggests", "asks"):
            self.assertEqual(normalize_predicate(raw), "describes", raw)

    def test_completion_family(self):
        for raw in ("passed", "closes", "finished", "resolved"):
            self.assertEqual(normalize_predicate(raw), "completed", raw)

    def test_all_new_synonym_targets_are_canonical_fixed_points(self):
        # Every synonym target must itself normalize to itself (no chains).
        for target in set(SYNONYM_MAP.values()):
            self.assertEqual(normalize_predicate(target), target, target)


if __name__ == "__main__":
    unittest.main()
