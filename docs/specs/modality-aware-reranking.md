# Modality-Aware Reranking — query-intent weighting for mixed verbatim/derivative retrieval

> **Status:** proposed 2026-05-29 (gated — not to be built until the trigger fires)
> **Owner:** JP
> **Issue:** [techempower-org/mempalace#181](https://github.com/techempower-org/mempalace/issues/181)
> **Trigger:** a derivative collection exists alongside the verbatim corpus
> **Composes with:** cross-encoder rerank (#179, shipped), corpus partitioning (#169)

## Problem & motivation

Today every drawer in the palace is *verbatim* — the exact words the user
or an agent produced, stored without summarization (Principle 1). Retrieval
treats all hits as the same modality: a hybrid blend of vector similarity,
BM25, and AGE graph signals (`searcher.py:_hybrid_rank`, `_rrf_rank`),
optionally reordered by a cross-encoder (`cross_encoder_rerank.rerank`).

The moment a *derivative* modality enters the corpus — an Auto Dream
consolidation, an AGE-KG summary row, a Haiku-enriched topic doc, an AAAK
closet card — that uniform treatment becomes a liability. A summary and a
verbatim fragment are not interchangeable for every query:

- A **detail lookup** ("what was the exact error message?", "what command
  did I run?") wants the verbatim fragment. A summary that paraphrases the
  detail away is a worse answer even when it scores higher on cosine
  similarity.
- A **synthesis query** ("what's the overall state of the daemon work?",
  "summarize what changed this month") wants the derivative row. A single
  verbatim fragment, however well-matched, under-answers it.

The True Memory paper (Adler & Zehavi, May 2026,
[arXiv:2605.04897](https://arxiv.org/abs/2605.04897), Section 4.5) applies a
*modality factor* at its Stage 10 reranker:

| Query intent | Effect on summary-type results |
|---|---|
| Detail | penalize 0.7× |
| Synthesis | boost 1.2× |

It is simple, cheap, and principled: the optimal result *type* depends on
the query *intent*. Our fork's own comparison doc
(`docs/research/2026-05-24-true-memory-comparison.md`, "What MemPalace could
learn") already flagged this as "directly applicable if MemPalace ever
introduces derivative collections alongside verbatim."

This spec designs that feature so the issue stays actionable the day the
trigger fires — without building it before there is anything to weight.

## Trigger condition (what must exist before this is built)

This feature is **inert and unbuildable** until the palace contains at least
one derivative collection that is reachable from the search surface. There is
nothing to weight when every hit is verbatim. Concretely, the trigger is **any
one** of:

1. A second, derivative *partition* lands per the corpus-partitioning spec
   (`docs/specs/2026-05-26-corpus-partitioning-by-purpose.md`, #169) — e.g.
   `kg_topics` (KG-derivative / Haiku-enriched topic docs) earns a
   `searchable: true` read surface.
2. Auto Dream consolidations are written back as searchable drawers
   (currently MemPalace deliberately stays un-consolidated; see
   `docs/research/verbatim-vs-derivative-axis.md` and the
   `project_auto_dream` memory).
3. AGE-KG summary rows or AAAK closet cards become first-class search hits
   rather than an index-only layer.

Until then this spec is filed-for-future. The precondition is the same one
issue #181 states: *"only applicable once there are derivative collections
alongside verbatim drawers."*

**Corollary trigger discipline:** because modality weighting only matters
across modalities, the feature must ship *together with* the first derivative
collection, not before it — and its acceptance test (below) requires a
two-modality fixture that cannot exist today.

## Proposed design

Two pieces, both opt-in, both bounded so they preserve 100% recall:

1. A **modality tag** on each hit (`modality: "verbatim" | "derivative"`),
   resolved from existing metadata — not a new write-time field where it can
   be derived.
2. A **query-intent classifier** (`detail | synthesis | neutral`) feeding a
   **bounded reweight** that nudges derivative hits up for synthesis queries
   and down for detail queries, leaving verbatim as the default preference.

### Where it sits in the pipeline

The reweight runs as a distinct stage *after* fusion and *after* (or folded
into) the optional cross-encoder rerank, mirroring how `cross_encoder_rerank`
already composes:

```
vector + BM25 + AGE fusion        (searcher.py: _FUSION_RANKERS[fusion_mode])
  → optional cross-encoder rerank  (searcher.py:2182 → cross_encoder_rerank.rerank)
  → optional modality reweight     (NEW — this spec)
  → trim to n_results              (searcher.py:2195)
```

It reorders the already-fused/reranked list; it never replaces fusion and it
never drops a hit. This is the same invariant `cross_encoder_rerank.rerank`
holds ("Reranking is a quality lift, not a correctness invariant",
`cross_encoder_rerank.py:230`).

### Modality resolution

`modality` is **derived, not stored** — Principle 1 forbids re-tagging
verbatim content, and a derived tag can't drift from the source of truth.
Resolution precedence:

- If the hit's partition is declared derivative in config (the #169
  `partitions` block carries a per-partition `kind`/`modality` field), the
  hit is `derivative`.
- Else if `matched_via` indicates a derivative source (e.g. a future
  `closet-summary` or `kg-summary` matched_via, analogous to the existing
  `drawer+closet` value at `searcher.py:2037`), `derivative`.
- Else `verbatim` (the default — and today's only — value).

A hit that cannot be classified is treated as `verbatim`, the safe default
that preserves current behavior.

### Query-intent classification

The classifier must honor *local-first, zero-external-API-by-default*. Three
implementations, in increasing cost, selectable by config:

1. **Lexical (default).** A rule set over the query string: interrogatives
   and specificity markers ("exact", "what command", "which line", "error
   message", quoted strings, code tokens) → `detail`; aggregation markers
   ("summarize", "overview", "overall", "what's the state of", "across") →
   `synthesis`; otherwise `neutral`. Zero model load, sub-millisecond, fully
   inspectable. This is the shipping default because it adds no query-time
   model — consistent with the cross-encoder defaulting off
   (`cross_encoder_rerank.py:34`, "no-model-at-query-time default").
2. **Local LLM (opt-in).** A tiny local classifier (e.g. Phi-4-mini on the
   homelab GPU, the same model the KG triple extractor targets — see
   `docs/specs/kg-triple-extraction.md`) returns one of the three labels.
   Local runtime, not an external API.
3. **Cross-encoder-derived (opt-in, free when rerank is on).** When the
   cross-encoder rerank stage is already active, its per-hit
   `cross_encoder_score` spread can act as a weak intent proxy without a
   second model call. Lower priority; documented, not optimized.

`neutral` applies **no** reweight — verbatim stays the default preference,
satisfying Principle 1 and issue #181's "keep verbatim as the default."

### The bounded reweight (the recall-safe core)

The fork already has the exact idiom: a small, **capped** distance
adjustment that can reorder neighbors but can never push a relevant drawer
out of the result set. See `ratings.py` (`RATING_DISTANCE_CAP = 0.12`,
clamped both directions, `ratings.py:90`) and `recency.py`
(`recency_distance_adjustment`, same capped pattern, ships dark behind
`PALACE_RECENCY_BOOST`). Modality weighting follows this pattern exactly
rather than inventing a multiplicative factor that could dominate the score:

```python
# mempalace/modality.py (NEW), mirroring ratings.py / recency.py
MODALITY_DISTANCE_CAP = 0.10  # tuned after A/B on our corpus; <= rating cap

def modality_distance_adjustment(modality: str, intent: str) -> float:
    """Small, bounded distance nudge by (modality, query-intent).

    Negative = pull toward the top, positive = push down. Clamped to
    ±MODALITY_DISTANCE_CAP so it reorders neighbors but never displaces a
    relevant drawer out of the result set — recall is preserved.
    """
```

| (modality, intent) | direction | rationale |
|---|---|---|
| derivative, synthesis | pull up (−) | True Memory's 1.2× boost, expressed as a bounded distance nudge |
| derivative, detail | push down (+) | True Memory's 0.7× penalty |
| verbatim, detail | pull up (−, smaller) | reinforce the verbatim default for detail |
| any, neutral | 0.0 | no reweight; verbatim default stands |
| verbatim, synthesis | 0.0 | never penalize verbatim; only lift derivative |

Expressing the factor as a **bounded additive distance adjustment** rather
than a **multiplicative score factor** is the deliberate fork divergence
from True Memory: it inherits the cap-preserves-recall guarantee the fork
already relies on, and it composes with `effective_dist` arithmetic at
`searcher.py:2065` instead of fighting it.

### Gating

Off by default, env-wins-over-config, identical to every other optional
retrieval stage:

- `MEMPALACE_MODALITY_RERANK=1` (env) / `"modality_rerank": true`
  (config.json) — enable the stage.
- `MEMPALACE_MODALITY_INTENT=lexical|llm|cross_encoder` — classifier
  backend, default `lexical`.
- `MEMPALACE_MODALITY_CAP=0.10` — the distance cap, for A/B tuning.

When the flag is off, the module imports nothing and adds zero query-time
cost — the same discipline as `cross_encoder_rerank.is_enabled`
(`config.py:400`).

## Integration points in current code (cite real files/functions)

- **`mempalace/searcher.py:2182`** — the optional-stage insertion point. The
  modality reweight slots immediately after the `_cer.rerank(...)` block
  (`searcher.py:2186`) and before the `hits = hits[:n_results]` trim
  (`searcher.py:2195`), so it sees the full fused+reranked pool.
- **`mempalace/searcher.py:2065`** — `effective_dist = max(0.0, min(2.0, dist
  - boost + rating_adj + recency_adj))`. The modality adjustment becomes a
  fourth bounded addend here when the reweight is folded into hit
  construction, or a post-fusion reorder when applied as a stage. The stage
  form is preferred because intent depends on the *query*, which the
  per-hit construction loop doesn't currently carry.
- **`mempalace/searcher.py:2079`** — the hit `entry` dict. Add a derived
  `modality` key here (alongside `matched_via`) so the reweight stage and
  `include_trace` observability can read it.
- **`mempalace/cross_encoder_rerank.py:181`** — `rerank()` is the structural
  template: top-N window, injection-seam `scorer` for tests, never-drop
  invariant, `try/except` that falls back to the prior order on failure.
  `modality.reweight()` copies this shape.
- **`mempalace/config.py:400-437`** — the `cross_encoder_rerank` /
  `cross_encoder_model` / `cross_encoder_top_n` properties are the config
  template: env-wins, lazy import, off-by-default. Add `modality_rerank`,
  `modality_intent`, `modality_cap` properties in the same style.
- **`mempalace/ratings.py:76` / `mempalace/recency.py:79`** — the bounded
  `*_distance_adjustment` functions are the exact recall-safe idiom the new
  `mempalace/modality.py` mirrors (cap, clamp both directions, comment that
  recall is preserved).
- **`mempalace/searcher.py:2037`** — the `matched_via = "drawer+closet"`
  precedent shows how a derivative/enriched source already surfaces a
  distinct `matched_via`; the modality resolver reads the same field.
- **`docs/specs/2026-05-26-corpus-partitioning-by-purpose.md`** — the
  `partitions` config block (its per-partition tuple) is where a
  partition's derivative-ness is declared; the modality resolver reads it.

## Data/schema impact

- **No verbatim re-write.** `modality` is derived at read time from partition
  config + `matched_via`. No migration, no new column on
  `mempalace_drawers`, no re-tagging — Principle 1 (verbatim always) and
  Incremental-only both hold.
- **Optional config keys only.** `modality_rerank`, `modality_intent`,
  `modality_cap` join the existing optional-retrieval keys in
  `~/.mempalace/config.json`. Absent ⇒ today's behavior, bit-for-bit.
- **New module** `mempalace/modality.py` (pure functions, no DB), mirroring
  `ratings.py` / `recency.py`. Unit-testable without a palace.
- **Observability.** When enabled, each reweighted hit gains a
  `modality_adjustment` key (the applied nudge) and the resolved
  `modality` + `query_intent`, surfaced through `include_trace` the way
  `cross_encoder_score` already is.

## Open questions

1. **Stage vs. fold.** Apply modality as a post-rerank *reorder stage*
   (clean, sees the query) or fold the adjustment into `effective_dist` at
   `searcher.py:2065` (cheaper, but the construction loop doesn't carry the
   query/intent today)? Lean: stage, for the query dependency. Decide by
   measuring whether the construction loop can cheaply receive the intent.
2. **Cap value.** `0.10` is a placeholder ≤ the rating cap. Must be A/B'd on
   the fork's own corpus (per "test retrieval against our corpus"
   discipline) once a derivative collection exists — literature factors
   (0.7×/1.2×) don't translate directly to a bounded additive scale.
3. **Intent classifier scope.** Is the lexical rule set enough, or does the
   homelab corpus need the local-LLM classifier to disambiguate detail vs.
   synthesis reliably? Resolve with a labeled query sample, not a priori.
4. **Interaction with cross-encoder.** When both stages are on, does the
   cross-encoder already implicitly capture modality preference (it scores
   query↔doc relevance directly), making the explicit reweight redundant or
   double-counting? Needs an ablation: rerank-only vs. rerank+modality.
5. **Three-way intent vs. continuous.** `detail|synthesis|neutral` is the
   True Memory shape. A continuous intent score (0=pure detail, 1=pure
   synthesis) mapped to a continuous nudge may behave better at the
   boundary. Start discrete (matches the paper, easier to test); revisit if
   boundary queries misbehave.

## References

- Issue: [techempower-org/mempalace#181](https://github.com/techempower-org/mempalace/issues/181)
  — Evaluate modality-aware reranking for mixed verbatim/derivative retrieval.
- True Memory comparison: `docs/research/2026-05-24-true-memory-comparison.md`
  (see "What MemPalace could learn → Modality-aware reranking").
- True Memory paper: Adler & Zehavi, "Storage Is Not Memory" (May 2026),
  [arXiv:2605.04897](https://arxiv.org/abs/2605.04897), Section 4.5
  (Modality-Aware Reranking).
- Verbatim-vs-derivative axis: `docs/research/verbatim-vs-derivative-axis.md`.
- Shipped cross-encoder rerank (the structural template): #179,
  `mempalace/cross_encoder_rerank.py`.
- Bounded recall-safe adjustment idiom: `mempalace/ratings.py` (#159),
  `mempalace/recency.py` (#158).
- Corpus partitioning (the most likely trigger):
  `docs/specs/2026-05-26-corpus-partitioning-by-purpose.md` (#169).
