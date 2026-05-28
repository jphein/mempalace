# RRF fusion vs convex-blend rerank — A/B on our corpus

**Date:** 2026-05-28
**Tracking:** [techempower-org/mempalace#162](https://github.com/techempower-org/mempalace/issues/162)
**Status:** RESEARCH — measurement complete. **RRF underperforms the
convex blend on this corpus.** Convex stays the default; RRF stays the
opt-in alternative for callers who want it.
**Code:** `mempalace/searcher.py` (`_FUSION_RANKERS` dispatch + `_rrf_rank`),
`mempalace/rrf.py`.
**Eval:** `scripts/eval_fusion_ab.py --mine-corpus mempalace --probes scripts/probes_v2_git_derived.json`.

## Why this exists

The fork ships two fusion modes in `search_memories`:

* **`fusion_mode="convex"` (default)** — `_hybrid_rank` blends absolute
  cosine similarity (weight 0.6) with min-max-normalized BM25 (weight 0.4)
  on a shared score scale. Tuned through repeated A/B rounds during the
  2026-05-14 hybrid-cutover work.
* **`fusion_mode="rrf"`** — `_rrf_rank` fuses the two *rank orderings*
  (vector by ascending distance, BM25 by descending score) via the
  classical Cormack 2009 RRF formula `Σ 1/(k + rank_i)` with `k=60`.
  Score-scale agnostic — only ordinal information is used.

The [#82 / multi-encoder RRF research][82] left an explicit open question
(its "Open question 1"): a raw-vector RRF lift of +0.0841 MRR collapses to
flat once `search_memories`' closet boost + BM25 rerank run on top.
That finding spoke to *encoder*-orthogonality fusion. It did **not**
test the cleaner question — RRF over the vector + BM25 orderings
*as the final ranker*, vs the existing convex blend. That's the gap
issue #162 named; this is the run that closes it.

## The honest framing

> "I don't know which is better on my corpus and want to find out."
>
> — issue #162

No prior. We respect [`feedback_test_retrieval_against_our_corpus`][corpus] —
no literature-trusted numbers, no synthetic queries; the A/B is run
on the same probe set the multi-encoder eval used so the result is
directly comparable to the 2026-05-15 finding.

## Setup

* **Corpus:** the `mempalace/` package directory of this fork, mined
  into a fresh local ChromaDB palace via
  `scripts/eval_fusion_ab.py --mine-corpus mempalace`. Self-contained:
  doesn't touch the production daemon or share GPU/daemon capacity
  with real callers.
* **Drawers:** 3413 (single-encoder default ONNX MiniLM). The
  2026-05-15 multi-encoder eval mined the same directory and reported
  2084 drawers; the fork has grown — more module files, more
  docstring chunks — but the corpus shape is the same.
* **Probe set:** `scripts/probes_v2_git_derived.json`, 200 probes, each
  a commit-subject query paired with the file the commit touched. Same
  apparatus as [#82's writeup][rrf-82-doc].
* **Candidate strategy:** `"hybrid"` for both runs (vector +
  tsvector-BM25 + AGE-graph candidate sources). The local-Chroma palace
  has no AGE backend so the graph candidate source is silently empty;
  the bm25_postgres path falls back to local BM25 scoring. Only
  `fusion_mode` flips between the two runs.
* **Top-K:** 10. Metrics: MRR, Recall@5, Recall@10, end-to-end latency
  over all 200 probes.
* **Machine:** katana (Linux 6.17, AMD64 CPU, ONNX MiniLM CPU-only).

## Results

| Metric | convex | rrf | Δ (rrf − convex) |
|---|---:|---:|---:|
| MRR | 0.4075 | 0.3758 | **−0.0318** |
| Recall@5 | 47.0% | 45.0% | −2.0 pp |
| Recall@10 | 52.0% | 47.0% | **−5.0 pp** |
| Found (top-10) | 104 / 200 | 94 / 200 | −10 |
| Elapsed (200 probes) | 32.2 s | 29.5 s | −2.7 s |
| QPS | 6.21 | 6.78 | +0.57 |

**RRF loses on every quality metric.** It is slightly faster (RRF skips
the per-candidate BM25 normalization the convex blend runs), but the
quality cost is more than the latency saves.

Per-probe breakdown across 200 probes:

| Outcome | Count |
|---|---:|
| **Improved** (rrf ranked the target strictly better) | 10 |
| **Regressed** (rrf ranked the target strictly worse) | 20 |
| **Tied** (same rank, including same miss in both) | 170 |

Twice as many regressions as improvements. The regressions are *severe*
— most of them are rank-1-under-convex → miss-under-rrf, i.e. RRF
demotes the correct answer off the top-10 entirely:

```
Top RRF regressions (rank under convex → rank under rrf):
  1 → miss   Retry tool_search once on Chroma "Error finding id" transient (#1315)
  1 → miss   Clamp effective_distance to valid cosine range [0, 2]
  1 → miss   Address Copilot review on #1306
  1 → miss   Split get_or_create_collection on reopen (follow-up to #1262)
  3 → miss   Verify write roundtrip before bailout
  3 → miss   Don't write chunking defaults in cfg.init()
```

The improvements, by contrast, are smaller in magnitude (none reach
rank 1 from a previous miss):

```
Top RRF improvements (rank under convex → rank under rrf):
  miss → 2   Harden HNSW startup preflight
  miss → 5   Add hook_verbatim_mode toggle for transcript ingest
  miss → 6   Paginate closet_llm col.get (#1073)
  miss → 9   Quote ChromaBackend annotation for Python 3.9 compatibility
     5 → 3   Atomic write to prevent partial corruption on crash
     5 → 3   Add multi-agent support roadmap …
```

Raw per-probe ranks + deltas: `docs/research/2026-05-28-rrf-vs-hybrid-rerank-ab.json`.

## Reading the result

The asymmetry in the convex blend is doing real work. `_hybrid_rank`
weights vector similarity at 0.6 and BM25-normalized at 0.4, then
floors `bm25_postgres`/`bm25_sqlite`-surfaced candidates at 0.9 BM25-norm
so the tokenizer-disagreement guard doesn't demote backend matches.
That weighting reflects a tuned prior — on our corpus, vector
similarity is the stronger signal, and BM25 is a useful tiebreak
when terminology matches verbatim. The convex blend keeps that
asymmetry.

RRF throws the prior out. By using only rank positions, it treats a
candidate that ranks rank-1 in vector and rank-50 in BM25 the same
as one that ranks rank-1 in BM25 and rank-50 in vector — both score
`1/(60+1) + 1/(60+50) = 0.025`. On a corpus where vector dominates,
that's a loss: RRF "trades down" a strong vector hit for a weak BM25
match. The rank-1→miss regressions are the visible cost — those are
the cases where vector got the answer right and BM25's noise diluted
the fusion away from it.

The improvements are mostly BM25-favorable terminology matches that
the convex blend's vector-heavy weighting downweighted too far. Those
exist — they're the "miss → 2" / "miss → 5" entries — but they're
fewer and smaller than the regressions.

This is consistent with the [#82 through-pipeline finding][rrf-82-doc]
("the encoder-orthogonality signal that makes RRF work on raw vector
retrieval is largely already captured by the production path's closet
boost + BM25 rerank"). Where #82's caveat ended in a flat result (3-way
RRF over multiple encoders), this run shows that *fusing vector + BM25*
as ranks loses ground to the existing weighted blend — the convex
blend isn't just "good enough," it's actively better on our corpus.

## Recommendation

* **Keep the convex blend as the default** (`fusion_mode="convex"`).
  No change to `search_memories` callers; production behavior is
  unchanged.
* **Keep `fusion_mode="rrf"` shipping** as the explicit opt-in. It is
  still useful for callers who want a score-scale-agnostic fusion —
  e.g. future multi-list fusion paths (encoder ensembles, graph-source
  re-injection) where convex weighting can't be tuned cleanly. The A/B
  apparatus (`scripts/eval_fusion_ab.py`) stays in the tree so the
  experiment is reproducible and so the next person who asks "should
  we flip to RRF?" gets a numeric answer instead of a debate.
* **Do not pursue an RRF `k` sweep** as a follow-up. The cost shape
  (1→miss regressions) is structural — RRF discarding the weight
  prior — not a tuning artifact; sweeping `k` would shift the bias
  between top-of-list precision and tail recall, but the strong-vector
  bias the convex blend exploits would still be unavailable to RRF.

## What this run does NOT measure

* **Production-scale corpus.** The mined corpus is the mempalace/
  package only (3413 drawers), not the production 390K-drawer palace
  on `familiar`. Driving the production palace would require the
  daemon's `/search/hybrid` to forward `fusion_mode` (it currently
  hard-codes `candidate_strategy="hybrid"` and doesn't accept a
  fusion-mode body field). That's a palace-daemon patch — out of
  scope here, and given the clear loss at this scale, lower priority
  than it would otherwise be. A scale-up experiment would still be
  informative since corpus shape can shift fusion behavior, but the
  result here is decisive enough that the bar to flip the default has
  risen meaningfully.
* **User-shaped queries.** The probe set is derived from commit
  subjects — docstring-shaped natural language. Real chat-style user
  queries may be terser / more question-shaped. Issue #82 raised the
  same caveat. If user-shaped queries do change the answer, it would
  show up first in the production-corpus run above.
* **AGE-graph candidate path.** `candidate_strategy="hybrid"` includes
  AGE graph candidates in production; the temp local-Chroma palace
  here has no AGE backend, so that source is silently empty.
  Convex-vs-rrf comparison is unaffected (both modes see the same
  candidate pool), but if AGE graph candidates dominate the
  production pool's composition, behavior could shift there.
* **`k` tuning.** RRF uses the Cormack default `k=60`. We did not
  sweep — see the recommendation above for why this isn't worth
  pursuing.

## Reproducing

```bash
# Self-contained run (no daemon, no production GPU)
python scripts/eval_fusion_ab.py \
    --probes scripts/probes_v2_git_derived.json \
    --mine-corpus mempalace \
    --n-results 10 \
    --out docs/research/2026-05-28-rrf-vs-hybrid-rerank-ab.json
```

The harness mines the corpus into a temp local-Chroma palace, runs
all probes twice (convex + rrf), writes a JSON report with full
per-probe ranks, then deletes the temp palace (`--keep-palace` to
retain it for follow-up). Mining takes ~3 minutes on katana CPU;
search ~30 seconds per mode (200 queries).

Scoring math is pure and tested at the math layer — see
`tests/test_eval_fusion_ab.py` (29 tests).

## Related

* [techempower-org/mempalace#162][162] — tracking issue, now closable.
* [techempower-org/mempalace#82][82] — multi-encoder RRF, the prior
  finding that motivated asking this question.
* [`docs/research/2026-05-15-multi-encoder-rrf.md`][rrf-82-doc] — the
  through-pipeline RRF write-up referenced above.
* [`scripts/eval_fusion_ab.py`][harness] — the A/B apparatus.

[162]: https://github.com/techempower-org/mempalace/issues/162
[82]: https://github.com/techempower-org/mempalace/issues/82
[corpus]: # "feedback_test_retrieval_against_our_corpus — A/B on our corpus, not from literature"
[rrf-82-doc]: 2026-05-15-multi-encoder-rrf.md
[harness]: ../../scripts/eval_fusion_ab.py
