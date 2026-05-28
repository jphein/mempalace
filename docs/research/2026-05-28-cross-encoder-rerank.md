# Optional cross-encoder reranking

**Issue:** techempower-org/mempalace#179
**Date:** 2026-05-28
**Status:** Infrastructure shipped; A/B measurement deferred until daemon capacity frees up.

---

## What shipped

An opt-in cross-encoder rerank stage between the existing hybrid fusion
(vector + BM25 + AGE) and result return. Default OFF — preserves the
zero-model-at-query-time default per Principle 4 (local-first, no model
loads unless the operator explicitly opts in).

Components:

- `mempalace/cross_encoder_rerank.py` — module-level helpers (`is_enabled`,
  `get_model_name`, `get_top_n`, `get_scorer`, `rerank`). Lazy-imports
  `sentence_transformers` only when the feature is engaged, so the
  off-by-default cost is zero.
- `mempalace/searcher.py` — `_cross_encoder_rerank_config()` resolver +
  the rerank invocation inside `search_memories`, positioned **after**
  the existing fusion (convex / RRF) and **before** the final
  `n_results` trim. The trim now happens after rerank so the rerank gets
  to see candidates the fused blend buried.
- `mempalace/config.py` — `cross_encoder_rerank`, `cross_encoder_model`,
  and `cross_encoder_top_n` properties on `MempalaceConfig`. Env wins
  over file config, matching the rest of `MempalaceConfig`.
- `pyproject.toml` — `[rerank]` optional extra:
  `pip install mempalace[rerank]` pulls `sentence-transformers>=2.7`.
- `scripts/eval_cross_encoder_rerank.py` — A/B harness reusing the
  `eval_fusion_ab.py` scoring math. Gated by
  `--i-know-the-corpus-is-stable` so it can't accidentally run during
  the KG backfill or while #162 is mid-flight.

## Configuration

| Env | Config key | Default | Meaning |
|---|---|---|---|
| `MEMPALACE_RERANK_CROSS_ENCODER` | `cross_encoder_rerank` | `false` | Master switch |
| `MEMPALACE_RERANK_CROSS_ENCODER_MODEL` | `cross_encoder_model` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Cross-encoder to load |
| `MEMPALACE_RERANK_TOP_N` | `cross_encoder_top_n` | `25` | How many top hits to rerank |

## Why `ms-marco-MiniLM-L-6-v2`

From the True Memory comparison (`docs/research/2026-05-24-true-memory-comparison.md`):

> The ablation shows a cheap reranker (`ms-marco-MiniLM-L-6-v2`, 22M parameters,
> runs on CPU) captures most of the value; upgrading to 149M
> (`ms-marco-MiniLM-L-12-v2`) barely moves the needle — 1.3pp within the 256d
> subfamily.

22M parameters, CPU-friendly, ~90MB to load, sub-200ms latency ceiling on
top-25 rerank batches per query. Operators who care about that 1.3pp can
override via env to the L-12 variant; the architecture treats the model
choice as a knob, not a contract.

## Why the stage runs *after* fusion

The issue explicitly framed this as "between hybrid RRF fusion and
result return." This composes cleanly:

- `candidate_strategy="vector"` → vector candidates → convex / RRF fuse → rerank
- `candidate_strategy="union"` → vector + BM25 union → convex / RRF fuse → rerank
- `candidate_strategy="hybrid"` → vector + BM25 + AGE graph → convex / RRF fuse → rerank

The rerank never replaces fusion; it reorders the already-fused candidate
list. The fused ordering is what's preserved in the tail (anything past
`top_n`) — recall is invariant. The rerank only competes for positions in
the head.

## Why we trim *after* rerank, not before

The existing pipeline trimmed to `n_results` immediately after fusion:

```python
hits = _FUSION_RANKERS[fusion_mode](hits, query)[:n_results]
```

Trimming before rerank would limit the rerank to the top `n_results`
that the fused blend already chose — losing the whole point of having a
reranker (correcting fusion's head). The trim now happens after the
rerank stage so the rerank can promote a candidate from position 6 to
position 1 when the rerank disagrees with fusion.

## Measurement gap

Per `feedback_test_retrieval_against_our_corpus` — retrieval-stack
changes are A/B'd on our own corpus, never trusted from literature. The
harness exists (`scripts/eval_cross_encoder_rerank.py`) and the probe
set exists (`scripts/probes_v2_git_derived.json`), but the run itself is
deferred:

1. The KG backfill is still in flight (Lucid is finishing #250 / the
   isotonic calibrator that depends on the backfill).
2. The #162 RRF-vs-hybrid A/B is also queued against the same daemon
   (Nebula is doing the mempalace/-corpus measurement).
3. Stacking a third A/B on top of those two would steal capacity from
   both, and the results wouldn't be apples-to-apples (rerank-on vs
   rerank-off needs a stable fused baseline to subtract).

The measurement is filed as a follow-up. When the KG backfill + #162
complete, run:

```
python scripts/eval_cross_encoder_rerank.py \\
    --probes scripts/probes_v2_git_derived.json \\
    --palace-path "$MEMPALACE_PALACE_PATH" \\
    --candidate-strategy hybrid \\
    --fusion-mode rrf \\
    --n-results 10 \\
    --i-know-the-corpus-is-stable \\
    --out docs/research/2026-MM-DD-cross-encoder-rerank-ab.json
```

Expected outcomes (predictions to disconfirm, not numbers to trust):

- **Likely small lift on R@5.** The True Memory 56-config ablation
  showed a 1–3pp spread from rerank alone. Our hybrid baseline already
  fuses three signals; the rerank's marginal contribution should be
  smaller than True Memory's (which fused only two).
- **Latency on the order of 50–200ms per query** for the top-25 rerank
  on a single MiniLM-L-6 inference. Acceptable for the daemon path; not
  acceptable as a default.
- **Composes with hybrid AGE fusion.** The rerank operates on the final
  fused candidate list, so the AGE graph hits go through it like any
  other source.

## Follow-ups

- File the measurement run as a follow-up issue once #162 lands.
- Consider a per-query rerank toggle (already supported via the env var
  being read live) so operators can A/B in production traffic.
- Investigate batching across concurrent searches if the daemon ends up
  rerank-on by default in a future release — single-query inference
  leaves throughput on the table.
