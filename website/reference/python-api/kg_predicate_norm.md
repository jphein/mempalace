# `mempalace.kg_predicate_norm`

Source: [`mempalace/kg_predicate_norm.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/kg_predicate_norm.py)

Predicate normalization for the AGE knowledge graph (issue #50).

The LLM triple extractor emits ~1000+ distinct ``relation_type`` strings.
Three classes of contamination bloat the predicate vocabulary far past the
underlying semantic relation count:

1. **Code tokens** treated as predicates (``appendchild``, ``createelement``,
   ``executemany``, ``setattribute``, ``getelementbyid``) — JS/Python API
   method names the extractor pulled out of source-code drawers. These should
   be dropped.
2. **Near-synonyms** not collapsed (``is`` / ``is_a`` / ``is_an_instance_of``;
   ``was_a`` / ``is_a_kind_of``) — canonicalized to one relation type.
3. **Grammatical fragments** with negation/punctuation glued in
   (``don't_adapt``, ``aren't_merged``, ``'doesn't_appear'``) — apostrophes
   and quotes are stripped and the negation polarity is denormalized into a
   ``not_&lt;base>`` form rather than left as an arbitrary contraction.

This is a **pure module** — no DB, no AGE imports, no network. The single
public entry point is :func:`normalize_predicate`, which returns the
canonical predicate string or ``None`` to signal "drop this triple". That
makes it trivially unit-testable and safe to run as a read-only dry-run pass
over the live vocabulary without touching the graph.

Wiring this into the write path is the daemon's choice and must be opt-in;
this module never mutates anything on its own.

## Functions

### `normalize_predicate`

```python
def normalize_predicate(raw: str) -> Optional[str]
```

Normalize a raw extractor predicate to its canonical form.

Returns the canonical predicate string, or ``None`` to signal that the
triple should be dropped (code token, empty, or punctuation-only input).

Pipeline:
  1. fold — lowercase, snake_case, strip quotes/apostrophes (class 3 prep)
  2. drop if empty or a known/heuristic code or shell token (class 1)
  3. drop if a content-free function word / modal (class 1c, issue #45)
  4. strip negation prefix, remember polarity (class 3)
  5. canonicalize the base via the synonym map (class 2)
  6. re-apply ``not_`` prefix if it was negated

The negation prefix is applied *after* canonicalization so that
``doesn't_appear`` and ``does_not_appear`` both land on ``not_appear``,
and a negated synonym (``is not a part of`` → base ``a_part_of`` →
``part_of``) collapses to ``not_part_of``.

Note the base is whatever remains *after* the negation prefix is peeled:
``isn't_a`` strips ``isnt_`` leaving base ``a`` (not in ``SYNONYM_MAP``),
so it yields ``not_a``, not ``not_is_a``.
