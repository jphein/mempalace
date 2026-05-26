# nakata-app/adaptmem v0.7.0 — techniques deep-dive and training-config recommendations

*Techniques companion to [PR #226 (`2026-05-26-adaptmem-v0.7-fork-integration.md`)](https://github.com/techempower-org/mempalace/pull/226), which lays out the fork-integration plan and experiment matrix (E1–E5). This note goes one level lower: it explains **what** each v0.7 training-recipe change does, **why** it composes the way it does, and **which** specific TrainConfig values work on our P102 (familiar) and 2080 Ti (katana) VRAM budgets. Source: [`nakata-app/adaptmem`](https://github.com/nakata-app/adaptmem) at v0.7.0 (tag `e137d30`, 2026-05-23). Also a companion to [`adaptmem-orthogonal-layers.md`](adaptmem-orthogonal-layers.md), the 2026-05-15 four-layer framing.*

## Summary

v0.7.0 is a training-recipe release. The library surface (`AdaptMem.train()` / `.search()`) is unchanged; the lift comes from three contrastive-training upgrades that target low-VRAM hardware specifically, plus a default-reranker swap. The published interpretation in `TODO_ADAPTMEM_V2.md` is that v0.6's FT-300 (MiniLM, R@1=0.915 / R@5=0.995 on the 200-test split) was bench-ceiling for MiniLM on this dataset, and v0.7 is the recipe needed to get BGE-large into the trainer on a T4-class GPU and chase R@1 toward 0.98.

What landed (commit `e137d30`):

- **Multi-negative mining** (`adaptmem/miner.py`, `TrainConfig.n_negatives`, default 1). The miner now emits up to K hard negatives per (query, positive) pair instead of one. Each (positive, negative) becomes a separate `TrainingPair`, so n_negatives=3 triples the number of contrastive examples from the same labelled queries. Falls back to random non-relevant ids when the top-K mining doesn't yield enough hards.
- **Gradient accumulation** (`TrainConfig.gradient_accumulation_steps`, default 1, wired into `base.fit(accumulation_steps=...)`). Effective batch is `batch_size * accumulation_steps`. `n_steps` is now computed from the effective batch, so warmup ratios stay sensible. The graceful fallback for older sentence-transformers without `accumulation_steps` is `f2dccd6` ("fix: graceful fallback when fit() lacks accumulation_steps").
- **`CachedMultipleNegativesRankingLoss`** as an alternative loss (`TrainConfig.loss_type`, default `"mnrl"`, alternative `"cached_mnrl"`). Selected in `core.py` via:
  ```python
  if config.loss_type == "cached_mnrl":
      loss = losses.CachedMultipleNegativesRankingLoss(base, mini_batch_size=config.batch_size)
  else:
      loss = losses.MultipleNegativesRankingLoss(base)
  ```
- **Default cross-encoder upgrade** from `cross-encoder/ms-marco-MiniLM-L-12-v2` (33M params) to `BAAI/bge-reranker-v2-m3` (560M). This is a breaking default-change — saved `config.json` from older models still resolves to MS-MARCO via the explicit `cfg.get("rerank_model", ...)` fallback, but new constructions get bge-reranker. v0.7.0 in `__init__.py` reflects the breaking change.
- **`model_kwargs` passthrough** on `AdaptMem(...)` and `.load()` — lets callers pass `trust_remote_code=True` and similar SentenceTransformer kwargs. Persisted in `config.json`.
- **R@1 miss analysis** in `benchmarks/longmemeval_eval.py`. The eval JSON now carries an `r1_misses` list: per-question diagnostics (question_id, question_type, expected vs retrieved rank-1, whether hit at 5 or 10). `cmd_test` also prints a question_type histogram of misses. Direct lift for our own miss-pattern analysis if we run their harness.

Follow-up commits the same evening (`ab9ebda`, `12c7fa7`) are VRAM-management notebooks for Colab T4 and Kaggle T4, not library changes: move the bi-encoder to CPU before loading the bge-reranker-v2-m3 cross-encoder, then keep the bi-encoder on CPU through the rerank pass. Direct application to our P102 (10 GB) and 2080 Ti (11 GB) — neither will hold BGE-large (335 M) + bge-reranker-v2-m3 (560 M) simultaneously on GPU; the staged-loading recipe in `benchmarks/colab_v3_cell.py` is the working pattern.

### Why CachedMNRL matters for low-VRAM training

`MultipleNegativesRankingLoss` is in-batch contrastive: every other example in the batch is a negative for every query, so larger batches give more (and harder) negatives. On a 10–11 GB GPU with BGE-large the practical batch ceiling is `batch_size=2`, which destroys MNRL's main signal source.

`CachedMultipleNegativesRankingLoss` (Gao et al., "Scaling Deep Contrastive Learning Batch Size under Memory Limited Setup", 2021 — shipped in `sentence-transformers>=2.2`) decouples loss batch size from forward-pass batch size. It runs the forward pass in `mini_batch_size` chunks (= our VRAM-feasible 2), caches the embeddings, then computes the in-batch contrastive loss across the full batch with gradient-checkpointing tricks so the backward pass only needs the chunk-sized memory. The practical effect: you can target an "effective" contrastive batch of 32–128 on a T4 / P102 / 2080 Ti without OOM. This composes with gradient accumulation — accumulation increases the effective optimizer-update batch, CachedMNRL increases the effective contrastive-loss batch, and they are independent levers.

Multi-negative mining is the orthogonal third lever: it doesn't grow the batch, it grows the dataset by emitting K pairs per labelled positive. n_negatives=3 with 300 labelled queries roughly approximates the contrastive signal of 900 labelled queries at n_negatives=1.

## Where this lands for our fork

Our fork's encoder layer is the embedder pluggable into `mempalace.searcher` and the daemon. Today's defaults are MiniLM-L6 (palace-default) plus the BM25 + AGE-graph hybrid we ship in PR #228 / #229 / #230. The 2026-05-24 katana FT-300 reproduction (R@5=1.000 on held-out 200) confirmed adaptmem's recipe works on our hardware; what v0.7.0 unlocks is **training a larger encoder on the same hardware**.

Concretely, the directly-relevant pieces:

1. **CachedMNRL + gradient accumulation gives BGE-large on katana.** Our katana 2080 Ti (11 GB) couldn't reach a useful effective batch with vanilla MNRL on BGE-large. With `loss_type="cached_mnrl"`, `mini_batch_size=2`, `gradient_accumulation_steps=16` we get effective contrastive batch ~32, effective optimizer batch ~32 — the Kaggle T4 cell's recipe targets exactly this. Expected outcome: BGE-large FT-300 R@1 in the 0.94–0.95 range based on adaptmem's published BGE-large reference rows (vs MiniLM FT-300's 0.915). Worth measuring on our corpus before treating the number as load-bearing — chunking and prompt-style differences move these results 1–2pt in either direction, and our existing chunk-x-encoder ablation is the obvious harness.

