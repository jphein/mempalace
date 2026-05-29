# Post-migration E2E QA — LongMemEval oracle

> **Status:** published 2026-05-29
> **Closes:** [techempower-org/mempalace#168](https://github.com/techempower-org/mempalace/issues/168),
> [#41](https://github.com/techempower-org/mempalace/issues/41)
> **Run:** [SME #44](https://github.com/techempower-org/multipass-structural-memory-eval/issues/44)
> (QA) / [#98](https://github.com/techempower-org/multipass-structural-memory-eval/issues/98)
> (corrected R@5 matcher) — both **closed**
> **Source results:** [`docs/longmemeval_mempalace_results.md`](https://github.com/techempower-org/multipass-structural-memory-eval/blob/main/docs/longmemeval_mempalace_results.md)
> in techempower-org/multipass-structural-memory-eval

## Why this report exists

Earlier drafts of the README attributed a "0.984 R@5 but ~17% E2E QA"
critique to engram-2 — the implication being that MemPalace retrieves the
right session yet can't turn it into an answer. That specific framing was
**never substantiated in engram-2's published materials**: their ~17-point
figure is the gap between engram-2's *own* LoCoMo score (74.5%) and SOTA,
attributed to the answerer model, and no "17% E2E QA for MemPalace" number
appears in their tables (see
[`docs/research/2026-05-24-memory-system-benchmarks.md`](../../../docs/research/2026-05-24-memory-system-benchmarks.md#the-engram-2-17-e2e-qa-for-mempalace-claim)).

But the underlying *question* was fair and unanswered: the fork had only
ever published **retrieval recall** (0.984 R@5, inherited from upstream's
held-out hybrid), never an **end-to-end QA accuracy** on a standard
benchmark. The corpus-shape pathology that critique surfaced — checkpoint
drawers dominating `mempalace_search`, `kind=content` returning ~3 tokens/Q
pre-migration — was real and is closed by the pgvector + AGE migration.

This report closes the loop: it publishes the **post-migration** R@5
*and* the first end-to-end QA-accuracy number for the fork, on the same run,
so the retrieval→consumption gap is measured rather than assumed.

## Headline

| Metric | Daemon `/search` (default) | `/search/age-fused` | Familiar |
|---|---:|---:|---:|
| **R@5** (drawer_id, #98 matcher) | **97.0%** | 90.2% | 28.6% |
| **E2E QA accuracy** | **60.40%** | 17.60% | 29.20% |
| Retrieval→QA gap (R@5 − QA) | +38.4pp | +72.8pp | −2.4pp |

n = 500 (LongMemEval **oracle** split). Reader **`o4-mini`**, judge
**`gpt-5.3-chat`**, both on Azure Foundry.

**The headline is 60.40%** — the production palace-daemon `/search` path
(vector + BM25). It is the number directly comparable to LongMemEval's
canonical overall QA accuracy.

Two things must be stated alongside it, not buried:

1. **Retrieval is near-ceiling; the reader is the bottleneck.** R@5 of
   97.0% means the gold session was in the top-5 essentially every time,
   yet QA landed at 60.40% — a **+38.4pp** retrieval→QA gap. The post-
   migration palace finds the right memory; the open work is in
   *consumption* (the Cat 9 / "Handshake" problem), not retrieval. This is
   the precise inverse of the "can't retrieve" critique.
2. **age-fused underperformed badly (17.60%) — and we know why.** It is
   **not** evidence that AGE-graph fusion is structurally worse. Two
   confounds: (a) the snippet handed to the reader was ~459 chars mean vs
   ~2,539 for `/search` — 5.5× narrower, starving the reader
   ([palace-daemon#150](https://github.com/techempower-org/palace-daemon/issues/150));
   (b) the AGE **triples** layer was effectively empty during the bench
   window (`kg_stats` reported `triples: 1` on 2026-05-25 after the
   entities-only backfill), so the graph half of the RRF fusion had almost
   nothing to fuse. The real age-fused A/B must rerun once the triple layer
   is fully populated and the snippet boundary is fixed.

## Per-category

### E2E QA accuracy (SME #44, 2026-05-28)

ABSTAIN counts as correct for the abstention category (`cat_1_negative`)
per LongMemEval convention.

| SME category | LME type | n | QA-acc daemon `/search` | QA-acc familiar |
|---|---|---:|---:|---:|
| cat_1          | single-session (IE)      | 150 | 52.67% | 21.33% |
| cat_2c         | multi-session (MR)       | 121 | 74.38% | 14.88% |
| cat_3_partial  | knowledge-update (KU)    | 72  | 69.44% | 27.78% |
| cat_6          | temporal-reasoning (TR)  | 127 | 44.09% | 37.01% |
| cat_1_negative | abstention (ABS)         | 30  | 90.00% | 96.67% |
| **Overall**    | —                        | 500 | **60.40%** | **29.20%** |

cat_6 (temporal reasoning) is the weakest category and also the only one
where the reader frequently *abstained* (19 of 127) rather than guessing —
a healthier failure mode than cat_1/cat_2c, where every wrong answer was a
confident wrong answer (0 abstentions).

### R@5 retrieval recall — corrected matcher (SME #98, 2026-05-29)

R@5 = a parent drawer of the gold session appeared in the top-5 retrieved
chunks, computed with the chunk-suffix-aware drawer_id matcher. Re-scored
from the existing 2026-05-28 rerun records — **no new bench compute**.

| SME category | LME type | n | R@5 daemon `/search` | R@5 age-fused | R@5 familiar |
|---|---|---:|---:|---:|---:|
| cat_1          | single-session (IE)      | 150 | 100.00% | 100.00% | 38.00% |
| cat_2c         | multi-session (MR)       | 121 | 98.35%  | 98.35%  | 26.45% |
| cat_3_partial  | knowledge-update (KU)    | 72  | 100.00% | 100.00% | 34.72% |
| cat_6          | temporal-reasoning (TR)  | 127 | 90.55%  | 64.57%  | 21.26% |
| cat_1_negative | abstention (ABS)         | 30  | 96.67%  | 93.33%  |  6.67% |
| **Overall**    | —                        | 500 | **97.00%** | **90.20%** | **28.60%** |

An earlier reading of the source doc showed R@5 ≈ 3.97% and framed
retrieval as broken. It was a **matcher** bug, not a retrieval failure: the
daemon chunks each drawer into `<parent>_chunk_NNNNNN` and `/search`
returns chunk IDs, which the old matcher compared exact-string against the
parent IDs stored at ingest. SME #98 strips the suffix before comparing.
The QA-accuracy figures never depended on the matcher and are unchanged.

## What this says about the fork

- **The substrate is parity-good.** SME #51 separately measured the
  postgres-vector substrate at R@5 = 0.966 byte-identical to upstream; the
  97.0% here is consistent with that. The gap from the published 87–95% QA
  range (OMEGA, Hindsight, True Memory) lives in the consumption layer and
  the reader/judge stack, not the retrieval substrate.
- **The open frontier is the Handshake, not recall.** A +38.4pp
  retrieval→QA gap on the default path is the clean, quantified statement of
  the Cat 9 problem the fork has been describing qualitatively. Familiar's
  near-zero gap (−2.4pp) is the opposite shape — its reader keeps pace with
  its (much weaker, corpus-mismatched) retrieval.

## Caveats (read before citing)

- **Split:** all numbers are the LongMemEval **oracle** split (small
  per-question haystack, ~3–6 sessions). The earlier README note said
  "LongMemEval-**S**"; the run that actually landed is **oracle**. S
  (~115K tok/Q) and M (~1.5M tok/Q) shift numbers downward and are
  follow-up scope — comparisons must hold the split constant.
- **Reader/judge stack:** `o4-mini` reader + `gpt-5.3-chat` judge (Azure
  Foundry) are newer than the GPT-4 family LongMemEval's published runs
  used. Cross-stack comparison is standard for this benchmark but must be
  flagged: **do not** read 60.40% as "below OMEGA's 95.4%" without also
  naming the model-stack delta.
- **age-fused (17.60%)** is confounded by snippet width (palace-daemon#150)
  and an empty triples layer; it is a known-broken-harness reading, not a
  verdict on graph fusion. Rerun when triples are populated and the snippet
  boundary is fixed.
- **Familiar** ran without per-question wing scoping (its eval endpoint
  doesn't accept a wing scope yet), so its numbers are best-case; its low
  R@5/QA reflect a Hybrid-v4 stack tuned for its own corpus, not
  LongMemEval's one-drawer-per-session topology.

## References

- Source results doc (verbatim numbers above):
  [`docs/longmemeval_mempalace_results.md`](https://github.com/techempower-org/multipass-structural-memory-eval/blob/main/docs/longmemeval_mempalace_results.md),
  techempower-org/multipass-structural-memory-eval.
- Runs: SME [#44](https://github.com/techempower-org/multipass-structural-memory-eval/issues/44)
  (QA, closed), [#45](https://github.com/techempower-org/multipass-structural-memory-eval/issues/45)
  (search-endpoint A/B), [#46](https://github.com/techempower-org/multipass-structural-memory-eval/issues/46)
  (Familiar adapter), [#98](https://github.com/techempower-org/multipass-structural-memory-eval/issues/98)
  (corrected R@5 matcher, closed).
- Engram-2 "17% E2E QA" claim, fact-checked:
  [`docs/research/2026-05-24-memory-system-benchmarks.md`](../../../docs/research/2026-05-24-memory-system-benchmarks.md#the-engram-2-17-e2e-qa-for-mempalace-claim).
- Snippet-width bug: [palace-daemon#150](https://github.com/techempower-org/palace-daemon/issues/150).
- Fork issues this closes: [#168](https://github.com/techempower-org/mempalace/issues/168),
  [#41](https://github.com/techempower-org/mempalace/issues/41).
