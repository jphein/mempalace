# `mempalace.kg_canonical_vocab`

Source: [`mempalace/kg_canonical_vocab.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/kg_canonical_vocab.py)

Closed-vocabulary predicate mapping spike (issue #72).

#50 → PR #61 / #71 established that the AGE knowledge graph carries **64,029**
distinct ``r.relation_type`` predicate strings over 1.72M RELATION triples, and
that the conservative surface-form normalizer (:mod:`kg_predicate_norm`) only
trims ~3% of the *distinct* vocabulary because a long tail of one-off verbose
LLM paraphrases dominates the cardinality.

This module is a **design spike**, not a production write path. It proves
whether a small curated canonical ontology plus embedding-nearest-canonical
mapping can collapse the vocabulary to "dozens" of relation types while
covering the bulk of *triples* (frequency-weighted). The deliverable is
measurement: see ``scripts/canonical_vocab_report.py``.

Pipeline for one raw predicate:

    raw  --kg_predicate_norm.normalize_predicate-->  surface-normalized
         --embed (mempalace MiniLM, same model as the corpus)-->  vector
         --cosine nearest canonical-->  canonical | "other" (if below threshold)

The canonical set (:data:`CANONICAL_RELATIONS`) is a curated ~40-relation
ontology seeded from the highest-frequency post-normalization predicates on
production (``is_a``, ``contains``, ``depends_on``, ``created_by``, …) plus a
handful of schema.org / SKOS-style relations to catch common clusters. Each
canonical has a short gloss; we embed the gloss (not just the bare token) so
the nearest-neighbour match has more semantic surface to bind to.

Embeddings: we reuse ``mempalace.embedding.get_embedding_function`` — the same
ONNX MiniLM (384-dim) the palace embeds drawers with — so no new heavy
dependency is added and predicate similarity is measured in the corpus's own
embedding space. If that import fails, callers can fall back to the pure
lexical scorer (:func:`lexical_similarity`) and the report flags the downgrade.

## Classes

### `class Canonical`

A canonical relation: the stored predicate name + a gloss to embed.

### `class CanonicalMapper`

Maps raw predicates to a closed canonical relation set.

Construction embeds each canonical's gloss once (or, in lexical mode, just
holds the canonical names). :meth:`map_predicate` then surface-normalizes
the raw predicate, scores it against every canonical, and returns the best
match if it clears ``threshold`` — otherwise ``"other"`` (the explicit
long-tail bucket). A dropped predicate (code token, per
``normalize_predicate``) maps to ``None``.

#### `__init__`

```python
def __init__(self, threshold: float = 0.45, scorer: Optional[Callable[[str, list[str]], list[float]]] = None, use_embeddings: bool = True)
```

#### `map_predicate`

```python
def map_predicate(self, raw: str) -> tuple[Optional[str], float]
```

Return (canonical_or_other_or_None, score).

* ``None`` — dropped by ``normalize_predicate`` (code token / junk).
* ``"other"`` — kept but below threshold (the long-tail bucket).
* canonical name — nearest canonical at/above threshold.

#### `map_predicates`

```python
def map_predicates(self, raws: Iterable[str], batch_size: int = 1024) -> list[tuple[Optional[str], float]]
```

Batched equivalent of :meth:`map_predicate` for bulk callers.

Same per-element semantics as the per-call API — failed-normalize,
canonical short-circuit, embedding nearest-neighbour, threshold gate,
``"other"`` long-tail bucket — but collapses the embedding cost from N
single-string ``ef()`` calls into ceil(N/batch_size) batch calls. Output
is aligned 1:1 with the input iterable (one tuple per input, in order),
so callers can ``zip(raws, mapper.map_predicates(raws))`` without
bookkeeping.

Returns
-------
list[tuple[Optional[str], float]]
    Per-input result, same shape as :meth:`map_predicate`:

    * ``(None, 0.0)`` — dropped by ``normalize_predicate``.
    * ``(name, 1.0)`` — normalized form is exactly a canonical name.
    * ``(canonical, score)`` — nearest canonical at/above threshold.
    * ``("other", score)`` — kept but below threshold.

## Performance

Measured 2026-05-27 on the live ~64k-predicate vocabulary, embedding
mode, batch_size=1024:

* GPU (per-call ``map_predicate``): 64,801 raws in ~7 min (~155/s)
* GPU (batched ``map_predicates``): 64,801 raws in ~74 s (~880/s)
* CPU (per-call): ~21 min for the same input

The batched path's win comes from amortising Python-side model launch
overhead — each ``ef()`` call would otherwise dispatch a batch of 1 +
the 39 pre-cached canonicals. Lexical mode does NOT benefit from
batching (the scoring is pure-Python token overlap); the implementation
falls back to a per-call loop there. Reach for this method when N is
in the thousands and the mapper is in embedding mode; below ~100 raws
the overhead crossover makes the per-call API equivalent or faster.

The next caller in the tree is
``palace-daemon/scripts/canonical_migration.py``, which re-runs the
mapping over the live AGE predicate vocabulary; replacing its per-call
loop with this method cuts the embedding-mode migration from minutes
to under two.

## Functions

### `lexical_similarity`

```python
def lexical_similarity(a: str, b: str) -> float
```

Jaccard token overlap in [0, 1] — the no-embedding fallback.

Crude but dependency-free: shared snake_case tokens / union. Used when the
ONNX embedding model can't be loaded, and the report says so explicitly.

### `build_embedding_scorer`

```python
def build_embedding_scorer() -> Optional[Callable[[str, list[str]], list[float]]]
```

Return ``score(query, candidates) -> [cosine...]`` backed by mempalace's
ONNX MiniLM, or ``None`` if the model can't be loaded.

The returned scorer batch-embeds ``[query] + candidates`` in one call and
returns the cosine of query vs each candidate. Caller decides the
threshold. Kept as a factory so the (slow) model load happens once and the
mapper can be constructed lazily / fall back cleanly.