2. **Familiar (P102, 10 GB) is the harder case.** P102 has 10 GB and no bfloat16; CachedMNRL + accumulation will still let us train BGE-large, but the staged bi-encoder→CPU / cross-encoder→GPU dance from `kaggle_v3_cell.py` becomes necessary for eval, not just training. If we want familiar to host the eval *and* the trained encoder at inference, the bi-encoder needs to stay on GPU and the cross-encoder rerank needs to live on a different host (katana, or CPU on familiar). This is a layout question, not a training question — and it argues for keeping the trained encoder behind palace-daemon and reranking on a different node only when the latency budget allows it.

3. **Multi-negative mining is independent of VRAM and the cheapest possible win.** `n_negatives=3` is a one-line config change; on the same FT-300 split we'd expect a sub-percent R@5 lift and a 1–2pt R@1 lift. The mining step adds CPU time, not GPU time, so it costs nothing on our setup. This is the candidate for "quick win" if we want one measurement before reaching for BGE-large.

4. **`BAAI/bge-reranker-v2-m3` is a candidate rerank model for our cross-encoder stage** (if/when we add one to the daemon). It's the same model the upstream MemPalace + Haiku rerank row leans on in the published numbers. Our fork doesn't currently expose a cross-encoder rerank stage — adding one is a separate scope from this memo, but the model choice is settled if we go that direction.

5. **R@1 miss analysis is a free harness improvement.** Their `r1_misses` field with question_type histogram pinpoints which categories (multi-session, temporal-reasoning, knowledge-update, single-session-{user,assistant,preference}) are eating the recall budget. We currently report aggregate R@k from `benchmarks/longmemeval_bench.py`; folding in the per-type miss breakdown costs ~20 lines and pays back every benchmark run.

## Concrete next steps, ranked

These slot underneath PR #226's experiment matrix — #226 defines the **what** (E1 bench-only swap → E2 chunk × encoder → E3 daemon swap → E4 triples-aware → E5 our-corpus FT). This section adds the **training-config layer** that runs inside E1, E3, and E5 wherever new FT models get trained on our hardware.

### 1. CachedMNRL FT-300 on katana with BGE-large *(highest impact / effort ratio)*

Rerun our FT-300 reproduction on katana 2080 Ti but with:
- `base_model="BAAI/bge-large-en-v1.5"`
- `TrainConfig(epochs=5, batch_size=2, n_negatives=3, gradient_accumulation_steps=16, loss_type="cached_mnrl", top_k_mine=15)`
- Same 300-train / 200-test split as the 2026-05-24 reproduction.

**Expected:** R@1 in the 0.94–0.95 range on the 200-test split — a 2.5–3.5pt lift over our MiniLM FT-300 R@1=0.915. R@5 likely already saturated at 0.995–1.000.

**Effort:** half a day. The benchmarks/colab_v3_cell.py recipe is portable to katana with the data paths swapped (`/content/longmemeval_s_cleaned.json` → our local copy). The model save is portable, so a successful run is also a candidate encoder for palace-daemon if we want to A/B at the daemon layer.

**Risk:** if 2080 Ti's 11 GB isn't enough headroom for BGE-large training even with CachedMNRL + accumulation (Gao et al.'s memory math assumes some flexibility on the activation side), we drop to BGE-base (110 M) and treat that as the production target instead. Worth measuring rather than predicting.

### 2. Multi-negative mining cheap-shot on MiniLM FT-300 *(highest confidence)*

Before chasing BGE-large, rerun MiniLM FT-300 with **only** `n_negatives=3` changed. Same encoder, same hardware, same hyperparameters otherwise.

**Expected:** R@1 lift of 1–2pt (0.915 → 0.92–0.93), R@5 lift of 0.3–0.5pt. This isolates the multi-negative axis from the encoder-size and loss-function axes.

**Effort:** an hour, mostly waiting for the train pass.

**Why this matters:** if multi-neg mining alone closes most of the gap, the BGE-large experiment is less urgent and we save the GPU time. If multi-neg gives <1pt lift, that's evidence the MiniLM ceiling is the real constraint and BGE-large is the right next move.

### 3. Per-question-type miss-pattern harness *(infrastructure)*

Port the `r1_misses` + per-type histogram from `benchmarks/longmemeval_eval.py` into our `benchmarks/longmemeval_bench.py`. ~20 lines plus a results-JSON schema bump.

**Expected:** zero recall lift, full diagnostic on every future run. Tells us whether our remaining misses cluster in one or two question_types (which would argue for targeted augmentation or a rerank layer) or are spread evenly (which would argue for general capacity / encoder upgrades).

**Effort:** under an hour.

### 4. P102 (familiar) staged-loading recipe for eval

If the BGE-large encoder from step 1 ships into palace-daemon, familiar needs a working eval path that doesn't OOM. The pattern from `kaggle_v3_cell.py`: encode on CPU after the first eval pass, then load reranker on GPU. We'd want this measured (encode latency on CPU vs GPU, full eval wall-clock) before committing to a layout.

**Effort:** half a day to measure both layouts and pick.

### 5. *(Speculative)* Cross-encoder rerank stage in the daemon

Out of scope for this memo, but worth flagging: adaptmem's data row "MemPalace hybrid_v4 + adaptmem FT-300" at R@1=0.916 is what our fork already targets via the hybrid stack. The next published row up — "MemPalace + Haiku rerank" at R@1=1.000 — uses a cross-encoder + LLM rerank. A pure cross-encoder rerank stage (no LLM) using bge-reranker-v2-m3 would be the local-first, no-API-required equivalent. Numbers from adaptmem's `kaggle_v3_cell.py` Eval 2/3 will tell us what to expect once the BGE-large bi-encoder lift is measured.

This is a separate PR scope, not a v0.7.0 follow-up; calling it out so it's not lost.

## Bench techniques worth folding in

Beyond the `r1_misses` harness diff (covered above), two pieces from adaptmem's `benchmarks/` that aren't in our tree:

- **`benchmarks/mempal_bench_with_ft.py` style monkey-patched encoder swap.** Reuses our own `benchmarks/longmemeval_bench.py` by replacing the embedding function at import time. This is the cleanest way to A/B a candidate FT encoder against our shipping default *through our own scorer*, with zero changes to bench code. Worth porting as `benchmarks/encoder_swap_eval.py` so we can drop in any SentenceTransformer-compatible model and get matched-protocol numbers without rewriting eval logic.
- **`benchmarks/bootstrap_paired_mrr.py`** for paired-bootstrap confidence intervals on cross-encoder comparisons. We currently report point estimates from `longmemeval_bench.py`; paired-bootstrap MRR with 1000 resamples is a 20-line addition that makes our cross-encoder claims actually statistically defensible. Especially relevant if we end up A/B'ing BGE-large FT-300 against MiniLM FT-300 with overlapping confidence intervals.

Neither is required for steps 1–3 above; both are inexpensive quality-of-life upgrades for the benchmarks/ directory.

## Open questions

- Does CachedMNRL + accumulation on a 2080 Ti actually reach the BGE-large numbers adaptmem reports on T4? Step 1 above answers this with measurement, not prediction.
- Is the FT-300 split the right size for our purposes? adaptmem's data shows recall scales monotonically with train-set size up to 300 queries on this benchmark; we haven't yet measured whether 500 queries with BGE-large would lift further or saturate.
- How does an FT'd encoder compose with our AGE graph-expansion path? Our hybrid_v4 lift partially comes from graph expansion on top of the dense recall — if FT'ing the encoder eats the same surface, the lifts may not compose as cleanly as they did with MiniLM. Worth measuring after step 1.

## Provenance

- Source repo: <https://github.com/nakata-app/adaptmem> (cloned to `/tmp/adaptmem-review` for this review, not committed under `~/Projects/`).
- v0.7.0 commit: `e137d30` (2026-05-23).
- VRAM-management follow-ups: `ab9ebda` (Colab T4), `12c7fa7` (Kaggle T4).
- Reference notebooks for the v0.7 recipe: `benchmarks/colab_v3_cell.py`, `benchmarks/kaggle_v3_cell.py`.
- Reference results table: adaptmem `README.md` and `CHANGELOG.md`.
- Prior fork context: [`adaptmem-orthogonal-layers.md`](adaptmem-orthogonal-layers.md), 2026-05-24 katana reproduction summary in `bench-logs/full-*-500q.log`.
- Integration plan this companions: [PR #226](https://github.com/techempower-org/mempalace/pull/226) (`2026-05-26-adaptmem-v0.7-fork-integration.md`).
